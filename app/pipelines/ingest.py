import asyncio
import random
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, Union

import pandas as pd
import yfinance as yf
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Asset, PriceHistory, Transaction
from app.db.session import AsyncSessionLocal

logger = get_logger(__name__)

_DEFAULT_CSV = Path(__file__).resolve().parents[2] / "data" / "transactions.csv"

# Weighted synthetic transaction templates
_CATEGORIES: dict[str, dict] = {
    "Housing": {
        "weight": 0.10,
        "range": (600.0, 2500.0),
        "sign": -1,
        "descriptions": ["Rent", "Mortgage Payment", "Electricity", "Internet Bill", "Water Bill"],
    },
    "Food": {
        "weight": 0.22,
        "range": (8.0, 220.0),
        "sign": -1,
        "descriptions": ["Groceries", "Restaurant", "Coffee Shop", "Takeout", "Food Delivery"],
    },
    "Transport": {
        "weight": 0.12,
        "range": (12.0, 350.0),
        "sign": -1,
        "descriptions": ["Gas Station", "Uber", "Monthly Transit Pass", "Parking", "Car Insurance"],
    },
    "Entertainment": {
        "weight": 0.10,
        "range": (10.0, 180.0),
        "sign": -1,
        "descriptions": ["Netflix", "Spotify", "Cinema Tickets", "Concert", "Gaming"],
    },
    "Health": {
        "weight": 0.08,
        "range": (20.0, 450.0),
        "sign": -1,
        "descriptions": ["Gym Membership", "Pharmacy", "Doctor Visit", "Vitamins", "Dental"],
    },
    "Shopping": {
        "weight": 0.13,
        "range": (15.0, 600.0),
        "sign": -1,
        "descriptions": ["Clothing", "Electronics", "Amazon", "Home Goods", "Books"],
    },
    "Income": {
        "weight": 0.15,
        "range": (3200.0, 6500.0),
        "sign": 1,
        "descriptions": ["Salary Deposit", "Freelance Payment", "Quarterly Bonus", "Dividend"],
    },
    "Savings": {
        "weight": 0.10,
        "range": (200.0, 1200.0),
        "sign": -1,
        "descriptions": ["Transfer to Savings", "Index Fund Purchase", "Emergency Fund"],
    },
}


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------


async def fetch_market_data(
    tickers: list,
    session: Optional[AsyncSession] = None,
) -> dict:
    """Download 2 years of daily OHLCV for each ticker and upsert to DB.

    Returns {ticker: rows_upserted}.  Safe to re-run — existing rows are
    updated in-place via the uq_price_history_asset_date constraint.
    """
    own_session = session is None
    _session = AsyncSessionLocal() if own_session else session
    results: dict[str, int] = {}

    try:
        for ticker in tickers:
            log = logger.bind(ticker=ticker)
            log.info("fetching market data")
            try:
                hist = await asyncio.get_event_loop().run_in_executor(
                    None, _download_ticker, ticker
                )
                if hist.empty:
                    log.warning("no data returned from yfinance")
                    continue

                asset = await _get_or_create_asset(_session, ticker)
                await _session.flush()

                rows = _build_price_rows(asset.id, hist)
                if rows:
                    stmt = pg_insert(PriceHistory).values(rows)
                    stmt = stmt.on_conflict_do_update(
                        constraint="uq_price_history_asset_date",
                        set_={col: getattr(stmt.excluded, col) for col in ("open", "high", "low", "close", "volume")},
                    )
                    await _session.execute(stmt)
                    results[ticker] = len(rows)
                    log.info("upserted price rows", count=len(rows))

            except Exception:
                log.exception("failed to fetch ticker")

        await _session.commit()
    except Exception:
        await _session.rollback()
        raise
    finally:
        if own_session:
            await _session.close()

    return results


def _download_ticker(ticker: str) -> pd.DataFrame:
    return yf.Ticker(ticker).history(period="2y", interval="1d", auto_adjust=True)


async def _get_or_create_asset(session: AsyncSession, ticker: str) -> Asset:
    result = await session.execute(select(Asset).where(Asset.ticker == ticker))
    asset = result.scalar_one_or_none()
    if asset is None:
        asset = Asset(ticker=ticker, asset_type="stock")
        session.add(asset)
    return asset


def _build_price_rows(asset_id: int, hist: pd.DataFrame) -> list[dict]:
    rows = []
    for ts, row in hist.iterrows():
        rows.append({
            "asset_id": asset_id,
            "date": ts.date(),
            "open": float(row["Open"]) if pd.notna(row["Open"]) else None,
            "high": float(row["High"]) if pd.notna(row["High"]) else None,
            "low": float(row["Low"]) if pd.notna(row["Low"]) else None,
            "close": float(row["Close"]),
            "volume": int(row["Volume"]) if pd.notna(row["Volume"]) else None,
        })
    return rows


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


async def fetch_transactions(
    session: Optional[AsyncSession] = None,
    csv_path: Optional[Union[Path, str]] = None,
) -> int:
    """Load transactions from CSV if it exists, else generate synthetic data.

    Idempotent — rows matching on (date, amount, description, category) are
    skipped, so re-running the pipeline never creates duplicates.
    Returns the number of rows inserted.
    """
    own_session = session is None
    _session = AsyncSessionLocal() if own_session else session

    try:
        src = Path(csv_path) if csv_path else _DEFAULT_CSV
        if src.exists():
            df = _load_csv(src)
            logger.info("loaded transactions from CSV", path=str(src), rows=len(df))
        else:
            df = _generate_synthetic_transactions()
            logger.info("generated synthetic transactions", rows=len(df))

        inserted = await _upsert_transactions(_session, df)
        await _session.commit()
        logger.info("transactions committed", inserted=inserted)
        return inserted
    except Exception:
        await _session.rollback()
        raise
    finally:
        if own_session:
            await _session.close()


def _load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["amount"] = df["amount"].astype(float)
    for col in ("description", "source"):
        if col not in df.columns:
            df[col] = None
    return df[["date", "amount", "category", "description", "source"]]


def _generate_synthetic_transactions() -> pd.DataFrame:
    """Deterministic synthetic data (seed=42) covering 2 years."""
    rng = random.Random(42)
    today = date.today()
    rows = []

    for offset in range(730):
        day = today - timedelta(days=730 - offset)
        for _ in range(rng.randint(1, 4)):
            cat_name = rng.choices(
                list(_CATEGORIES.keys()),
                weights=[v["weight"] for v in _CATEGORIES.values()],
            )[0]
            spec = _CATEGORIES[cat_name]
            raw = round(rng.uniform(*spec["range"]), 2)
            rows.append({
                "date": day,
                "amount": spec["sign"] * raw,
                "category": cat_name,
                "description": rng.choice(spec["descriptions"]),
                "source": "synthetic",
            })

    return pd.DataFrame(rows)


async def _upsert_transactions(session: AsyncSession, df: pd.DataFrame) -> int:
    """Insert only rows not already present on (date, amount, description, category)."""
    if df.empty:
        return 0

    min_date, max_date = df["date"].min(), df["date"].max()
    result = await session.execute(
        select(
            Transaction.date,
            Transaction.amount,
            Transaction.description,
            Transaction.category,
        ).where(Transaction.date.between(min_date, max_date))
    )
    existing = {
        (row.date, float(row.amount), row.description, row.category)
        for row in result.all()
    }

    new_rows = [
        Transaction(
            date=row["date"],
            amount=row["amount"],
            category=row["category"],
            description=row.get("description"),
            source=row.get("source", "synthetic"),
        )
        for _, row in df.iterrows()
        if (row["date"], float(row["amount"]), row.get("description"), row["category"]) not in existing
    ]

    if new_rows:
        session.add_all(new_rows)

    return len(new_rows)
