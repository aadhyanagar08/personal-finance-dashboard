from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Forecast, Transaction
from app.db.session import get_db
from app.ml.forecast import SpendingForecaster

router = APIRouter(prefix="/forecasts", tags=["forecasts"])

# Module-level singleton shared between refresh trigger and forecast reads.
_forecaster = SpendingForecaster()


def get_forecaster() -> SpendingForecaster:
    return _forecaster


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ForecastPoint(BaseModel):
    forecast_date: date
    predicted_amount: Decimal
    confidence_lower: Decimal | None
    confidence_upper: Decimal | None

    model_config = {"from_attributes": True}


class CategoryForecast(BaseModel):
    category: str
    periods: int
    points: list[ForecastPoint]


class CategorySummary(BaseModel):
    category: str
    projected_30d_spend: Decimal


class ForecastSummary(BaseModel):
    date_from: date
    date_to: date
    categories: list[CategorySummary]


class RefreshResponse(BaseModel):
    status: str
    categories_queued: int = Field(..., description="Number of categories being retrained")


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------


async def _retrain_and_persist(forecaster: SpendingForecaster) -> None:
    """Pull all historical transactions, retrain Prophet per category, persist."""
    import asyncio
    import pandas as pd
    from sqlalchemy import text
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Transaction.date, Transaction.amount, Transaction.category)
        )
        rows = result.all()

    if not rows:
        return

    df = pd.DataFrame(rows, columns=["date", "amount", "category"])
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, forecaster.train_by_category, df)
    await forecaster.persist_all_forecasts()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/summary",
    response_model=ForecastSummary,
    summary="30-day forecast summary",
    description="Next 30-day projected spend aggregated by category, sourced from the Forecast table.",
)
async def forecast_summary(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ForecastSummary:
    today = date.today()
    from datetime import timedelta
    end = today + timedelta(days=30)

    result = await db.execute(
        select(
            Forecast.category,
            func.sum(Forecast.predicted_amount).label("total"),
        )
        .where(and_(Forecast.forecast_date >= today, Forecast.forecast_date <= end))
        .group_by(Forecast.category)
        .order_by(func.sum(Forecast.predicted_amount).desc())
    )
    rows = result.all()

    return ForecastSummary(
        date_from=today,
        date_to=end,
        categories=[
            CategorySummary(category=r.category, projected_30d_spend=Decimal(str(r.total)))
            for r in rows
        ],
    )


@router.get(
    "/{category}",
    response_model=CategoryForecast,
    summary="90-day category forecast",
    description="Returns the next 90 days of predicted spend with confidence bands for the given category.",
)
async def category_forecast(
    category: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    periods: Annotated[int, Query(ge=1, le=365)] = 90,
) -> CategoryForecast:
    today = date.today()
    from datetime import timedelta
    end = today + timedelta(days=periods - 1)

    result = await db.execute(
        select(Forecast)
        .where(
            and_(
                Forecast.category == category,
                Forecast.forecast_date >= today,
                Forecast.forecast_date <= end,
            )
        )
        .order_by(Forecast.forecast_date)
        .limit(periods)
    )
    points = result.scalars().all()

    if not points:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No forecast data found for category '{category}'. Run POST /forecasts/refresh first.",
        )

    return CategoryForecast(
        category=category,
        periods=len(points),
        points=[ForecastPoint.model_validate(p) for p in points],
    )


@router.post(
    "/refresh",
    response_model=RefreshResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Refresh forecasts",
    description="Triggers background retraining of Prophet models for all categories, then persists fresh forecasts.",
)
async def refresh_forecasts(
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
    forecaster: Annotated[SpendingForecaster, Depends(get_forecaster)],
) -> RefreshResponse:
    # Count distinct categories to report back immediately.
    result = await db.execute(
        select(func.count(Transaction.category.distinct()))
    )
    category_count = result.scalar_one() or 0

    background_tasks.add_task(_retrain_and_persist, forecaster)

    return RefreshResponse(status="queued", categories_queued=category_count)
