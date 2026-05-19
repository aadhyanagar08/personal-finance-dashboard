from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import LabelEncoder

from app.core.logging import get_logger

logger = get_logger(__name__)

_MODEL_DIR = Path(__file__).resolve().parents[2] / "data" / "models"
_MODEL_PATH = _MODEL_DIR / "anomaly_detector.joblib"


class AnomalyDetector:
    def __init__(self, contamination: float = 0.05) -> None:
        self._model = IsolationForest(contamination=contamination, random_state=42, n_jobs=-1)
        self._encoder = LabelEncoder()
        self._fitted = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _encode_categories(self, categories: pd.Series, *, fit: bool) -> np.ndarray:
        values = categories.astype(str).values
        if fit:
            return self._encoder.fit_transform(values)
        # Map unseen labels to the first known class rather than raising
        known = set(self._encoder.classes_)
        safe = np.where(np.isin(values, list(known)), values, self._encoder.classes_[0])
        return self._encoder.transform(safe)

    def _build_features(self, df: pd.DataFrame, *, fit: bool = False) -> np.ndarray:
        amount = df["amount"].astype(float).values
        day_of_week = pd.to_datetime(df["date"]).dt.dayofweek.values
        cat_encoded = self._encode_categories(df["category"], fit=fit)
        return np.column_stack([amount, day_of_week, cat_encoded])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def train(self, transactions_df: pd.DataFrame) -> None:
        """Fit IsolationForest on Amount, day_of_week, category_encoded."""
        X = self._build_features(transactions_df, fit=True)
        self._model.fit(X)
        self._fitted = True
        logger.info("anomaly detector trained", rows=len(transactions_df))

    def predict(self, transactions_df: pd.DataFrame) -> pd.DataFrame:
        """Return transactions_df with added is_anomaly (bool) and anomaly_score (float).

        anomaly_score is the negated decision function: higher = more anomalous.
        """
        if not self._fitted:
            raise RuntimeError("Model not trained. Call train() or load_model() first.")
        X = self._build_features(transactions_df, fit=False)
        raw_scores = self._model.decision_function(X)
        predictions = self._model.predict(X)  # 1 = inlier, -1 = outlier
        result = transactions_df.copy()
        result["is_anomaly"] = predictions == -1
        result["anomaly_score"] = (-raw_scores).astype(float)
        return result

    def save_model(self) -> Path:
        _MODEL_DIR.mkdir(parents=True, exist_ok=True)
        joblib.dump({"model": self._model, "encoder": self._encoder}, _MODEL_PATH)
        logger.info("anomaly model saved", path=str(_MODEL_PATH))
        return _MODEL_PATH

    def load_model(self) -> None:
        payload = joblib.load(_MODEL_PATH)
        self._model = payload["model"]
        self._encoder = payload["encoder"]
        self._fitted = True
        logger.info("anomaly model loaded", path=str(_MODEL_PATH))
