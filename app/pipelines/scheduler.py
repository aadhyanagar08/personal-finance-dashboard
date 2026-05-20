import asyncio
import time
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.logging import get_logger

logger = get_logger(__name__)

# Default tickers to refresh daily. Override via settings if needed.
_DEFAULT_TICKERS = ["SPY", "QQQ", "AAPL", "MSFT", "GOOGL", "AMZN", "BRK-B"]

_scheduler: Optional[AsyncIOScheduler] = None


def get_scheduler() -> AsyncIOScheduler:
    """Return the singleton APScheduler instance, creating it on first call."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    _scheduler = AsyncIOScheduler(timezone="UTC")

    _scheduler.add_job(
        refresh_market_data,
        trigger=CronTrigger(hour=6, minute=0, timezone="UTC"),
        id="refresh_market_data",
        name="Refresh market OHLCV data",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3_600,
    )
    _scheduler.add_job(
        run_ml_pipeline,
        trigger=CronTrigger(hour=7, minute=0, timezone="UTC"),
        id="run_ml_pipeline",
        name="Run ML forecasting pipeline",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3_600,
    )

    return _scheduler


# ---------------------------------------------------------------------------
# Job implementations
# ---------------------------------------------------------------------------


async def refresh_market_data() -> None:
    """Pull fresh OHLCV data for all tracked tickers and upsert to DB."""
    from app.core.config import settings
    from app.pipelines.ingest import fetch_market_data

    log = logger.bind(job="refresh_market_data")
    start = time.monotonic()
    log.info("job started")

    try:
        tickers: list[str] = getattr(settings, "TRACKED_TICKERS", _DEFAULT_TICKERS)
        results = await fetch_market_data(tickers=tickers)
        elapsed = round(time.monotonic() - start, 3)
        log.info(
            "job completed",
            elapsed_s=elapsed,
            tickers_updated=len(results),
            total_rows=sum(results.values()),
        )
    except Exception:
        elapsed = round(time.monotonic() - start, 3)
        log.exception("job failed", elapsed_s=elapsed)


async def run_ml_pipeline() -> None:
    """Run forecasting and anomaly-detection models over latest transaction data."""
    log = logger.bind(job="run_ml_pipeline")
    start = time.monotonic()
    log.info("job started")

    try:
        # Placeholder — replaced once app/ml/ modules are implemented.
        await asyncio.sleep(0)
        elapsed = round(time.monotonic() - start, 3)
        log.info("job completed", elapsed_s=elapsed)
    except Exception:
        elapsed = round(time.monotonic() - start, 3)
        log.exception("job failed", elapsed_s=elapsed)
