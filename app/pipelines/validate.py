from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd

from app.core.logging import get_logger

logger = get_logger(__name__)

CheckResult = dict[str, Any]


@dataclass
class DataQualityChecker:
    """Chainable data-quality checker that accumulates results for a final report.

    Usage::

        report = (
            DataQualityChecker()
            .check_no_nulls(df, ["date", "close"])
            .check_date_range(df, max_days_old=3)
            .check_amount_bounds(df, min_val=0.0, max_val=1_000_000.0, amount_col="close")
            .generate_report()
        )
    """

    _results: list[CheckResult] = field(default_factory=list, init=False, repr=False)

    # ------------------------------------------------------------------
    # Individual checks — each returns self for chaining
    # ------------------------------------------------------------------

    def check_no_nulls(
        self,
        df: pd.DataFrame,
        required_cols: list[str],
    ) -> "DataQualityChecker":
        """Fail if any required column is absent from the DataFrame or contains NaN."""
        missing_cols = [c for c in required_cols if c not in df.columns]
        null_counts = {
            c: int(df[c].isna().sum())
            for c in required_cols
            if c in df.columns
        }
        passed = not missing_cols and all(v == 0 for v in null_counts.values())
        self._results.append({
            "check": "no_nulls",
            "passed": passed,
            "details": {
                "required_cols": required_cols,
                "missing_cols": missing_cols,
                "null_counts": {k: v for k, v in null_counts.items() if v > 0},
            },
        })
        logger.debug("check_no_nulls", passed=passed, null_counts=null_counts)
        return self

    def check_date_range(
        self,
        df: pd.DataFrame,
        max_days_old: int = 1,
        date_col: str = "date",
    ) -> "DataQualityChecker":
        """Fail if the most recent date in *date_col* is older than *max_days_old* days."""
        if date_col not in df.columns or df.empty:
            self._results.append({
                "check": "date_range",
                "passed": False,
                "details": {
                    "error": f"column '{date_col}' missing or DataFrame is empty"
                },
            })
            return self

        latest: date = pd.to_datetime(df[date_col]).dt.date.max()
        cutoff = date.today() - timedelta(days=max_days_old)
        passed = latest >= cutoff
        self._results.append({
            "check": "date_range",
            "passed": passed,
            "details": {
                "date_col": date_col,
                "latest_date": str(latest),
                "cutoff_date": str(cutoff),
                "max_days_old": max_days_old,
            },
        })
        logger.debug("check_date_range", passed=passed, latest=str(latest), cutoff=str(cutoff))
        return self

    def check_amount_bounds(
        self,
        df: pd.DataFrame,
        min_val: float,
        max_val: float,
        amount_col: str = "amount",
    ) -> "DataQualityChecker":
        """Fail if any value in *amount_col* falls outside [min_val, max_val]."""
        if amount_col not in df.columns or df.empty:
            self._results.append({
                "check": "amount_bounds",
                "passed": False,
                "details": {
                    "error": f"column '{amount_col}' missing or DataFrame is empty"
                },
            })
            return self

        col = pd.to_numeric(df[amount_col], errors="coerce")
        violations = int(((col < min_val) | (col > max_val)).sum())
        passed = violations == 0
        self._results.append({
            "check": "amount_bounds",
            "passed": passed,
            "details": {
                "amount_col": amount_col,
                "min_val": min_val,
                "max_val": max_val,
                "violations": violations,
                "actual_min": float(col.min()),
                "actual_max": float(col.max()),
            },
        })
        logger.debug(
            "check_amount_bounds",
            passed=passed,
            violations=violations,
            actual_min=float(col.min()),
            actual_max=float(col.max()),
        )
        return self

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------

    def generate_report(self) -> dict[str, Any]:
        """Return a structured pass/fail report for all checks run so far."""
        passed_count = sum(1 for r in self._results if r["passed"])
        return {
            "passed": all(r["passed"] for r in self._results),
            "summary": {
                "total": len(self._results),
                "passed": passed_count,
                "failed": len(self._results) - passed_count,
            },
            "checks": {
                r["check"]: {"passed": r["passed"], "details": r["details"]}
                for r in self._results
            },
        }
