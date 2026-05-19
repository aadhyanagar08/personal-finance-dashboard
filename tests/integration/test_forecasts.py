from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.db.models import Forecast, Transaction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_forecasts(maker, category: str, n: int = 10) -> None:
    today = date.today()
    async with maker() as session:
        for i in range(n):
            session.add(
                Forecast(
                    forecast_date=today + timedelta(days=i),
                    predicted_amount=Decimal(str(100 + i * 5)),
                    category=category,
                    confidence_lower=Decimal(str(80 + i * 5)),
                    confidence_upper=Decimal(str(120 + i * 5)),
                    model_version="prophet-v1",
                )
            )
        await session.commit()


async def _insert_transaction(maker, category: str) -> None:
    async with maker() as session:
        session.add(
            Transaction(
                date=date.today(),
                amount=Decimal("-50.00"),
                category=category,
            )
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------


async def test_forecast_category_no_token_returns_401(app_client):
    client, _ = app_client
    assert (await client.get("/api/v1/forecasts/Food")).status_code == 401


async def test_forecast_summary_no_token_returns_401(app_client):
    client, _ = app_client
    assert (await client.get("/api/v1/forecasts/summary")).status_code == 401


async def test_forecast_refresh_no_token_returns_401(app_client):
    client, _ = app_client
    assert (await client.post("/api/v1/forecasts/refresh")).status_code == 401


# ---------------------------------------------------------------------------
# GET /forecasts/summary
# ---------------------------------------------------------------------------


async def test_forecast_summary_empty_returns_empty_list(app_client, auth_headers):
    client, _ = app_client
    resp = await client.get("/api/v1/forecasts/summary", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["categories"] == []
    assert "date_from" in body
    assert "date_to" in body


async def test_forecast_summary_returns_categories_with_data(app_client, auth_headers):
    client, maker = app_client
    await _insert_forecasts(maker, "Food", n=15)
    await _insert_forecasts(maker, "Transport", n=15)

    resp = await client.get("/api/v1/forecasts/summary", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    categories = {c["category"] for c in body["categories"]}
    assert "Food" in categories
    assert "Transport" in categories


async def test_forecast_summary_only_includes_future_dates(app_client, auth_headers):
    client, maker = app_client
    # Insert past forecast (should be excluded from 30-day window)
    async with maker() as session:
        session.add(
            Forecast(
                forecast_date=date.today() - timedelta(days=60),
                predicted_amount=Decimal("200.00"),
                category="OldCategory",
                model_version="v1",
            )
        )
        await session.commit()

    await _insert_forecasts(maker, "NewCategory", n=5)

    resp = await client.get("/api/v1/forecasts/summary", headers=auth_headers)
    body = resp.json()
    categories = {c["category"] for c in body["categories"]}
    assert "OldCategory" not in categories
    assert "NewCategory" in categories


async def test_forecast_summary_projected_spend_is_sum(app_client, auth_headers):
    client, maker = app_client
    # Insert 10 forecasts of $100 each — should sum to $1000
    async with maker() as session:
        today = date.today()
        for i in range(10):
            session.add(
                Forecast(
                    forecast_date=today + timedelta(days=i),
                    predicted_amount=Decimal("100.00"),
                    category="Shopping",
                    model_version="v1",
                )
            )
        await session.commit()

    resp = await client.get("/api/v1/forecasts/summary", headers=auth_headers)
    body = resp.json()
    shopping = next(c for c in body["categories"] if c["category"] == "Shopping")
    assert float(shopping["projected_30d_spend"]) == pytest.approx(1000.00)


# ---------------------------------------------------------------------------
# GET /forecasts/{category}
# ---------------------------------------------------------------------------


async def test_category_forecast_not_found_returns_404(app_client, auth_headers):
    client, _ = app_client
    resp = await client.get("/api/v1/forecasts/UnknownCategory", headers=auth_headers)
    assert resp.status_code == 404
    assert "refresh" in resp.json()["detail"].lower()


async def test_category_forecast_returns_points(app_client, auth_headers):
    client, maker = app_client
    await _insert_forecasts(maker, "Food", n=30)

    resp = await client.get("/api/v1/forecasts/Food", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["category"] == "Food"
    assert body["periods"] == 30
    assert len(body["points"]) == 30


async def test_category_forecast_point_schema(app_client, auth_headers):
    client, maker = app_client
    await _insert_forecasts(maker, "Transport", n=5)

    body = (await client.get("/api/v1/forecasts/Transport", headers=auth_headers)).json()
    point = body["points"][0]
    for field in ("forecast_date", "predicted_amount", "confidence_lower", "confidence_upper"):
        assert field in point


async def test_category_forecast_custom_periods(app_client, auth_headers):
    client, maker = app_client
    await _insert_forecasts(maker, "Health", n=60)

    body = (
        await client.get("/api/v1/forecasts/Health?periods=30", headers=auth_headers)
    ).json()
    assert body["periods"] == 30
    assert len(body["points"]) == 30


async def test_category_forecast_invalid_periods_returns_422(app_client, auth_headers):
    client, _ = app_client
    resp = await client.get("/api/v1/forecasts/Food?periods=0", headers=auth_headers)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /forecasts/refresh
# ---------------------------------------------------------------------------


async def test_refresh_returns_202(app_client, auth_headers):
    client, _ = app_client
    resp = await client.post("/api/v1/forecasts/refresh", headers=auth_headers)
    assert resp.status_code == 202


async def test_refresh_response_schema(app_client, auth_headers):
    client, _ = app_client
    body = (await client.post("/api/v1/forecasts/refresh", headers=auth_headers)).json()
    assert body["status"] == "queued"
    assert "categories_queued" in body
    assert isinstance(body["categories_queued"], int)


async def test_refresh_reports_category_count(app_client, auth_headers):
    client, maker = app_client
    await _insert_transaction(maker, "Food")
    await _insert_transaction(maker, "Transport")

    body = (await client.post("/api/v1/forecasts/refresh", headers=auth_headers)).json()
    assert body["categories_queued"] == 2
