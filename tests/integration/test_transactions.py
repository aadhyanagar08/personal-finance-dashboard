from __future__ import annotations

from datetime import date

import pytest

from app.db.models import Transaction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create(client, headers, **overrides) -> dict:
    body = {
        "date": str(date.today()),
        "amount": -25.00,
        "category": "Food",
        "description": "Test",
    }
    body.update(overrides)
    resp = await client.post("/api/v1/transactions", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------


async def test_list_no_token_returns_401(app_client):
    client, _ = app_client
    assert (await client.get("/api/v1/transactions")).status_code == 401


async def test_list_invalid_token_returns_401(app_client):
    client, _ = app_client
    resp = await client.get(
        "/api/v1/transactions",
        headers={"Authorization": "Bearer not.a.valid.jwt"},
    )
    assert resp.status_code == 401


async def test_create_no_token_returns_401(app_client):
    client, _ = app_client
    resp = await client.post(
        "/api/v1/transactions",
        json={"date": "2024-01-01", "amount": -10.0, "category": "Food"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def test_create_returns_201_and_fields(app_client, auth_headers):
    client, _ = app_client
    resp = await client.post(
        "/api/v1/transactions",
        json={"date": "2024-03-01", "amount": -42.50, "category": "Food", "description": "Lunch"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["category"] == "Food"
    assert body["is_anomaly"] is False
    assert "id" in body
    assert body["amount"] == "-42.5"


async def test_create_default_category_is_uncategorized(app_client, auth_headers):
    client, _ = app_client
    resp = await client.post(
        "/api/v1/transactions",
        json={"date": "2024-03-01", "amount": -10.00},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["category"] == "Uncategorized"


async def test_create_income_positive_amount(app_client, auth_headers):
    client, _ = app_client
    resp = await client.post(
        "/api/v1/transactions",
        json={"date": "2024-03-15", "amount": 3500.00, "category": "Income"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    assert float(resp.json()["amount"]) == pytest.approx(3500.00)


async def test_create_with_source_field(app_client, auth_headers):
    client, _ = app_client
    body = await _create(client, auth_headers, source="bank_import")
    assert body["source"] == "bank_import"


# ---------------------------------------------------------------------------
# List / pagination
# ---------------------------------------------------------------------------


async def test_list_empty_db(app_client, auth_headers):
    client, _ = app_client
    resp = await client.get("/api/v1/transactions", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []
    assert body["limit"] == 50
    assert body["offset"] == 0


async def test_list_returns_created_transactions(app_client, auth_headers):
    client, _ = app_client
    await _create(client, auth_headers, category="Food")
    await _create(client, auth_headers, category="Transport")
    body = (await client.get("/api/v1/transactions", headers=auth_headers)).json()
    assert body["total"] == 2
    assert len(body["items"]) == 2


async def test_pagination_limit_slices_results(app_client, auth_headers):
    client, _ = app_client
    for _ in range(5):
        await _create(client, auth_headers)

    resp = await client.get("/api/v1/transactions?limit=2&offset=0", headers=auth_headers)
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["total"] == 5


async def test_pagination_offset_returns_different_rows(app_client, auth_headers):
    client, _ = app_client
    for _ in range(6):
        await _create(client, auth_headers)

    page1 = (await client.get("/api/v1/transactions?limit=3&offset=0", headers=auth_headers)).json()
    page2 = (await client.get("/api/v1/transactions?limit=3&offset=3", headers=auth_headers)).json()
    ids1 = {item["id"] for item in page1["items"]}
    ids2 = {item["id"] for item in page2["items"]}
    assert ids1.isdisjoint(ids2)


async def test_pagination_last_page_has_remaining_rows(app_client, auth_headers):
    client, _ = app_client
    for _ in range(5):
        await _create(client, auth_headers)

    body = (await client.get("/api/v1/transactions?limit=3&offset=3", headers=auth_headers)).json()
    assert len(body["items"]) == 2
    assert body["total"] == 5


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


async def test_filter_by_category(app_client, auth_headers):
    client, _ = app_client
    await _create(client, auth_headers, category="Food")
    await _create(client, auth_headers, category="Transport")
    await _create(client, auth_headers, category="Food")

    body = (await client.get("/api/v1/transactions?category=Food", headers=auth_headers)).json()
    assert body["total"] == 2
    assert all(item["category"] == "Food" for item in body["items"])


async def test_filter_by_date_range(app_client, auth_headers):
    client, _ = app_client
    await _create(client, auth_headers, date="2024-01-10")
    await _create(client, auth_headers, date="2024-02-15")
    await _create(client, auth_headers, date="2024-03-20")

    body = (
        await client.get(
            "/api/v1/transactions?date_from=2024-01-01&date_to=2024-02-28",
            headers=auth_headers,
        )
    ).json()
    assert body["total"] == 2
    for item in body["items"]:
        assert item["date"] <= "2024-02-28"


async def test_filter_is_anomaly_false(app_client, auth_headers):
    client, _ = app_client
    await _create(client, auth_headers)  # is_anomaly=False by default

    body = (
        await client.get("/api/v1/transactions?is_anomaly=false", headers=auth_headers)
    ).json()
    assert body["total"] == 1
    assert body["items"][0]["is_anomaly"] is False


async def test_filter_is_anomaly_true_empty_when_none_flagged(app_client, auth_headers):
    client, _ = app_client
    await _create(client, auth_headers)

    body = (
        await client.get("/api/v1/transactions?is_anomaly=true", headers=auth_headers)
    ).json()
    assert body["total"] == 0


async def test_combined_category_and_date_filter(app_client, auth_headers):
    client, _ = app_client
    await _create(client, auth_headers, category="Food", date="2024-05-01")
    await _create(client, auth_headers, category="Transport", date="2024-05-10")
    await _create(client, auth_headers, category="Food", date="2024-06-01")

    body = (
        await client.get(
            "/api/v1/transactions?category=Food&date_from=2024-01-01&date_to=2024-05-31",
            headers=auth_headers,
        )
    ).json()
    assert body["total"] == 1
    assert body["items"][0]["category"] == "Food"
    assert body["items"][0]["date"] == "2024-05-01"


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


async def test_summary_income_and_expenses(app_client, auth_headers):
    client, _ = app_client
    await _create(client, auth_headers, amount=5000.00, category="Income", date="2024-06-01")
    await _create(client, auth_headers, amount=-200.00, category="Food", date="2024-06-15")
    await _create(client, auth_headers, amount=-100.00, category="Transport", date="2024-06-20")

    body = (
        await client.get(
            "/api/v1/transactions/summary?date_from=2024-06-01&date_to=2024-06-30",
            headers=auth_headers,
        )
    ).json()
    assert float(body["total_income"]) == pytest.approx(5000.00)
    assert float(body["total_expenses"]) == pytest.approx(-300.00)
    assert float(body["net_savings"]) == pytest.approx(4700.00)
    assert body["savings_rate"] == pytest.approx(0.94)


async def test_summary_no_income_yields_null_savings_rate(app_client, auth_headers):
    client, _ = app_client
    await _create(client, auth_headers, amount=-50.00, date="2024-07-01")

    body = (
        await client.get(
            "/api/v1/transactions/summary?date_from=2024-07-01&date_to=2024-07-31",
            headers=auth_headers,
        )
    ).json()
    assert body["savings_rate"] is None
    assert float(body["total_expenses"]) == pytest.approx(-50.00)
    assert float(body["total_income"]) == pytest.approx(0.0)


async def test_summary_invalid_date_order_returns_422(app_client, auth_headers):
    client, _ = app_client
    resp = await client.get(
        "/api/v1/transactions/summary?date_from=2024-12-01&date_to=2024-01-01",
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_summary_missing_params_returns_422(app_client, auth_headers):
    client, _ = app_client
    resp = await client.get("/api/v1/transactions/summary", headers=auth_headers)
    assert resp.status_code == 422


async def test_summary_empty_range_returns_zeros(app_client, auth_headers):
    client, _ = app_client
    # No transactions in range
    body = (
        await client.get(
            "/api/v1/transactions/summary?date_from=2020-01-01&date_to=2020-01-31",
            headers=auth_headers,
        )
    ).json()
    assert float(body["total_income"]) == pytest.approx(0.0)
    assert float(body["total_expenses"]) == pytest.approx(0.0)
    assert body["savings_rate"] is None


# ---------------------------------------------------------------------------
# Anomalies endpoint
# ---------------------------------------------------------------------------


async def test_anomalies_empty_when_none_flagged(app_client, auth_headers):
    client, _ = app_client
    await _create(client, auth_headers)  # normal transaction

    resp = await client.get("/api/v1/transactions/anomalies", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []


async def test_anomalies_returns_only_flagged_transactions(app_client, auth_headers):
    client, maker = app_client
    # Insert anomalous transaction directly (no API path to set is_anomaly=True)
    async with maker() as session:
        session.add(
            Transaction(
                date=date.today(),
                amount=-9999.99,
                category="Food",
                description="Anomalous charge",
                is_anomaly=True,
            )
        )
        await session.commit()

    # Also create a normal transaction via the API
    await _create(client, auth_headers)

    resp = await client.get("/api/v1/transactions/anomalies", headers=auth_headers)
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["is_anomaly"] is True
    assert float(body["items"][0]["amount"]) == pytest.approx(-9999.99)


async def test_anomalies_respects_category_filter(app_client, auth_headers):
    client, maker = app_client
    async with maker() as session:
        session.add_all([
            Transaction(date=date.today(), amount=-500.0, category="Food", is_anomaly=True),
            Transaction(date=date.today(), amount=-800.0, category="Transport", is_anomaly=True),
        ])
        await session.commit()

    body = (
        await client.get("/api/v1/transactions/anomalies?category=Food", headers=auth_headers)
    ).json()
    assert body["total"] == 1
    assert body["items"][0]["category"] == "Food"


async def test_anomalies_no_token_returns_401(app_client):
    client, _ = app_client
    assert (await client.get("/api/v1/transactions/anomalies")).status_code == 401
