from __future__ import annotations

import asyncio
from datetime import date
from typing import Optional

import pandas as pd
from prophet import Prophet
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Forecast
from app.db.session import AsyncSessionLocal

logger = get_logger(__name__)

_MODEL_VERSION = "prophet-v1"


class SpendingForecaster:
    def __init__(self) -> None:
        self._models: dict[str, Prophet] = {}
        self._hist_avg: dict[str, float] = {}  # category → mean daily spend (for capping)

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def train_by_category(self, transactions_df: pd.DataFrame) -> None:
        """Fit one Prophet model per category using absolute daily spend."""
        df = transactions_df.copy()
        df["ds"] = pd.to_datetime(df["date"])
        df["y"] = df["amount"].astype(float).abs()

        for category, group in df.groupby("category"):
            sparse = group.groupby("ds")["y"].sum().reset_index()
            if len(sparse) < 2:
                logger.warning("skipping category — insufficient history", category=category)
                continue

            # Fill missing days with 0 — sparse non-zero-only data causes Prophet
            # to wildly overfit and extrapolate inflated forecasts.
            full_range = pd.date_range(sparse["ds"].min(), sparse["ds"].max(), freq="D")
            daily = (
                sparse.set_index("ds")
                .reindex(full_range, fill_value=0.0)
                .rename_axis("ds")
                .reset_index()
            )

            hist_avg = float(daily["y"].mean())
            self._hist_avg[str(category)] = hist_avg

            model = Prophet(
                growth="flat",              # no trend — prevents spurious up/down drift on sparse data
                yearly_seasonality=False,   # insufficient data (<1 yr) to fit yearly wave
                weekly_seasonality=True,
                daily_seasonality=False,
                changepoint_prior_scale=0.05,  # low flexibility — prevents overfitting small datasets
                interval_width=0.95,
            )
            model.fit(daily)
            self._models[str(category)] = model
            logger.info(
                "prophet fitted",
                category=category,
                rows=len(daily),
                hist_avg=round(hist_avg, 2),
            )

    # ------------------------------------------------------------------
    # Forecasting
    # ------------------------------------------------------------------

    def forecast(self, category: str, periods: int = 90) -> pd.DataFrame:
        """Return df[ds, yhat, yhat_lower, yhat_upper] for the next *periods* days."""
        if category not in self._models:
            raise KeyError(f"No model trained for category '{category}'")
        model = self._models[category]
        future = model.make_future_dataframe(periods=periods, freq="D")
        fc = model.predict(future)
        result = fc[["ds", "yhat", "yhat_lower", "yhat_upper"]].tail(periods).reset_index(drop=True)

        # Cap at 3× historical daily average to prevent exploding forecasts.
        hist_avg = self._hist_avg.get(category)
        if hist_avg is not None and hist_avg > 0:
            cap = hist_avg * 3
            result["yhat"] = result["yhat"].clip(lower=0, upper=cap)
            result["yhat_upper"] = result["yhat_upper"].clip(lower=0, upper=cap)
            result["yhat_lower"] = result["yhat_lower"].clip(lower=0)
        else:
            result["yhat"] = result["yhat"].clip(lower=0)
            result["yhat_lower"] = result["yhat_lower"].clip(lower=0)
            result["yhat_upper"] = result["yhat_upper"].clip(lower=0)

        return result

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def persist_forecasts(
        self,
        category: str,
        periods: int = 90,
        session: Optional[AsyncSession] = None,
    ) -> int:
        """Forecast *category* and write results to the Forecast table.

        Deletes existing future forecasts for the category before inserting new
        ones so the table always reflects the latest model run.
        Returns the number of rows written.
        """
        loop = asyncio.get_running_loop()
        forecast_df = await loop.run_in_executor(None, self.forecast, category, periods)

        own_session = session is None
        _session = AsyncSessionLocal() if own_session else session
        try:
            today = date.today()
            await _session.execute(
                delete(Forecast).where(
                    Forecast.category == category,
                    Forecast.forecast_date >= today,
                )
            )
            rows = [
                Forecast(
                    forecast_date=row["ds"].date(),
                    predicted_amount=round(float(max(row["yhat"], 0)), 2),
                    category=category,
                    confidence_lower=round(float(max(row["yhat_lower"], 0)), 2),
                    confidence_upper=round(float(max(row["yhat_upper"], 0)), 2),
                    model_version=_MODEL_VERSION,
                )
                for _, row in forecast_df.iterrows()
            ]
            _session.add_all(rows)
            await _session.commit()
            logger.info("forecasts persisted", category=category, rows=len(rows))
            return len(rows)
        except Exception:
            await _session.rollback()
            raise
        finally:
            if own_session:
                await _session.close()

    async def persist_all_forecasts(self, periods: int = 90) -> dict[str, int]:
        """Persist forecasts for every trained category. Returns {category: rows_written}."""
        results: dict[str, int] = {}
        for category in self._models:
            results[category] = await self.persist_forecasts(category, periods)
        return results
