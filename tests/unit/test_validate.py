from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from app.pipelines.validate import DataQualityChecker


# ---------------------------------------------------------------------------
# check_no_nulls
# ---------------------------------------------------------------------------


def test_no_nulls_passes_clean_data():
    df = pd.DataFrame({"date": ["2024-01-01"], "close": [100.0]})
    report = DataQualityChecker().check_no_nulls(df, ["date", "close"]).generate_report()
    assert report["passed"] is True
    assert report["summary"]["failed"] == 0


def test_no_nulls_fails_missing_column():
    df = pd.DataFrame({"date": ["2024-01-01"]})
    report = DataQualityChecker().check_no_nulls(df, ["date", "close"]).generate_report()
    assert report["passed"] is False
    assert "close" in report["checks"]["no_nulls"]["details"]["missing_cols"]


def test_no_nulls_fails_with_nan_values():
    df = pd.DataFrame({"date": ["2024-01-01", None], "close": [100.0, 200.0]})
    report = DataQualityChecker().check_no_nulls(df, ["date", "close"]).generate_report()
    assert report["passed"] is False
    assert report["checks"]["no_nulls"]["details"]["null_counts"].get("date", 0) >= 1


def test_no_nulls_empty_required_list_passes():
    df = pd.DataFrame({"x": [1, 2, 3]})
    report = DataQualityChecker().check_no_nulls(df, []).generate_report()
    assert report["passed"] is True


def test_no_nulls_multiple_missing_columns():
    df = pd.DataFrame({"x": [1]})
    report = DataQualityChecker().check_no_nulls(df, ["a", "b", "c"]).generate_report()
    details = report["checks"]["no_nulls"]["details"]
    assert set(details["missing_cols"]) == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# check_date_range
# ---------------------------------------------------------------------------


def test_date_range_passes_today():
    df = pd.DataFrame({"date": [date.today()]})
    report = DataQualityChecker().check_date_range(df, max_days_old=0).generate_report()
    assert report["passed"] is True


def test_date_range_passes_within_window():
    df = pd.DataFrame({"date": [date.today() - timedelta(days=1)]})
    report = DataQualityChecker().check_date_range(df, max_days_old=2).generate_report()
    assert report["passed"] is True


def test_date_range_fails_stale_data():
    old = date.today() - timedelta(days=10)
    df = pd.DataFrame({"date": [old]})
    report = DataQualityChecker().check_date_range(df, max_days_old=1).generate_report()
    assert report["passed"] is False
    assert report["checks"]["date_range"]["details"]["latest_date"] == str(old)


def test_date_range_details_include_cutoff():
    df = pd.DataFrame({"date": [date.today() - timedelta(days=5)]})
    report = DataQualityChecker().check_date_range(df, max_days_old=2).generate_report()
    details = report["checks"]["date_range"]["details"]
    assert "cutoff_date" in details
    assert "max_days_old" in details


def test_date_range_fails_missing_column():
    df = pd.DataFrame({"price": [100.0]})
    report = DataQualityChecker().check_date_range(df, date_col="date").generate_report()
    assert report["passed"] is False
    assert "error" in report["checks"]["date_range"]["details"]


def test_date_range_fails_empty_dataframe():
    df = pd.DataFrame({"date": pd.Series([], dtype="object")})
    report = DataQualityChecker().check_date_range(df).generate_report()
    assert report["passed"] is False


def test_date_range_custom_column_name():
    df = pd.DataFrame({"ts": [date.today()]})
    report = (
        DataQualityChecker()
        .check_date_range(df, max_days_old=0, date_col="ts")
        .generate_report()
    )
    assert report["passed"] is True


# ---------------------------------------------------------------------------
# check_amount_bounds
# ---------------------------------------------------------------------------


def test_amount_bounds_passes_all_in_range():
    df = pd.DataFrame({"amount": [10.0, 50.0, 99.9]})
    report = (
        DataQualityChecker()
        .check_amount_bounds(df, min_val=0.0, max_val=100.0)
        .generate_report()
    )
    assert report["passed"] is True
    assert report["checks"]["amount_bounds"]["details"]["violations"] == 0


def test_amount_bounds_fails_value_above_max():
    df = pd.DataFrame({"amount": [10.0, 5_000.0]})
    report = (
        DataQualityChecker()
        .check_amount_bounds(df, min_val=0.0, max_val=100.0)
        .generate_report()
    )
    assert report["passed"] is False
    assert report["checks"]["amount_bounds"]["details"]["violations"] == 1


def test_amount_bounds_fails_value_below_min():
    df = pd.DataFrame({"amount": [-5.0, 50.0]})
    report = (
        DataQualityChecker()
        .check_amount_bounds(df, min_val=0.0, max_val=100.0)
        .generate_report()
    )
    assert report["passed"] is False


def test_amount_bounds_details_include_actual_min_max():
    df = pd.DataFrame({"amount": [20.0, 80.0]})
    report = (
        DataQualityChecker()
        .check_amount_bounds(df, min_val=0.0, max_val=100.0)
        .generate_report()
    )
    details = report["checks"]["amount_bounds"]["details"]
    assert details["actual_min"] == pytest.approx(20.0)
    assert details["actual_max"] == pytest.approx(80.0)


def test_amount_bounds_fails_missing_column():
    df = pd.DataFrame({"price": [100.0]})
    report = (
        DataQualityChecker()
        .check_amount_bounds(df, min_val=0.0, max_val=200.0, amount_col="amount")
        .generate_report()
    )
    assert report["passed"] is False
    assert "error" in report["checks"]["amount_bounds"]["details"]


def test_amount_bounds_fails_empty_dataframe():
    df = pd.DataFrame({"amount": pd.Series([], dtype=float)})
    report = (
        DataQualityChecker()
        .check_amount_bounds(df, min_val=0.0, max_val=100.0)
        .generate_report()
    )
    assert report["passed"] is False


def test_amount_bounds_custom_column():
    df = pd.DataFrame({"close": [50.0, 100.0, 150.0]})
    report = (
        DataQualityChecker()
        .check_amount_bounds(df, min_val=0.0, max_val=200.0, amount_col="close")
        .generate_report()
    )
    assert report["passed"] is True


def test_amount_bounds_multiple_violations():
    df = pd.DataFrame({"amount": [-10.0, 50.0, 200.0, 300.0]})
    report = (
        DataQualityChecker()
        .check_amount_bounds(df, min_val=0.0, max_val=100.0)
        .generate_report()
    )
    assert report["checks"]["amount_bounds"]["details"]["violations"] == 3


# ---------------------------------------------------------------------------
# generate_report structure & chaining
# ---------------------------------------------------------------------------


def test_empty_checker_passes():
    report = DataQualityChecker().generate_report()
    assert report["passed"] is True
    assert report["summary"]["total"] == 0
    assert report["summary"]["passed"] == 0
    assert report["summary"]["failed"] == 0


def test_report_summary_counts_all_passing():
    df = pd.DataFrame({"date": [date.today()], "amount": [50.0]})
    report = (
        DataQualityChecker()
        .check_no_nulls(df, ["date", "amount"])
        .check_date_range(df, max_days_old=0)
        .check_amount_bounds(df, min_val=0.0, max_val=100.0)
        .generate_report()
    )
    assert report["summary"]["total"] == 3
    assert report["summary"]["passed"] == 3
    assert report["summary"]["failed"] == 0
    assert report["passed"] is True


def test_chained_one_fail_marks_report_failed():
    df = pd.DataFrame(
        {"date": [date.today() - timedelta(days=5)], "amount": [50.0]}
    )
    report = (
        DataQualityChecker()
        .check_no_nulls(df, ["date", "amount"])   # pass
        .check_date_range(df, max_days_old=1)      # fail
        .check_amount_bounds(df, 0.0, 100.0)       # pass
        .generate_report()
    )
    assert report["passed"] is False
    assert report["summary"]["failed"] == 1
    assert report["summary"]["passed"] == 2


def test_report_checks_dict_keyed_by_check_name():
    df = pd.DataFrame({"date": [date.today()], "amount": [5.0]})
    report = (
        DataQualityChecker()
        .check_no_nulls(df, ["date"])
        .check_date_range(df, max_days_old=0)
        .generate_report()
    )
    assert "no_nulls" in report["checks"]
    assert "date_range" in report["checks"]
    for v in report["checks"].values():
        assert "passed" in v
        assert "details" in v
