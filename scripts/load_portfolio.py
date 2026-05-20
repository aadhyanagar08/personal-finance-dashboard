#!/usr/bin/env python3
"""
scripts/load_portfolio.py

1. Reads data/portfolio.csv and upserts rows into the assets table.
2. Fetches 2 years of daily OHLCV price history from yfinance for every
   ticker in the CSV (applying exchange-specific suffixes) plus the
   USD/CAD FX series (USDCAD=X).
3. Upserts all OHLCV rows into the price_history table.

Usage
-----
    python scripts/load_portfolio.py          # uses .env DATABASE_URL
    DATABASE_URL=postgresql://... python scripts/load_portfolio.py
"""
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
import psycopg2
import psycopg2.extras
import yfinance as yf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
for noisy in ("yfinance", "urllib3", "peewee", "curl_cffi"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

log = logging.getLogger("load_portfolio")

CSV_PATH = ROOT / "data" / "portfolio.csv"

# Exchange → yfinance suffix
_SUFFIX = {"TSX": ".TO", "TSX-V": ".V", "NYSE": "", "NASDAQ": ""}

# Benchmark: fetch alongside holdings
BENCHMARK_SYMBOL = "XIC.TO"
FX_SYMBOL = "USDCAD=X"


def _connect() -> psycopg2.extensions.connection:
    raw = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/finplatform",
    )
    dsn = raw.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg2://", "postgresql://"
    )
    return psycopg2.connect(dsn)


# ---------------------------------------------------------------------------
# Step 1 — load portfolio CSV and upsert assets
# ---------------------------------------------------------------------------


def load_csv() -> pd.DataFrame:
    df = pd.read_csv(CSV_PATH)
    # Normalise Unicode minus sign used in some cells
    for col in ("unrealized_return_cad",):
        if col in df.columns:
            df[col] = (
                df[col].astype(str)
                .str.replace("−", "-", regex=False)
                .astype(float)
            )
    return df


def _yf_symbol(row: pd.Series) -> str:
    suffix = _SUFFIX.get(str(row["exchange"]).strip(), "")
    return str(row["symbol"]).strip() + suffix


def upsert_assets(df: pd.DataFrame, conn: psycopg2.extensions.connection) -> dict[str, int]:
    """Insert/update assets. Returns {ticker: asset_id}."""
    with conn.cursor() as cur:
        for _, row in df.iterrows():
            ticker = str(row["symbol"]).strip()
            yf_sym = _yf_symbol(row)
            cur.execute(
                """
                INSERT INTO assets
                  (ticker, name, asset_type, exchange, yf_symbol, quantity,
                   market_price, market_price_currency,
                   book_value_cad, market_value_cad, unrealized_return_cad)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (ticker) DO UPDATE SET
                  name                  = EXCLUDED.name,
                  asset_type            = EXCLUDED.asset_type,
                  exchange              = EXCLUDED.exchange,
                  yf_symbol             = EXCLUDED.yf_symbol,
                  quantity              = EXCLUDED.quantity,
                  market_price          = EXCLUDED.market_price,
                  market_price_currency = EXCLUDED.market_price_currency,
                  book_value_cad        = EXCLUDED.book_value_cad,
                  market_value_cad      = EXCLUDED.market_value_cad,
                  unrealized_return_cad = EXCLUDED.unrealized_return_cad
                RETURNING id
                """,
                (
                    ticker,
                    str(row["name"]),
                    str(row["security_type"]).upper(),
                    str(row["exchange"]).strip(),
                    yf_sym,
                    float(row["quantity"]),
                    float(row["market_price"]),
                    str(row["market_price_currency"]).strip(),
                    float(row["book_value_cad"]),
                    float(row["market_value_cad"]),
                    float(row["unrealized_return_cad"]),
                ),
            )
            asset_id = cur.fetchone()[0]
            log.info("  Upserted %-8s (id=%d, yf=%s)", ticker, asset_id, yf_sym)

    conn.commit()

    # Re-fetch the id map
    with conn.cursor() as cur:
        cur.execute("SELECT ticker, id FROM assets")
        return dict(cur.fetchall())


# ---------------------------------------------------------------------------
# Step 2 — fetch price history from yfinance
# ---------------------------------------------------------------------------


def _download(symbol: str) -> pd.DataFrame:
    """Return a cleaned OHLCV DataFrame or empty DataFrame on failure."""
    try:
        ticker_obj = yf.Ticker(symbol)
        hist = ticker_obj.history(period="2y", interval="1d", auto_adjust=True)
        if hist.empty:
            log.warning("  %-12s  no data returned", symbol)
            return pd.DataFrame()
        hist = hist[["Open", "High", "Low", "Close", "Volume"]].copy()
        hist.index = pd.to_datetime(hist.index).date
        hist.index.name = "date"
        hist.dropna(subset=["Close"], inplace=True)
        log.info("  %-12s  %d rows  %s → %s", symbol, len(hist), hist.index.min(), hist.index.max())
        return hist
    except Exception as exc:
        log.warning("  %-12s  download failed: %s", symbol, exc)
        return pd.DataFrame()


def fetch_and_store_history(
    df_portfolio: pd.DataFrame,
    ticker_id_map: dict[str, int],
    conn: psycopg2.extensions.connection,
) -> None:
    all_symbols: list[tuple[str, int | None]] = []

    for _, row in df_portfolio.iterrows():
        ticker = str(row["symbol"]).strip()
        yf_sym = _yf_symbol(row)
        asset_id = ticker_id_map.get(ticker)
        if asset_id is None:
            log.warning("No asset_id found for ticker %s — skipping", ticker)
            continue
        all_symbols.append((yf_sym, asset_id))

    # Also fetch FX rate so metrics can convert USD → CAD historically
    # Store under the special ticker USDCAD in assets (no portfolio columns)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO assets (ticker, name, asset_type, yf_symbol)
            VALUES ('USDCAD', 'USD/CAD Exchange Rate', 'FX', 'USDCAD=X')
            ON CONFLICT (ticker) DO UPDATE SET yf_symbol = EXCLUDED.yf_symbol
            RETURNING id
            """
        )
        fx_id = cur.fetchone()[0]
    conn.commit()
    all_symbols.append(("USDCAD=X", fx_id))

    total_inserted = 0
    now_utc = datetime.now(timezone.utc)

    for yf_sym, asset_id in all_symbols:
        log.info("Fetching %s …", yf_sym)
        hist = _download(yf_sym)
        if hist.empty:
            continue

        rows = []
        for dt, price_row in hist.iterrows():
            open_p = float(price_row["Open"]) if pd.notna(price_row["Open"]) else None
            high_p = float(price_row["High"]) if pd.notna(price_row["High"]) else None
            low_p = float(price_row["Low"]) if pd.notna(price_row["Low"]) else None
            close_p = float(price_row["Close"])
            vol = int(price_row["Volume"]) if pd.notna(price_row["Volume"]) else None
            rows.append((asset_id, dt, open_p, high_p, low_p, close_p, vol, now_utc))

        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO price_history
                  (asset_id, date, open, high, low, close, volume, created_at)
                VALUES %s
                ON CONFLICT ON CONSTRAINT uq_price_history_asset_date
                DO UPDATE SET
                  open   = EXCLUDED.open,
                  high   = EXCLUDED.high,
                  low    = EXCLUDED.low,
                  close  = EXCLUDED.close,
                  volume = EXCLUDED.volume
                """,
                rows,
                template="(%s,%s,%s,%s,%s,%s,%s,%s)",
                page_size=500,
            )
        conn.commit()
        total_inserted += len(rows)
        log.info("  → %d rows stored for asset_id=%d", len(rows), asset_id)

    log.info("Total price_history rows upserted: %d", total_inserted)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    t0 = time.monotonic()
    log.info("═══ load_portfolio.py starting ══════════════════════════════")

    df = load_csv()
    log.info("Loaded %d holdings from %s", len(df), CSV_PATH)

    conn = _connect()
    try:
        log.info("─── Step 1: Upserting assets ────────────────────────────────")
        ticker_id_map = upsert_assets(df, conn)
        log.info("Assets table: %d rows", len(ticker_id_map))

        log.info("─── Step 2: Fetching price history ──────────────────────────")
        fetch_and_store_history(df, ticker_id_map, conn)
    finally:
        conn.close()

    log.info("═══ Done in %.1fs ════════════════════════════════════════════", time.monotonic() - t0)


if __name__ == "__main__":
    main()
