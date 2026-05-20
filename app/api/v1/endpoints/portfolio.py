"""
Portfolio endpoints.

Metrics are computed from stored price_history rows rather than live yfinance
calls so the calculations are deterministic and fast.  Live prices (for the
holdings list) are still fetched via yfinance in a thread-pool executor.

Sharpe ratio uses a 4.5 % annualised risk-free rate (approximate Bank of
Canada overnight target as of 2026).
"""
from __future__ import annotations

import asyncio
import math
import statistics
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Optional

import yfinance as yf
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.limiter import limiter
from app.core.logging import get_logger
from app.core.security import verify_token
from app.db.models import Asset, PriceHistory
from app.db.session import get_db

logger = get_logger(__name__)

RISK_FREE_ANNUAL = 0.045          # 4.5 % p.a.
TRADING_DAYS = 252

router = APIRouter(
    prefix="/portfolio",
    tags=["portfolio"],
    dependencies=[Depends(verify_token)],
)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class Holding(BaseModel):
    ticker: str
    name: Optional[str]
    exchange: Optional[str]
    asset_type: Optional[str]
    quantity: Optional[float]
    current_price: Optional[float]
    market_price_currency: Optional[str]
    daily_change: Optional[float] = Field(None, description="Absolute price change")
    daily_change_pct: Optional[float] = Field(None, description="Percentage change")
    book_value_cad: Optional[float]
    market_value_cad: Optional[float]
    unrealized_return_cad: Optional[float]
    unrealized_return_pct: Optional[float]


class PortfolioOut(BaseModel):
    holdings: list[Holding]
    total_assets: int
    total_market_value_cad: Optional[float]
    total_book_value_cad: Optional[float]
    total_unrealized_cad: Optional[float]
    total_unrealized_pct: Optional[float]


class PricePoint(BaseModel):
    date: date
    open: Optional[Decimal]
    high: Optional[Decimal]
    low: Optional[Decimal]
    close: Decimal
    volume: Optional[int]


class AssetHistory(BaseModel):
    ticker: str
    history: list[PricePoint]


class PortfolioHistory(BaseModel):
    assets: list[AssetHistory]


class PortfolioMetrics(BaseModel):
    total_value_cad: Optional[float] = Field(None, description="Total portfolio market value in CAD")
    total_book_value_cad: Optional[float]
    total_unrealized_cad: Optional[float]
    total_unrealized_pct: Optional[float]
    daily_change: Optional[float]
    daily_change_pct: Optional[float]
    return_30d: Optional[float] = Field(None, description="Value-weighted 30-day return (%)")
    return_90d: Optional[float] = Field(None, description="Value-weighted 90-day return (%)")
    return_365d: Optional[float] = Field(None, description="Value-weighted 365-day return (%)")
    volatility_30d: Optional[float] = Field(None, description="Annualised 30-day volatility (%)")
    sharpe_ratio: Optional[float] = Field(None, description="Annualised Sharpe (4.5% risk-free)")
    total_value: Optional[float] = None
    daily_change_pct_alias: Optional[float] = Field(None, alias="daily_change_pct_v2", exclude=True)


class BenchmarkPoint(BaseModel):
    date: date
    portfolio_value: float
    benchmark_value: float


class BenchmarkComparison(BaseModel):
    days: int
    benchmark_ticker: str
    portfolio_return_pct: Optional[float]
    benchmark_return_pct: Optional[float]
    relative_return_pct: Optional[float]
    series: list[BenchmarkPoint]


class CorrelationEntry(BaseModel):
    ticker: str
    name: Optional[str]
    correlation: Optional[float]


class PortfolioImpact(BaseModel):
    current_sharpe: Optional[float]
    pro_forma_sharpe: Optional[float]
    current_volatility_pct: Optional[float]
    pro_forma_volatility_pct: Optional[float]
    avg_correlation: Optional[float]
    sharpe_delta: Optional[float]
    volatility_delta_pct: Optional[float]


class PriceClose(BaseModel):
    date: date
    close: float


class AnalysisResult(BaseModel):
    ticker: str
    name: Optional[str]
    price_history: list[PriceClose]
    correlations: list[CorrelationEntry]
    portfolio_impact: PortfolioImpact
    recommendation: str
    recommendation_detail: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fetch_live_prices(tickers: list[str]) -> dict[str, dict]:
    """Fetch last_price + previous_close from yfinance for each ticker."""
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


async def _load_history(
    db: AsyncSession,
    asset_ids: list[int],
    cutoff: date,
) -> dict[int, list[tuple[date, float]]]:
    """Return {asset_id: [(date, close), ...]} sorted ascending."""
    result = await db.execute(
        select(PriceHistory.asset_id, PriceHistory.date, PriceHistory.close)
        .where(PriceHistory.asset_id.in_(asset_ids), PriceHistory.date >= cutoff)
        .order_by(PriceHistory.date)
    )
    by_asset: dict[int, list[tuple[date, float]]] = {}
    for asset_id, dt, close in result.all():
        by_asset.setdefault(asset_id, []).append((dt, float(close)))
    return by_asset


def _period_return(series: list[tuple[date, float]], cutoff: date) -> Optional[float]:
    """Percentage return from first observation on or after cutoff to last."""
    in_window = [(d, c) for d, c in series if d >= cutoff]
    if len(in_window) < 2:
        return None
    start = in_window[0][1]
    end = in_window[-1][1]
    if start <= 0:
        return None
    return (end - start) / start * 100


def _portfolio_daily_returns(
    assets: list[Asset],
    by_asset: dict[int, list[tuple[date, float]]],
    usdcad: float,
) -> list[float]:
    """
    Compute value-weighted portfolio daily log-returns.
    Only dates present for ALL holdings are included (inner join on dates).
    """
    by_date: dict[date, dict[int, float]] = {}
    for asset in assets:
        fx = usdcad if (asset.market_price_currency or "CAD").upper() == "USD" else 1.0
        for dt, close in by_asset.get(asset.id, []):
            by_date.setdefault(dt, {})[asset.id] = close * fx

    threshold = max(1, len(assets) // 2)
    common_dates = sorted(d for d, m in by_date.items() if len(m) >= threshold)

    if len(common_dates) < 2:
        return []

    qty: dict[int, float] = {a.id: float(a.quantity or 0) for a in assets}

    daily_returns: list[float] = []
    for i in range(1, len(common_dates)):
        d_prev, d_curr = common_dates[i - 1], common_dates[i]
        prev_map, curr_map = by_date[d_prev], by_date[d_curr]

        port_prev = sum(qty.get(aid, 0) * p for aid, p in prev_map.items())
        port_curr = sum(qty.get(aid, 0) * p for aid, p in curr_map.items())

        if port_prev > 0 and port_curr > 0:
            daily_returns.append(math.log(port_curr / port_prev))

    return daily_returns


def _fetch_yf_history(ticker: str, days: int) -> list[tuple[date, float]]:
    """Fetch price history from yfinance. Returns list of (date, close) sorted ascending."""
    try:
        period = "1y" if days <= 400 else "2y"
        hist = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=True)
        if hist.empty:
            return []
        result: list[tuple[date, float]] = []
        for ts, row in hist.iterrows():
            c = float(row["Close"])
            if not math.isnan(c):
                result.append((ts.date(), c))
        return sorted(result)
    except Exception as exc:
        logger.warning("yfinance history fetch failed", ticker=ticker, error=str(exc))
        return []


def _fetch_ticker_info(ticker: str) -> dict:
    try:
        info = yf.Ticker(ticker).info
        return {"name": info.get("longName") or info.get("shortName")}
    except Exception:
        return {}


def _log_returns_map(series: list[tuple[date, float]]) -> dict[date, float]:
    """Return {date: log_return} for consecutive trading-day pairs."""
    result: dict[date, float] = {}
    sorted_series = sorted(series)
    for i in range(1, len(sorted_series)):
        d_prev, p_prev = sorted_series[i - 1]
        d_curr, p_curr = sorted_series[i]
        if p_prev > 0 and p_curr > 0:
            result[d_curr] = math.log(p_curr / p_prev)
    return result


def _pearson_corr(x: list[float], y: list[float]) -> Optional[float]:
    """Sample Pearson correlation coefficient."""
    n = len(x)
    if n < 3 or n != len(y):
        return None
    mx, my = statistics.mean(x), statistics.mean(y)
    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / (n - 1)
    sx = statistics.stdev(x)
    sy = statistics.stdev(y)
    if sx == 0 or sy == 0:
        return None
    return cov / (sx * sy)


def _build_recommendation(
    ticker: str,
    correlations: list[CorrelationEntry],
    avg_corr: Optional[float],
    sharpe_delta: Optional[float],
    vol_delta: Optional[float],
) -> tuple[str, str]:
    if avg_corr is None:
        return "Insufficient data", "Not enough overlapping price history to generate a recommendation."

    valid = [(c.ticker, c.correlation) for c in correlations if c.correlation is not None]
    most_corr = max(valid, key=lambda x: x[1]) if valid else None
    avg_str = f"{avg_corr:.2f}"

    if avg_corr < 0.3:
        rec = "low correlation, improves diversification"
        parts = [f"{ticker} has low average correlation ({avg_str}) with your existing holdings"]
        if vol_delta is not None and vol_delta < 0:
            parts.append(f"and would reduce portfolio volatility by {abs(vol_delta):.2f}%")
        if sharpe_delta is not None and sharpe_delta > 0:
            parts.append(f"while improving the Sharpe ratio by {sharpe_delta:.2f}")
        detail = " ".join(parts) + "."
    elif avg_corr < 0.6:
        if sharpe_delta is not None and sharpe_delta > 0:
            rec = "moderate correlation, slight diversification benefit"
            detail = (
                f"{ticker} has moderate average correlation ({avg_str}) with your portfolio. "
                f"Adding at 5% weight would improve Sharpe by {sharpe_delta:.2f}."
            )
        else:
            rec = "moderate correlation, limited diversification benefit"
            detail = (
                f"{ticker} has moderate average correlation ({avg_str}) with your holdings "
                f"and may not significantly improve portfolio diversification."
            )
    else:
        if most_corr:
            rec = f"high correlation with existing holdings ({most_corr[0]}), adds concentration risk"
            detail = (
                f"{ticker} has high average correlation ({avg_str}) with your portfolio, "
                f"most strongly correlated with {most_corr[0]} ({most_corr[1]:.2f}). "
                f"Adding it would increase concentration risk without meaningful diversification."
            )
        else:
            rec = "high correlation, adds concentration risk"
            detail = (
                f"{ticker} has high average correlation ({avg_str}) with your portfolio, "
                f"adding concentration risk without meaningful diversification benefit."
            )

    return rec, detail


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=PortfolioOut, summary="Portfolio holdings with live prices")
@limiter.limit("1000/minute")
async def get_portfolio(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PortfolioOut:
    result = await db.execute(
        select(Asset).where(Asset.quantity.is_not(None)).order_by(Asset.ticker)
    )
    assets = result.scalars().all()

    if not assets:
        return PortfolioOut(holdings=[], total_assets=0,
                            total_market_value_cad=None, total_book_value_cad=None,
                            total_unrealized_cad=None, total_unrealized_pct=None)

    yf_symbols = [a.yf_symbol or a.ticker for a in assets]
    loop = asyncio.get_running_loop()
    prices = await loop.run_in_executor(None, _fetch_live_prices, yf_symbols)

    holdings: list[Holding] = []
    total_market = 0.0
    total_book = 0.0
    total_unrealized = 0.0

    for asset in assets:
        yf_sym = asset.yf_symbol or asset.ticker
        p = prices.get(yf_sym, {})
        live_price = p.get("price")
        prev_close = p.get("prev_close")

        daily_change = (live_price - prev_close) if (live_price is not None and prev_close is not None) else None
        daily_change_pct = (daily_change / prev_close * 100) if (daily_change is not None and prev_close) else None

        mv = float(asset.market_value_cad) if asset.market_value_cad is not None else None
        bv = float(asset.book_value_cad) if asset.book_value_cad is not None else None
        ur = float(asset.unrealized_return_cad) if asset.unrealized_return_cad is not None else None
        ur_pct = (ur / bv * 100) if (ur is not None and bv and bv != 0) else None

        if mv is not None:
            total_market += mv
        if bv is not None:
            total_book += bv
        if ur is not None:
            total_unrealized += ur

        holdings.append(Holding(
            ticker=asset.ticker,
            name=asset.name,
            exchange=asset.exchange,
            asset_type=asset.asset_type,
            quantity=float(asset.quantity) if asset.quantity is not None else None,
            current_price=live_price,
            market_price_currency=asset.market_price_currency or "CAD",
            daily_change=daily_change,
            daily_change_pct=daily_change_pct,
            book_value_cad=bv,
            market_value_cad=mv,
            unrealized_return_cad=ur,
            unrealized_return_pct=ur_pct,
        ))

    total_ur_pct = (total_unrealized / total_book * 100) if total_book else None

    return PortfolioOut(
        holdings=holdings,
        total_assets=len(holdings),
        total_market_value_cad=round(total_market, 2) if total_market else None,
        total_book_value_cad=round(total_book, 2) if total_book else None,
        total_unrealized_cad=round(total_unrealized, 2),
        total_unrealized_pct=round(total_ur_pct, 2) if total_ur_pct is not None else None,
    )


@router.get("/history", response_model=PortfolioHistory, summary="OHLCV price history for charting")
@limiter.limit("1000/minute")
async def portfolio_history(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    days: Annotated[int, Query(ge=1, le=3650, description="Trailing calendar days")] = 90,
) -> PortfolioHistory:
    cutoff = date.today() - timedelta(days=days)

    assets_result = await db.execute(
        select(Asset).where(Asset.quantity.is_not(None)).order_by(Asset.ticker)
    )
    assets = assets_result.scalars().all()

    if not assets:
        return PortfolioHistory(assets=[])

    asset_map = {a.id: a.ticker for a in assets}

    history_result = await db.execute(
        select(PriceHistory)
        .where(PriceHistory.asset_id.in_(list(asset_map)), PriceHistory.date >= cutoff)
        .order_by(PriceHistory.asset_id, PriceHistory.date)
    )
    rows = history_result.scalars().all()

    grouped: dict[str, list[PricePoint]] = {a.ticker: [] for a in assets}
    for row in rows:
        ticker = asset_map.get(row.asset_id)
        if ticker:
            grouped[ticker].append(
                PricePoint(date=row.date, open=row.open, high=row.high,
                           low=row.low, close=row.close, volume=row.volume)
            )

    return PortfolioHistory(
        assets=[AssetHistory(ticker=t, history=pts) for t, pts in grouped.items() if pts]
    )


@router.get("/metrics", response_model=PortfolioMetrics, summary="Portfolio performance metrics")
@limiter.limit("1000/minute")
async def portfolio_metrics(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PortfolioMetrics:
    assets_result = await db.execute(
        select(Asset).where(Asset.quantity.is_not(None))
    )
    assets = assets_result.scalars().all()

    if not assets:
        return PortfolioMetrics()

    total_market = sum(float(a.market_value_cad or 0) for a in assets)
    total_book = sum(float(a.book_value_cad or 0) for a in assets)
    total_ur = sum(float(a.unrealized_return_cad or 0) for a in assets)
    total_ur_pct = (total_ur / total_book * 100) if total_book else None

    cutoff_365 = date.today() - timedelta(days=366)
    cutoff_30 = date.today() - timedelta(days=31)
    cutoff_90 = date.today() - timedelta(days=91)

    asset_ids = [a.id for a in assets]

    fx_result = await db.execute(select(Asset.id).where(Asset.ticker == "USDCAD"))
    fx_row = fx_result.scalar_one_or_none()
    query_ids = asset_ids + ([fx_row] if fx_row else [])

    by_asset = await _load_history(db, query_ids, cutoff_365)

    usdcad = 1.3753
    if fx_row and fx_row in by_asset and by_asset[fx_row]:
        usdcad = by_asset[fx_row][-1][1]

    portfolio_by_asset = {aid: v for aid, v in by_asset.items() if aid != fx_row}

    total_value_today = 0.0
    total_value_prev = 0.0
    for a in assets:
        series = portfolio_by_asset.get(a.id, [])
        if len(series) >= 2:
            fx = usdcad if (a.market_price_currency or "CAD").upper() == "USD" else 1.0
            qty = float(a.quantity or 0)
            total_value_today += series[-1][1] * fx * qty
            total_value_prev += series[-2][1] * fx * qty

    daily_change = (total_value_today - total_value_prev) if total_value_prev > 0 else None
    daily_change_pct = (daily_change / total_value_prev * 100) if total_value_prev > 0 else None

    def period_return_pct(cutoff: date) -> Optional[float]:
        total_w_start = 0.0
        total_w_end = 0.0
        count = 0
        for a in assets:
            series = [(d, c) for d, c in portfolio_by_asset.get(a.id, []) if d >= cutoff]
            if len(series) < 2:
                continue
            fx = usdcad if (a.market_price_currency or "CAD").upper() == "USD" else 1.0
            qty = float(a.quantity or 0)
            total_w_start += series[0][1] * fx * qty
            total_w_end += series[-1][1] * fx * qty
            count += 1
        if count == 0 or total_w_start <= 0:
            return None
        return (total_w_end - total_w_start) / total_w_start * 100

    return_30d = period_return_pct(cutoff_30)
    return_90d = period_return_pct(cutoff_90)
    return_365d = period_return_pct(cutoff_365)

    daily_rets = _portfolio_daily_returns(assets, portfolio_by_asset, usdcad)

    volatility_30d: Optional[float] = None
    sharpe: Optional[float] = None

    if len(daily_rets) > 10:
        std = statistics.stdev(daily_rets)
        mean = statistics.mean(daily_rets)
        if std > 0:
            volatility_30d = std * math.sqrt(TRADING_DAYS) * 100
            ann_return = mean * TRADING_DAYS
            sharpe = (ann_return - RISK_FREE_ANNUAL) / (std * math.sqrt(TRADING_DAYS))

    return PortfolioMetrics(
        total_value_cad=round(total_market, 2) if total_market else None,
        total_value=round(total_market, 2) if total_market else None,
        total_book_value_cad=round(total_book, 2) if total_book else None,
        total_unrealized_cad=round(total_ur, 2),
        total_unrealized_pct=round(total_ur_pct, 2) if total_ur_pct is not None else None,
        daily_change=round(daily_change, 2) if daily_change is not None else None,
        daily_change_pct=round(daily_change_pct, 4) if daily_change_pct is not None else None,
        return_30d=round(return_30d, 4) if return_30d is not None else None,
        return_90d=round(return_90d, 4) if return_90d is not None else None,
        return_365d=round(return_365d, 4) if return_365d is not None else None,
        volatility_30d=round(volatility_30d, 4) if volatility_30d is not None else None,
        sharpe_ratio=round(sharpe, 4) if sharpe is not None else None,
    )


@router.get("/benchmark", response_model=BenchmarkComparison, summary="Portfolio vs configurable benchmark")
@limiter.limit("1000/minute")
async def portfolio_benchmark(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    days: Annotated[int, Query(ge=7, le=3650, description="Look-back window in calendar days")] = 90,
    ticker: Annotated[str, Query(description="Benchmark ticker (e.g. XIC, SPY, QQQ, ZAG.TO)")] = "XIC",
) -> BenchmarkComparison:
    cutoff = date.today() - timedelta(days=days)
    bm_ticker = ticker.strip().upper()

    assets_result = await db.execute(
        select(Asset).where(Asset.quantity.is_not(None))
    )
    assets = assets_result.scalars().all()

    fx_result = await db.execute(select(Asset).where(Asset.ticker == "USDCAD"))
    fx_asset = fx_result.scalar_one_or_none()

    # Try DB first (match by ticker column or yf_symbol)
    bm_result = await db.execute(
        select(Asset)
        .where(or_(Asset.ticker == bm_ticker, Asset.yf_symbol == bm_ticker))
        .limit(1)
    )
    bm_asset = bm_result.scalar_one_or_none()

    all_ids = [a.id for a in assets]
    if fx_asset:
        all_ids.append(fx_asset.id)
    if bm_asset:
        all_ids.append(bm_asset.id)

    by_asset = await _load_history(db, list(set(all_ids)), cutoff)

    usdcad = 1.3753
    if fx_asset and fx_asset.id in by_asset and by_asset[fx_asset.id]:
        usdcad = by_asset[fx_asset.id][-1][1]

    exclude_ids = {
        bm_asset.id if bm_asset else -1,
        fx_asset.id if fx_asset else -1,
    }
    portfolio_by_asset = {aid: v for aid, v in by_asset.items() if aid not in exclude_ids}

    # Resolve benchmark series — DB hit or yfinance fallback
    bm_display_ticker = bm_ticker
    if bm_asset:
        bm_series: list[tuple[date, float]] = by_asset.get(bm_asset.id, [])
        bm_display_ticker = bm_asset.yf_symbol or bm_ticker
    else:
        loop = asyncio.get_running_loop()
        bm_series = await loop.run_in_executor(None, _fetch_yf_history, bm_ticker, days + 10)
        if not bm_series and not bm_ticker.endswith(".TO") and not bm_ticker.endswith(".V"):
            bm_series = await loop.run_in_executor(None, _fetch_yf_history, bm_ticker + ".TO", days + 10)
            if bm_series:
                bm_display_ticker = bm_ticker + ".TO"
        if not bm_series:
            raise HTTPException(
                status_code=404,
                detail=f"No price data found for benchmark ticker '{bm_ticker}'. "
                       f"Try adding an exchange suffix (e.g. ZAG.TO for TSX).",
            )

    bm_series = [(d, c) for d, c in bm_series if d >= cutoff]

    all_dates: set[date] = set()
    for a in assets:
        for dt, _ in portfolio_by_asset.get(a.id, []):
            all_dates.add(dt)
    for dt, _ in bm_series:
        all_dates.add(dt)

    sorted_dates = sorted(all_dates)

    def portfolio_val_on(dt: date) -> Optional[float]:
        total = 0.0
        found = 0
        for a in assets:
            prices_on = [c for d, c in portfolio_by_asset.get(a.id, []) if d <= dt]
            if not prices_on:
                continue
            fx = usdcad if (a.market_price_currency or "CAD").upper() == "USD" else 1.0
            total += prices_on[-1] * fx * float(a.quantity or 0)
            found += 1
        return total if found >= max(1, len(assets) // 2) else None

    def bm_price_on(dt: date) -> Optional[float]:
        prices = [c for d, c in bm_series if d <= dt]
        return prices[-1] if prices else None

    series_points: list[BenchmarkPoint] = []
    port_base: Optional[float] = None
    bm_base: Optional[float] = None

    for dt in sorted_dates:
        pv = portfolio_val_on(dt)
        bv = bm_price_on(dt)
        if pv is None or bv is None:
            continue
        if port_base is None:
            port_base = pv
        if bm_base is None:
            bm_base = bv
        if port_base > 0 and bm_base > 0:
            series_points.append(BenchmarkPoint(
                date=dt,
                portfolio_value=round(pv / port_base * 100, 4),
                benchmark_value=round(bv / bm_base * 100, 4),
            ))

    port_ret: Optional[float] = None
    bm_ret: Optional[float] = None
    rel_ret: Optional[float] = None

    if len(series_points) >= 2:
        port_ret = round(series_points[-1].portfolio_value - 100, 4)
        bm_ret = round(series_points[-1].benchmark_value - 100, 4)
        rel_ret = round(port_ret - bm_ret, 4) if (port_ret is not None and bm_ret is not None) else None

    return BenchmarkComparison(
        days=days,
        benchmark_ticker=bm_display_ticker,
        portfolio_return_pct=port_ret,
        benchmark_return_pct=bm_ret,
        relative_return_pct=rel_ret,
        series=series_points,
    )


@router.get("/analyze", response_model=AnalysisResult, summary="Stock analyzer: correlations and portfolio impact")
@limiter.limit("100/minute")
async def portfolio_analyze(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    ticker: Annotated[str, Query(description="Ticker to analyze (e.g. AAPL, MSFT, ZAG.TO)")],
) -> AnalysisResult:
    target_ticker = ticker.strip().upper()

    assets_result = await db.execute(select(Asset).where(Asset.quantity.is_not(None)))
    assets = list(assets_result.scalars().all())

    fx_result = await db.execute(select(Asset).where(Asset.ticker == "USDCAD"))
    fx_asset = fx_result.scalar_one_or_none()

    cutoff_1y = date.today() - timedelta(days=366)

    all_ids = [a.id for a in assets]
    if fx_asset:
        all_ids.append(fx_asset.id)

    by_asset = await _load_history(db, all_ids, cutoff_1y)

    usdcad = 1.3753
    if fx_asset and fx_asset.id in by_asset and by_asset[fx_asset.id]:
        usdcad = by_asset[fx_asset.id][-1][1]

    portfolio_by_asset = {
        aid: v for aid, v in by_asset.items()
        if aid != (fx_asset.id if fx_asset else -1)
    }

    # Fetch target ticker history and info concurrently
    loop = asyncio.get_running_loop()
    target_history, ticker_info = await asyncio.gather(
        loop.run_in_executor(None, _fetch_yf_history, target_ticker, 370),
        loop.run_in_executor(None, _fetch_ticker_info, target_ticker),
    )

    # Try .TO suffix fallback for Canadian tickers typed without suffix
    if not target_history and not target_ticker.endswith(".TO") and not target_ticker.endswith(".V"):
        target_history = await loop.run_in_executor(None, _fetch_yf_history, target_ticker + ".TO", 370)
        if target_history:
            target_ticker = target_ticker + ".TO"
            ticker_info = await loop.run_in_executor(None, _fetch_ticker_info, target_ticker)

    if not target_history:
        raise HTTPException(
            status_code=404,
            detail=f"No price data found for '{target_ticker}'. "
                   f"Try adding an exchange suffix (e.g. ZAG.TO for TSX).",
        )

    # Correlations: log-returns aligned to common dates
    target_ret_map = _log_returns_map(target_history)
    correlations: list[CorrelationEntry] = []
    all_corrs: list[float] = []

    for asset in assets:
        series = portfolio_by_asset.get(asset.id, [])
        if len(series) < 10:
            continue
        asset_ret_map = _log_returns_map(series)
        common_dates = sorted(set(target_ret_map.keys()) & set(asset_ret_map.keys()))
        if len(common_dates) < 10:
            continue
        t_rets = [target_ret_map[d] for d in common_dates]
        a_rets = [asset_ret_map[d] for d in common_dates]
        corr = _pearson_corr(t_rets, a_rets)
        correlations.append(CorrelationEntry(
            ticker=asset.ticker,
            name=asset.name,
            correlation=round(corr, 4) if corr is not None else None,
        ))
        if corr is not None:
            all_corrs.append(corr)

    correlations.sort(key=lambda x: (x.correlation or 0), reverse=True)
    avg_corr = statistics.mean(all_corrs) if all_corrs else None

    # Pro-forma portfolio impact: blend portfolio 95% + new stock 5%
    # Build portfolio value per date (no carry-forward, only dates with live data)
    asset_price_dicts = {a.id: {dt: c for dt, c in portfolio_by_asset.get(a.id, [])} for a in assets}
    all_portfolio_dates = sorted(set().union(*(d.keys() for d in asset_price_dicts.values())))

    port_value_by_date: dict[date, float] = {}
    for dt in all_portfolio_dates:
        total = 0.0
        found = 0
        for a in assets:
            if dt in asset_price_dicts[a.id]:
                fx = usdcad if (a.market_price_currency or "CAD").upper() == "USD" else 1.0
                total += asset_price_dicts[a.id][dt] * fx * float(a.quantity or 0)
                found += 1
        if found >= max(1, len(assets) // 2):
            port_value_by_date[dt] = total

    sorted_pv_dates = sorted(port_value_by_date.keys())
    port_simple_ret_by_date: dict[date, float] = {}
    for i in range(1, len(sorted_pv_dates)):
        d_prev, d_curr = sorted_pv_dates[i - 1], sorted_pv_dates[i]
        pv_prev, pv_curr = port_value_by_date[d_prev], port_value_by_date[d_curr]
        if pv_prev > 0:
            port_simple_ret_by_date[d_curr] = (pv_curr - pv_prev) / pv_prev

    target_date_map = {dt: c for dt, c in target_history}
    sorted_target_dates = sorted(target_date_map.keys())
    target_simple_ret_by_date: dict[date, float] = {}
    for i in range(1, len(sorted_target_dates)):
        d_prev, d_curr = sorted_target_dates[i - 1], sorted_target_dates[i]
        t_prev, t_curr = target_date_map[d_prev], target_date_map[d_curr]
        if t_prev > 0:
            target_simple_ret_by_date[d_curr] = (t_curr - t_prev) / t_prev

    common_pf_dates = sorted(
        set(port_simple_ret_by_date.keys()) & set(target_simple_ret_by_date.keys())
    )

    current_vol: Optional[float] = None
    current_sharpe: Optional[float] = None
    proforma_vol: Optional[float] = None
    proforma_sharpe: Optional[float] = None

    if len(common_pf_dates) >= 10:
        port_rets = [port_simple_ret_by_date[d] for d in common_pf_dates]
        targ_rets = [target_simple_ret_by_date[d] for d in common_pf_dates]

        WEIGHT_NEW = 0.05
        proforma_rets = [(1 - WEIGHT_NEW) * p + WEIGHT_NEW * t for p, t in zip(port_rets, targ_rets)]

        def _vol_sharpe(rets: list[float]) -> tuple[Optional[float], Optional[float]]:
            if len(rets) < 3:
                return None, None
            std = statistics.stdev(rets)
            mean = statistics.mean(rets)
            if std <= 0:
                return None, None
            vol = std * math.sqrt(TRADING_DAYS) * 100
            sharpe = (mean * TRADING_DAYS - RISK_FREE_ANNUAL) / (std * math.sqrt(TRADING_DAYS))
            return vol, sharpe

        current_vol, current_sharpe = _vol_sharpe(port_rets)
        proforma_vol, proforma_sharpe = _vol_sharpe(proforma_rets)

    sharpe_delta = (
        round(proforma_sharpe - current_sharpe, 4)
        if proforma_sharpe is not None and current_sharpe is not None else None
    )
    vol_delta = (
        round(proforma_vol - current_vol, 4)
        if proforma_vol is not None and current_vol is not None else None
    )

    recommendation, recommendation_detail = _build_recommendation(
        target_ticker, correlations, avg_corr, sharpe_delta, vol_delta
    )

    cutoff_chart = date.today() - timedelta(days=366)
    chart_history = [
        PriceClose(date=dt, close=round(c, 4))
        for dt, c in target_history
        if dt >= cutoff_chart
    ]

    return AnalysisResult(
        ticker=target_ticker,
        name=ticker_info.get("name"),
        price_history=chart_history,
        correlations=correlations,
        portfolio_impact=PortfolioImpact(
            current_sharpe=round(current_sharpe, 4) if current_sharpe is not None else None,
            pro_forma_sharpe=round(proforma_sharpe, 4) if proforma_sharpe is not None else None,
            current_volatility_pct=round(current_vol, 4) if current_vol is not None else None,
            pro_forma_volatility_pct=round(proforma_vol, 4) if proforma_vol is not None else None,
            avg_correlation=round(avg_corr, 4) if avg_corr is not None else None,
            sharpe_delta=sharpe_delta,
            volatility_delta_pct=vol_delta,
        ),
        recommendation=recommendation,
        recommendation_detail=recommendation_detail,
    )
