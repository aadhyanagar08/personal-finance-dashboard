from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import configure_logging, get_logger
from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.forecasts import router as forecasts_router
from app.api.v1.endpoints.portfolio import router as portfolio_router
from app.api.v1.endpoints.transactions import router as transactions_router
from app.ml.categorizer import router as ml_router

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    configure_logging()
    logger.info(
        "starting up",
        app=settings.APP_NAME,
        version=settings.APP_VERSION,
        environment=settings.ENVIRONMENT,
    )

    from app.pipelines.scheduler import get_scheduler

    scheduler = get_scheduler()
    scheduler.start()
    logger.info("scheduler started", jobs=len(scheduler.get_jobs()))

    yield

    scheduler.shutdown(wait=False)
    from app.db.session import engine

    await engine.dispose()
    logger.info("shutting down", app=settings.APP_NAME)


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
    lifespan=lifespan,
)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api/v1")
app.include_router(ml_router, prefix="/api/v1")
app.include_router(transactions_router, prefix="/api/v1")
app.include_router(forecasts_router, prefix="/api/v1")
app.include_router(portfolio_router, prefix="/api/v1")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health", tags=["ops"])
async def health() -> dict:
    from sqlalchemy import text

    from app.db.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        db: dict = {"status": "ok"}
    except Exception as exc:
        db = {"status": "error", "detail": str(exc)}

    return {
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "db": db,
        "status": "ok" if db["status"] == "ok" else "degraded",
    }
