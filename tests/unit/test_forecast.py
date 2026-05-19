"""
Unit tests for SpendingForecaster.

The `trained_forecaster` fixture is module-scoped so Prophet trains once for
the entire file rather than once per test function.
"""
from __future__ import annotations

import logging
import random
from datetime import date, timedelta

import pandas as pd
import pytest

# Keep Prophet / cmdstanpy output out of the test console.
logging.getLogger("prophet").setLevel(logging.WARNING)
logging.getLogger("cmdstanpy").setLevel(logging.WARNING)

from app.ml.forecast import SpendingForecaster


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_df(n_days: int = 90, seed: int = 42) -> pd.DataFrame:
    rng = random.Random(seed)
    base = date.today() - timedelta(days=n_days)
    rows = []
    for i in range(n_days):
        day = base + timedelta(days=i)
        rows.append(
            {
                "date": day,
                "amount": round(40.0 + rng.gauss(0, 5), 2),
                "category": "Food",
            }
        )
        rows.append(
            {
                "date": day,
                "amount": round(20.0 + rng.gauss(0, 3), 2),
                "category": "Transport",
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def trained_forecaster() -> SpendingForecaster:
    df = _make_df(90)
    fc = SpendingForecaster()
    fc.train_by_category(df)
    return fc


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_forecast_unknown_category_raises_key_error(trained_forecaster: SpendingForecaster):
    with pytest.raises(KeyError, match="UnknownCategory"):
        trained_forecaster.forecast("UnknownCategory", periods=30)


def test_skips_category_with_single_data_point():
    df = pd.DataFrame(
        {"date": [date.today()], "amount": [50.0], "category": ["OneDay"]}
    )
    fc = SpendingForecaster()
    fc.train_by_category(df)
    assert "OneDay" not in fc._models


def test_skips_category_with_zero_rows():
    df = pd.DataFrame({"date": [], "amount": [], "category": []})
    fc = SpendingForecaster()
    fc.train_by_category(df)
    assert len(fc._models) == 0


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------


def test_forecast_returns_required_columns(trained_forecaster: SpendingForecaster):
    result = trained_forecaster.forecast("Food", periods=30)
    assert {"ds", "yhat", "yhat_lower", "yhat_upper"}.issubset(result.columns)


def test_forecast_ds_column_is_datetime(trained_forecaster: SpendingForecaster):
    result = trained_forecaster.forecast("Food", periods=10)
    assert pd.api.types.is_datetime64_any_dtype(result["ds"])


def test_forecast_numeric_columns_are_float(trained_forecaster: SpendingForecaster):
    result = trained_forecaster.forecast("Food", periods=10)
    for col in ("yhat", "yhat_lower", "yhat_upper"):
        assert result[col].dtype.kind == "f", f"{col} is not float"


# ---------------------------------------------------------------------------
# Row counts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("periods", [30, 60, 90])
def test_forecast_returns_exact_periods(trained_forecaster: SpendingForecaster, periods: int):
    result = trained_forecaster.forecast("Food", periods=periods)
    assert len(result) == periods


def test_forecast_index_is_reset(trained_forecaster: SpendingForecaster):
    result = trained_forecaster.forecast("Food", periods=30)
    assert list(result.index) == list(range(30))


# ---------------------------------------------------------------------------
# Correctness invariants
# ---------------------------------------------------------------------------


def test_confidence_bands_order(trained_forecaster: SpendingForecaster):
    """yhat_lower ≤ yhat ≤ yhat_upper must hold for every row."""
    result = trained_forecaster.forecast("Food", periods=90)
    assert (result["yhat_lower"] <= result["yhat"]).all()
    assert (result["yhat"] <= result["yhat_upper"]).all()


def test_forecasts_are_future_dates(trained_forecaster: SpendingForecaster):
    """The returned rows should be for dates after the last training day."""
    result = trained_forecaster.forecast("Food", periods=30)
    today = pd.Timestamp(date.today())
    assert (result["ds"] >= today).all()


# ---------------------------------------------------------------------------
# Multiple categories
# ---------------------------------------------------------------------------


def test_both_categories_trained(trained_forecaster: SpendingForecaster):
    assert "Food" in trained_forecaster._models
    assert "Transport" in trained_forecaster._models


def test_separate_models_per_category(trained_forecaster: SpendingForecaster):
    food_result = trained_forecaster.forecast("Food", periods=30)
    transport_result = trained_forecaster.forecast("Transport", periods=30)
    # Models are independent, so median forecasts should differ
    assert not (food_result["yhat"].values == transport_result["yhat"].values).all()
