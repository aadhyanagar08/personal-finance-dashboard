from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from app.ml.anomaly import AnomalyDetector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normal_df(n: int = 200, seed: int = 42) -> pd.DataFrame:
    """n rows of normally-distributed ~$50 Food transactions."""
    rng = np.random.default_rng(seed)
    today = date.today()
    return pd.DataFrame(
        {
            "date": [today - timedelta(days=i) for i in range(n)],
            "amount": rng.normal(50.0, 5.0, n).tolist(),
            "category": ["Food"] * n,
        }
    )


# ---------------------------------------------------------------------------
# Guard: predict before train
# ---------------------------------------------------------------------------


def test_predict_raises_before_training():
    with pytest.raises(RuntimeError, match="not trained"):
        AnomalyDetector().predict(_normal_df(5))


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------


def test_output_has_required_columns():
    df = _normal_df(100)
    det = AnomalyDetector()
    det.train(df)
    result = det.predict(df)
    assert "is_anomaly" in result.columns
    assert "anomaly_score" in result.columns


def test_output_preserves_row_count():
    df = _normal_df(80)
    det = AnomalyDetector()
    det.train(df)
    assert len(det.predict(df)) == len(df)


def test_is_anomaly_is_bool_dtype():
    df = _normal_df(80)
    det = AnomalyDetector()
    det.train(df)
    result = det.predict(df)
    assert result["is_anomaly"].dtype == bool


def test_anomaly_score_is_float():
    df = _normal_df(80)
    det = AnomalyDetector()
    det.train(df)
    result = det.predict(df)
    assert result["anomaly_score"].dtype.kind == "f"


# ---------------------------------------------------------------------------
# Correctness
# ---------------------------------------------------------------------------


def test_extreme_outlier_flagged():
    """A $1,000,000 transaction against a ~$50 normal distribution must be flagged."""
    normal = _normal_df(200)
    det = AnomalyDetector(contamination=0.05)
    det.train(normal)

    outlier = pd.DataFrame(
        {"date": [date.today()], "amount": [1_000_000.0], "category": ["Food"]}
    )
    result = det.predict(outlier)
    assert bool(result["is_anomaly"].iloc[0])


def test_outlier_has_higher_anomaly_score_than_inlier():
    normal = _normal_df(200)
    det = AnomalyDetector(contamination=0.05)
    det.train(normal)

    test_df = pd.DataFrame(
        {
            "date": [date.today(), date.today()],
            "amount": [50.0, 500_000.0],
            "category": ["Food", "Food"],
        }
    )
    result = det.predict(test_df)
    # anomaly_score is negated decision function: higher → more anomalous
    assert result["anomaly_score"].iloc[1] > result["anomaly_score"].iloc[0]


def test_higher_contamination_flags_more_anomalies():
    df = _normal_df(200)
    det_low = AnomalyDetector(contamination=0.03)
    det_low.train(df)

    det_high = AnomalyDetector(contamination=0.20)
    det_high.train(df)

    flagged_low = det_low.predict(df)["is_anomaly"].sum()
    flagged_high = det_high.predict(df)["is_anomaly"].sum()
    assert flagged_high > flagged_low


# ---------------------------------------------------------------------------
# Robustness: unseen categories
# ---------------------------------------------------------------------------


def test_unseen_category_does_not_raise():
    """The encoder maps unknown categories to the first known class without raising."""
    normal = _normal_df(100)
    det = AnomalyDetector()
    det.train(normal)

    unseen = pd.DataFrame(
        {"date": [date.today()], "amount": [50.0], "category": ["NeverSeen"]}
    )
    result = det.predict(unseen)
    assert len(result) == 1
    assert "is_anomaly" in result.columns


def test_multiple_categories_encoded():
    rng = np.random.default_rng(0)
    today = date.today()
    n = 60
    df = pd.DataFrame(
        {
            "date": [today - timedelta(days=i) for i in range(n)],
            "amount": rng.normal(50.0, 5.0, n).tolist(),
            "category": (["Food"] * 30 + ["Transport"] * 30),
        }
    )
    det = AnomalyDetector()
    det.train(df)
    result = det.predict(df)
    assert len(result) == n
    assert "is_anomaly" in result.columns
