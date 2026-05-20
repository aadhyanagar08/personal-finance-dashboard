#!/usr/bin/env python3
"""
scripts/run_ml_pipeline.py

Trains all three ML models on live transaction data from Postgres and
writes results (anomaly flags, forecasts) back to the database.

Steps
-----
1. Load transactions from Postgres via psycopg2 (sync, no FastAPI needed).
2. AnomalyDetector  — IsolationForest on (amount, day_of_week, category).
   Updates is_anomaly in the transactions table.
3. SpendingForecaster — Prophet, one model per category with ≥ 2 daily
   observations. Writes 90-day forecasts to the forecasts table.
4. TransactionCategorizer — TF-IDF + LogisticRegression on description.
5. Save all three models to data/models/ via joblib.

Usage
-----
    # From project root:
    python scripts/run_ml_pipeline.py

    # Override DB URL:
    DATABASE_URL=postgresql://... python scripts/run_ml_pipeline.py
"""
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

# ---------------------------------------------------------------------------
# Make project root importable so we can reuse app/ml/ classes
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import joblib
import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from prophet import Prophet

from app.ml.anomaly import AnomalyDetector
from app.ml.categorizer import TransactionCategorizer
from app.ml.forecast import SpendingForecaster

# ---------------------------------------------------------------------------
# Logging — plain stdlib so the script works with no structlog config
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
# Silence Prophet / cmdstanpy / matplotlib chatter
for _noisy in ("prophet", "cmdstanpy", "matplotlib", "numexpr", "pandas"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

log = logging.getLogger("ml_pipeline")

# ---------------------------------------------------------------------------
# DB connection
# ---------------------------------------------------------------------------
MODEL_DIR = ROOT / "data" / "models"


def _connect() -> psycopg2.extensions.connection:
    raw_url = os.environ.get("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/finplatform")
    # Strip SQLAlchemy dialect prefix so psycopg2 accepts it
    dsn = raw_url.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg2://", "postgresql://")
    return psycopg2.connect(dsn)


def load_transactions(conn: psycopg2.extensions.connection) -> pd.DataFrame:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT id, date, amount::float AS amount, category, description "
            "FROM transactions ORDER BY date"
        )
        rows = cur.fetchall()
    df = pd.DataFrame(rows)
    log.info("Loaded %d transactions (%s → %s)", len(df), df["date"].min(), df["date"].max())
    return df


# ---------------------------------------------------------------------------
# Step 1 — Anomaly detection
# ---------------------------------------------------------------------------

def run_anomaly(df: pd.DataFrame, conn: psycopg2.extensions.connection) -> AnomalyDetector:
    log.info("─── Step 1: AnomalyDetector ─────────────────────────────────")
    t0 = time.monotonic()

    detector = AnomalyDetector(contamination=0.05)
    detector.train(df)

    predictions = detector.predict(df)
    anomalies = predictions[predictions["is_anomaly"]]
    log.info(
        "IsolationForest flagged %d / %d transactions (%.1f%%)",
        len(anomalies),
        len(df),
        100 * len(anomalies) / len(df),
    )

    # Persist is_anomaly flags to DB
    with conn.cursor() as cur:
        # Reset all flags first so a re-run clears stale results
        cur.execute("UPDATE transactions SET is_anomaly = FALSE")

        if not anomalies.empty:
            anomaly_ids = [int(r) for r in anomalies["id"].tolist()]
            cur.execute(
                "UPDATE transactions SET is_anomaly = TRUE WHERE id = ANY(%s)",
                (anomaly_ids,),
            )
            log.info("Marked %d rows as is_anomaly=TRUE", len(anomaly_ids))

    conn.commit()
    log.info("Anomaly step done in %.1fs", time.monotonic() - t0)
    return detector


# ---------------------------------------------------------------------------
# Step 2 — Spending forecasts
# ---------------------------------------------------------------------------

def run_forecasts(df: pd.DataFrame, conn: psycopg2.extensions.connection) -> SpendingForecaster:
    log.info("─── Step 2: SpendingForecaster ──────────────────────────────")
    t0 = time.monotonic()

    forecaster = SpendingForecaster()
    forecaster.train_by_category(df)

    trained_cats = list(forecaster._models.keys())
    log.info("Trained Prophet models for %d categories: %s", len(trained_cats), trained_cats)

    if not trained_cats:
        log.warning("No categories had enough data to train. Skipping forecast writes.")
        return forecaster

    # Delete ALL rows for trained categories so re-runs start from a clean slate.
    # Scoping by category (not by date) avoids leaving stale past rows from earlier
    # pipeline runs that started on a different date.
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM forecasts WHERE category = ANY(%s)",
            (trained_cats,),
        )
        deleted = cur.rowcount
    log.info("Cleared %d stale forecast rows for trained categories", deleted)

    total_written = 0
    model_version = "prophet-v1"

    with conn.cursor() as cur:
        for category in trained_cats:
            try:
                fc_df = forecaster.forecast(category, periods=90)
                rows = []
                for _, row in fc_df.iterrows():
                    rows.append((
                        row["ds"].date(),
                        round(max(float(row["yhat"]), 0), 2),
                        category,
                        round(max(float(row["yhat_lower"]), 0), 2),
                        round(max(float(row["yhat_upper"]), 0), 2),
                        model_version,
                        datetime.now(timezone.utc),
                    ))

                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO forecasts
                      (forecast_date, predicted_amount, category,
                       confidence_lower, confidence_upper, model_version, created_at)
                    VALUES %s
                    """,
                    rows,
                    template="(%s, %s, %s, %s, %s, %s, %s)",
                )
                total_written += len(rows)
                log.info("  %-15s → wrote %d forecast rows", category, len(rows))
            except Exception as exc:
                log.warning("  Skipping %s: %s", category, exc)

    conn.commit()
    log.info("Forecast step done — %d total rows written in %.1fs", total_written, time.monotonic() - t0)
    return forecaster


# ---------------------------------------------------------------------------
# Step 3 — Transaction categorizer
# ---------------------------------------------------------------------------

def run_categorizer(df: pd.DataFrame) -> TransactionCategorizer:
    log.info("─── Step 3: TransactionCategorizer ──────────────────────────")
    t0 = time.monotonic()

    # Drop rows with null descriptions — they can't be used for TF-IDF training
    labeled = df.dropna(subset=["description"]).copy()
    labeled = labeled[labeled["category"].notna() & (labeled["category"] != "")]

    log.info("Training on %d labeled transactions across %d categories", len(labeled), labeled["category"].nunique())

    if labeled["category"].nunique() < 2:
        log.warning("Need at least 2 categories; skipping categorizer training.")
        return TransactionCategorizer()

    categorizer = TransactionCategorizer()
    categorizer.train(labeled)

    # Smoke-test predictions
    sample = labeled["description"].iloc[0]
    pred = categorizer.predict(str(sample))
    log.info(
        "Smoke test: '%s' → %s (confidence %.2f)",
        sample,
        pred["category"],
        pred["confidence"],
    )

    log.info("Categorizer step done in %.1fs", time.monotonic() - t0)
    return categorizer


# ---------------------------------------------------------------------------
# Step 4 — Save models
# ---------------------------------------------------------------------------

def save_models(detector: AnomalyDetector, forecaster: SpendingForecaster, categorizer: TransactionCategorizer) -> None:
    log.info("─── Step 4: Saving models ───────────────────────────────────")
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    detector.save_model()
    log.info("Saved anomaly model → %s", MODEL_DIR / "anomaly_detector.joblib")

    # SpendingForecaster doesn't have a save_model(), so we joblib it directly
    forecaster_path = MODEL_DIR / "spending_forecaster.joblib"
    joblib.dump(forecaster._models, forecaster_path)
    log.info("Saved forecaster models → %s", forecaster_path)

    categorizer.save_model()
    log.info("Saved categorizer model → %s", MODEL_DIR / "categorizer.joblib")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    pipeline_start = time.monotonic()
    log.info("═══ ML Pipeline starting ════════════════════════════════════")
    log.info("Model output directory: %s", MODEL_DIR)

    conn = _connect()
    try:
        df = load_transactions(conn)

        if df.empty:
            log.error("No transactions in the database — aborting.")
            sys.exit(1)

        detector = run_anomaly(df, conn)
        forecaster = run_forecasts(df, conn)
        categorizer = run_categorizer(df)
        save_models(detector, forecaster, categorizer)

    finally:
        conn.close()

    elapsed = time.monotonic() - pipeline_start
    log.info("═══ Pipeline complete in %.1fs ══════════════════════════════", elapsed)


if __name__ == "__main__":
    main()
