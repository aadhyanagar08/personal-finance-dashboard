from __future__ import annotations

import asyncio
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated

import yfinance as yf
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Asset, PriceHistory
from app.db.session import get_db

logger = get_logger(__name__)

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class Holding(BaseModel):
    ticker: str
    name: str | None
    asset_type: str | None
    current_price: float | None
    daily_change: float | None = Field(None, description="Absolute price change vs previous close")
    daily_change_pct: float | None = Field(None, description="Percentage change vs previous close")


class PortfolioOut(BaseModel):
    holdings: list[Holding]
    total_assets: int


class PricePoint(BaseModel):
    date: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    volume: int | None


class AssetHistory(BaseModel):
    ticker: str
    history: list[PricePoint]


class PortfolioHistory(BaseModel):
    assets: list[AssetHistory]


class PortfolioMetrics(BaseModel):
    total_value: float | None = Field(None, description="Sum of latest close prices across all assets")
    daily_change: float | None = Field(None, description="Total daily price change across holdings")
    daily_change_pct: float | None
    return_30d: float | None = Field(None, description="Percentage price change over the last 30 days")
    volatility_30d: float | None = Field(None, description="Annualised 30-day return volatility")
    sharpe_ratio: float | None = Field(None, description="Annualised Sharpe ratio (risk-free rate = 0)")


# ---------------------------------------------------------------------------
# yfinance helpers (run in thread pool to avoid blocking event loop)
# ---------------------------------------------------------------------------


def _fetch_live_prices(tickers: list[str]) -> dict[str, dict]:
    """Return {ticker: {price, prev_close}} for each ticker via yfinance."""
    if not tickers:
        return {}
    data: dict[str, dict] = {}
    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).fast_info
            data[ticker] = {
                "price": getattr(info, "last_price", None),
                "prev_close": getattr(info, "previous_close", None),
            }
        except Exception as exc:
            logger.warning("yfinance fetch failed", ticker=ticker, error=str(exc))
            data[ticker] = {"price": None, "prev_close": None}
    return data


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=PortfolioOut,
    summary="Current portfolio holdings",
    description="Returns all tracked assets with live prices fetched from yfinance.",
)
async def get_portfolio(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PortfolioOut:
    result = await db.execute(select(Asset).order_by(Asset.ticker))
    assets = result.scalars().all()

    if not assets:
        return PortfolioOut(holdings=[], total_assets=0)

    tickers = [a.ticker for a in assets]
    loop = asyncio.get_running_loop()
    prices = await loop.run_in_executor(None, _fetch_live_prices, tickers)

    holdings = []
    for asset in assets:
        p = prices.get(asset.ticker, {})
        price = p.get("price")
        prev = p.get("prev_close")
        daily_change = (price - prev) if price is not None and prev is not None else None
        daily_change_pct = (daily_change / prev * 100) if daily_change is not None and prev else None
        holdings.append(
            Holding(
                ticker=asset.ticker,
                name=asset.name,
                asset_type=asset.asset_type,
                current_price=price,
                daily_change=daily_change,
                daily_change_pct=daily_change_pct,
            )
        )

    return PortfolioOut(holdings=holdings, total_assets=len(holdings))


@router.get(
    "/history",
    response_model=PortfolioHistory,
    summary="Portfolio price history",
    description="Returns OHLCV price history for all assets, useful for charting.",
)
async def portfolio_history(
    db: Annotated[AsyncSession, Depends(get_db)],
    days: Annotated[int, Query(ge=1, le=3650, description="Number of trailing days")] = 90,
) -> PortfolioHistory:
    cutoff = date.today() - timedelta(days=days)

    assets_result = await db.execute(select(Asset).order_by(Asset.ticker))
    assets = assets_result.scalars().all()

    if not assets:
        return PortfolioHistory(assets=[])

    asset_map = {a.id: a.ticker for a in assets}

    history_result = await db.execute(
        select(PriceHistory)
        .where(PriceHistory.date >= cutoff)
        .order_by(PriceHistory.asset_id, PriceHistory.date)
    )
    rows = history_result.scalars().all()

    # Group by asset
    grouped: dict[str, list[PricePoint]] = {a.ticker: [] for a in assets}
    for row in rows:
        ticker = asset_map.get(row.asset_id)
        if ticker:
            grouped[ticker].append(
                PricePoint(
                    date=row.date,
                    open=row.open,
                    high=row.high,
                    low=row.low,
                    close=row.close,
                    volume=row.volume,
                )
            )

    return PortfolioHistory(
        assets=[
            AssetHistory(ticker=ticker, history=points)
            for ticker, points in grouped.items()
        ]
    )


@router.get(
    "/metrics",
    response_model=PortfolioMetrics,
    summary="Portfolio metrics",
    description=(
        "Returns aggregate portfolio metrics: total value, daily change, "
        "30-day return, annualised volatility, and Sharpe ratio computed from stored price history."
    ),
)
async def portfolio_metrics(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PortfolioMetrics:
    import math

    cutoff_30d = date.today() - timedelta(days=30)
    cutoff_31d = date.today() - timedelta(days=31)

    assets_result = await db.execute(select(Asset))
    assets = assets_result.scalars().all()

    if not assets:
        return PortfolioMetrics()

    asset_ids = [a.id for a in assets]

    # Fetch 31 days of history so we can compute 30-day returns
    history_result = await db.execute(
        select(PriceHistory)
        .where(
            PriceHistory.asset_id.in_(asset_ids),
            PriceHistory.date >= cutoff_31d,
        )
        .order_by(PriceHistory.asset_id, PriceHistory.date)
    )
    rows = history_result.scalars().all()

    if not rows:
        return PortfolioMetrics()

    # Group closes by asset
    by_asset: dict[int, list[tuple[date, float]]] = {}
    for row in rows:
        by_asset.setdefault(row.asset_id, []).append((row.date, float(row.close)))

    total_value = 0.0
    total_prev_close = 0.0
    returns_30d: list[float] = []
    daily_returns_all: list[float] = []

    for asset_id, series in by_asset.items():
        series.sort(key=lambda x: x[0])
        if len(series) < 2:
            continue

        latest_close = series[-1][1]
        prev_close = series[-2][1]
        total_value += latest_close
        total_prev_close += prev_close

        # 30-day return: compare latest with the oldest point in window
        oldest_in_window = next(
            (close for dt, close in series if dt >= cutoff_30d), None
        )
        if oldest_in_window and oldest_in_window > 0:
            returns_30d.append((latest_close - oldest_in_window) / oldest_in_window * 100)

        # Daily log returns for volatility / Sharpe
        closes = [c for _, c in series]
        for i in range(1, len(closes)):
            if closes[i - 1] > 0:
                daily_returns_all.append(math.log(closes[i] / closes[i - 1]))

    daily_change = total_value - total_prev_close if total_prev_close > 0 else None
    daily_change_pct = (daily_change / total_prev_close * 100) if total_prev_close > 0 else None
    return_30d = (sum(returns_30d) / len(returns_30d)) if returns_30d else None

    volatility: float | None = None
    sharpe: float | None = None
    if len(daily_returns_all) > 1:
        import statistics
        std = statistics.stdev(daily_returns_all)
        mean = statistics.mean(daily_returns_all)
        volatility = std * math.sqrt(252) * 100  # annualised %
        sharpe = (mean * 252) / (std * math.sqrt(252)) if std > 0 else None

    return PortfolioMetrics(
        total_value=round(total_value, 2) if total_value else None,
        daily_change=round(daily_change, 2) if daily_change is not None else None,
        daily_change_pct=round(daily_change_pct, 4) if daily_change_pct is not None else None,
        return_30d=round(return_30d, 4) if return_30d is not None else None,
        volatility_30d=round(volatility, 4) if volatility is not None else None,
        sharpe_ratio=round(sharpe, 4) if sharpe is not None else None,
    )
