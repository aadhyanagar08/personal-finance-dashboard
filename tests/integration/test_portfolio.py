"""
Portfolio endpoint integration tests.

For tests that would normally call yfinance, we patch the live-price helper
so the tests run offline and deterministically.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.db.models import Asset, PriceHistory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_asset(maker, ticker: str = "AAPL", name: str = "Apple Inc") -> int:
    async with maker() as session:
        asset = Asset(ticker=ticker, name=name, asset_type="stock")
        session.add(asset)
        await session.commit()
        await session.refresh(asset)
        return asset.id


async def _insert_price_history(maker, asset_id: int, days: int = 32) -> None:
    async with maker() as session:
        today = date.today()
        for i in range(days, 0, -1):
            d = today - timedelta(days=i)
            session.add(
                PriceHistory(
                    asset_id=asset_id,
                    date=d,
                    open=Decimal("100.00"),
                    high=Decimal("105.00"),
                    low=Decimal("99.00"),
                    close=Decimal(str(100 + i * 0.1)),
                    volume=1_000_000,
                )
            )
        await session.commit()


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------


async def test_portfolio_no_token_returns_401(app_client):
    client, _ = app_client
    assert (await client.get("/api/v1/portfolio")).status_code == 401


async def test_portfolio_history_no_token_returns_401(app_client):
    client, _ = app_client
    assert (await client.get("/api/v1/portfolio/history")).status_code == 401


async def test_portfolio_metrics_no_token_returns_401(app_client):
    client, _ = app_client
    assert (await client.get("/api/v1/portfolio/metrics")).status_code == 401


# ---------------------------------------------------------------------------
# GET /portfolio  (empty)
# ---------------------------------------------------------------------------


async def test_portfolio_empty_no_assets(app_client, auth_headers):
    client, _ = app_client
    resp = await client.get("/api/v1/portfolio", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["holdings"] == []
    assert body["total_assets"] == 0


# ---------------------------------------------------------------------------
# GET /portfolio  (with live-price mock)
# ---------------------------------------------------------------------------


async def test_portfolio_with_assets_calls_yfinance(app_client, auth_headers):
    client, maker = app_client
    await _insert_asset(maker, "AAPL", "Apple Inc")

    mock_info = MagicMock()
    mock_info.last_price = 175.50
    mock_info.previous_close = 173.00

    with patch("app.api.v1.endpoints.portfolio.yf.Ticker") as mock_ticker:
        mock_ticker.return_value.fast_info = mock_info
        resp = await client.get("/api/v1/portfolio", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_assets"] == 1
    holding = body["holdings"][0]
    assert holding["ticker"] == "AAPL"
    assert holding["current_price"] == pytest.approx(175.50)
    assert holding["daily_change"] == pytest.approx(2.50)
    assert holding["daily_change_pct"] == pytest.approx(2.50 / 173.00 * 100)


async def test_portfolio_yfinance_failure_graceful(app_client, auth_headers):
    """A yfinance exception returns null prices instead of a 500."""
    client, maker = app_client
    await _insert_asset(maker, "FAIL")

    with patch("app.api.v1.endpoints.portfolio.yf.Ticker", side_effect=Exception("network error")):
        resp = await client.get("/api/v1/portfolio", headers=auth_headers)

    assert resp.status_code == 200
    holding = resp.json()["holdings"][0]
    assert holding["current_price"] is None
    assert holding["daily_change"] is None


async def test_portfolio_multiple_assets(app_client, auth_headers):
    client, maker = app_client
    await _insert_asset(maker, "AAPL")
    await _insert_asset(maker, "MSFT", "Microsoft")

    mock_info = MagicMock()
    mock_info.last_price = 150.0
    mock_info.previous_close = 148.0

    with patch("app.api.v1.endpoints.portfolio.yf.Ticker") as mock_ticker:
        mock_ticker.return_value.fast_info = mock_info
        resp = await client.get("/api/v1/portfolio", headers=auth_headers)

    assert resp.json()["total_assets"] == 2


# ---------------------------------------------------------------------------
# GET /portfolio/history
# ---------------------------------------------------------------------------


async def test_portfolio_history_empty_no_assets(app_client, auth_headers):
    client, _ = app_client
    resp = await client.get("/api/v1/portfolio/history", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["assets"] == []


async def test_portfolio_history_returns_ohlcv(app_client, auth_headers):
    client, maker = app_client
    asset_id = await _insert_asset(maker, "AAPL")
    await _insert_price_history(maker, asset_id, days=10)

    resp = await client.get("/api/v1/portfolio/history?days=30", headers=auth_headers)
    assert resp.status_code == 200
    assets = resp.json()["assets"]
    assert len(assets) == 1
    assert assets[0]["ticker"] == "AAPL"
    assert len(assets[0]["history"]) == 10

    point = assets[0]["history"][0]
    for field in ("date", "open", "high", "low", "close", "volume"):
        assert field in point


async def test_portfolio_history_days_param_filters(app_client, auth_headers):
    client, maker = app_client
    asset_id = await _insert_asset(maker, "SPY")
    await _insert_price_history(maker, asset_id, days=20)

    resp_short = await client.get("/api/v1/portfolio/history?days=5", headers=auth_headers)
    resp_long = await client.get("/api/v1/portfolio/history?days=30", headers=auth_headers)

    short_len = len(resp_short.json()["assets"][0]["history"])
    long_len = len(resp_long.json()["assets"][0]["history"])
    assert long_len > short_len


async def test_portfolio_history_invalid_days_returns_422(app_client, auth_headers):
    client, _ = app_client
    resp = await client.get("/api/v1/portfolio/history?days=0", headers=auth_headers)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /portfolio/metrics
# ---------------------------------------------------------------------------


async def test_portfolio_metrics_empty_no_assets(app_client, auth_headers):
    client, _ = app_client
    resp = await client.get("/api/v1/portfolio/metrics", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_value"] is None
    assert body["daily_change"] is None


async def test_portfolio_metrics_no_price_history(app_client, auth_headers):
    client, maker = app_client
    await _insert_asset(maker, "AAPL")
    # No price history rows

    resp = await client.get("/api/v1/portfolio/metrics", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_value"] is None


async def test_portfolio_metrics_with_price_history(app_client, auth_headers):
    client, maker = app_client
    asset_id = await _insert_asset(maker, "AAPL")
    await _insert_price_history(maker, asset_id, days=32)

    resp = await client.get("/api/v1/portfolio/metrics", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()

    assert body["total_value"] is not None
    assert body["daily_change"] is not None
    assert body["return_30d"] is not None
    assert body["volatility_30d"] is not None
    assert body["sharpe_ratio"] is not None


async def test_portfolio_metrics_response_schema(app_client, auth_headers):
    client, _ = app_client
    resp = await client.get("/api/v1/portfolio/metrics", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    for key in ("total_value", "daily_change", "daily_change_pct", "return_30d", "volatility_30d", "sharpe_ratio"):
        assert key in body
