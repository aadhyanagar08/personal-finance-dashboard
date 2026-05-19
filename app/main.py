from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging import configure_logging, get_logger

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
