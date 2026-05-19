from __future__ import annotations

import asyncio
from pathlib import Path

import joblib
import pandas as pd
from fastapi import APIRouter, BackgroundTasks
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sqlalchemy import select, update

from app.core.logging import get_logger
from app.db.models import Transaction
from app.db.session import AsyncSessionLocal

logger = get_logger(__name__)

_MODEL_DIR = Path(__file__).resolve().parents[2] / "data" / "models"
_MODEL_PATH = _MODEL_DIR / "categorizer.joblib"
_UNCATEGORIZED = "Uncategorized"


class TransactionCategorizer:
    def __init__(self) -> None:
        self._pipeline: Pipeline | None = None

    def train(self, labeled_df: pd.DataFrame) -> None:
        """Fit TF-IDF + LogisticRegression on description → category."""
        texts = labeled_df["description"].fillna("").astype(str).tolist()
        labels = labeled_df["category"].astype(str).tolist()
        self._pipeline = Pipeline([
            ("tfidf", TfidfVectorizer(ngram_range=(1, 2), max_features=10_000, sublinear_tf=True)),
            ("clf", LogisticRegression(max_iter=1_000, C=1.0, class_weight="balanced")),
        ])
        self._pipeline.fit(texts, labels)
        logger.info(
            "categorizer trained",
            samples=len(texts),
            classes=list(self._pipeline.classes_),
        )

    def predict(self, description: str) -> dict[str, object]:
        """Return {'category': str, 'confidence': float} for a single description."""
        if self._pipeline is None:
            raise RuntimeError("Model not trained. Call train() or load_model() first.")
        probs = self._pipeline.predict_proba([description])[0]
        idx = int(probs.argmax())
        return {
            "category": str(self._pipeline.classes_[idx]),
            "confidence": float(probs[idx]),
        }

    def save_model(self) -> Path:
        _MODEL_DIR.mkdir(parents=True, exist_ok=True)
        joblib.dump(self._pipeline, _MODEL_PATH)
        logger.info("categorizer model saved", path=str(_MODEL_PATH))
        return _MODEL_PATH

    def load_model(self) -> None:
        self._pipeline = joblib.load(_MODEL_PATH)
        logger.info("categorizer model loaded", path=str(_MODEL_PATH))


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------

async def categorize_pending_transactions(categorizer: TransactionCategorizer) -> int:
    """Classify all transactions whose category is 'Uncategorized' and update in-place.

    Designed to run as a FastAPI BackgroundTask after a transaction ingest.
    Returns the number of rows updated.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Transaction).where(Transaction.category == _UNCATEGORIZED)
        )
        transactions = result.scalars().all()

        if not transactions:
            logger.info("no uncategorized transactions found")
            return 0

        loop = asyncio.get_running_loop()
        updated = 0
        for txn in transactions:
            desc = txn.description or ""
            prediction = await loop.run_in_executor(None, categorizer.predict, desc)
            await session.execute(
                update(Transaction)
                .where(Transaction.id == txn.id)
                .values(category=prediction["category"])
            )
            updated += 1

        await session.commit()
        logger.info("categorized transactions", updated=updated)
        return updated


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/ml", tags=["ml"])

# Module-level singleton; populate via load_model() at startup or after training.
_categorizer = TransactionCategorizer()


def get_categorizer() -> TransactionCategorizer:
    return _categorizer


@router.post("/categorize", summary="Auto-categorize pending transactions")
async def trigger_categorization(background_tasks: BackgroundTasks) -> dict:
    """Enqueue a background job that categorizes all 'Uncategorized' transactions."""
    if _categorizer._pipeline is None:
        return {"status": "skipped", "reason": "categorizer model not loaded"}
    background_tasks.add_task(categorize_pending_transactions, _categorizer)
    return {"status": "queued"}
