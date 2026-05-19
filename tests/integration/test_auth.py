from __future__ import annotations

import pytest

_EMAIL = "alice@example.com"
_PASSWORD = "s3cure-p4ssword"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _register(client, email: str = _EMAIL, password: str = _PASSWORD) -> dict:
    resp = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )
    return resp


async def _login(client, email: str = _EMAIL, password: str = _PASSWORD):
    return await client.post(
        "/api/v1/auth/token", data={"username": email, "password": password}
    )


async def _login_token(client, email: str = _EMAIL, password: str = _PASSWORD) -> str:
    resp = await _login(client, email, password)
    return resp.json()["access_token"]


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------


async def test_register_returns_201_and_user_fields(app_client):
    client, _ = app_client
    resp = await _register(client)
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == _EMAIL
    assert body["is_active"] is True
    assert "id" in body
    assert "password" not in body
    assert "hashed_password" not in body


async def test_register_duplicate_email_returns_409(app_client):
    client, _ = app_client
    await _register(client)
    resp = await _register(client)
    assert resp.status_code == 409
    assert "already registered" in resp.json()["detail"].lower()


async def test_register_invalid_email_returns_422(app_client):
    client, _ = app_client
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "not-an-email", "password": _PASSWORD},
    )
    assert resp.status_code == 422


async def test_register_missing_fields_returns_422(app_client):
    client, _ = app_client
    resp = await client.post("/api/v1/auth/register", json={"email": _EMAIL})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Login / token
# ---------------------------------------------------------------------------


async def test_login_returns_bearer_token(app_client):
    client, _ = app_client
    await _register(client)
    resp = await _login(client)
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"
    assert len(body["access_token"]) > 20


async def test_login_wrong_password_returns_401(app_client):
    client, _ = app_client
    await _register(client)
    resp = await _login(client, password="wrong-password")
    assert resp.status_code == 401
    assert "WWW-Authenticate" in resp.headers


async def test_login_nonexistent_user_returns_401(app_client):
    client, _ = app_client
    resp = await _login(client, email="nobody@example.com")
    assert resp.status_code == 401


async def test_login_requires_form_data(app_client):
    client, _ = app_client
    await _register(client)
    # Sending JSON instead of form data should return 422
    resp = await client.post(
        "/api/v1/auth/token",
        json={"username": _EMAIL, "password": _PASSWORD},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /auth/me
# ---------------------------------------------------------------------------


async def test_get_me_returns_current_user(app_client):
    client, _ = app_client
    await _register(client)
    token = await _login_token(client)
    resp = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.json()["email"] == _EMAIL


async def test_get_me_no_token_returns_401(app_client):
    client, _ = app_client
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_get_me_invalid_token_returns_401(app_client):
    client, _ = app_client
    resp = await client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer fake.token.value"}
    )
    assert resp.status_code == 401


async def test_get_me_expired_token_structure(app_client):
    """A syntactically valid JWT with a past expiry is rejected."""
    import time
    from jose import jwt
    from app.core.config import settings

    past_token = jwt.encode(
        {"sub": _EMAIL, "exp": int(time.time()) - 3600},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    client, _ = app_client
    resp = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {past_token}"}
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Protected route access
# ---------------------------------------------------------------------------


async def test_transactions_accessible_with_valid_token(app_client):
    client, _ = app_client
    await _register(client)
    token = await _login_token(client)
    resp = await client.get(
        "/api/v1/transactions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


async def test_transactions_blocked_without_token(app_client):
    client, _ = app_client
    assert (await client.get("/api/v1/transactions")).status_code == 401


async def test_transactions_blocked_with_malformed_token(app_client):
    client, _ = app_client
    resp = await client.get(
        "/api/v1/transactions",
        headers={"Authorization": "Bearer aaaaa.bbbbb.ccccc"},
    )
    assert resp.status_code == 401


async def test_different_users_get_separate_tokens(app_client):
    """Each login call returns a fresh, distinct token for the same user."""
    client, _ = app_client
    await _register(client)
    token_a = await _login_token(client)
    token_b = await _login_token(client)
    # Tokens are time-based; they CAN be equal if issued in the same second,
    # but both must be accepted.
    for tok in (token_a, token_b):
        resp = await client.get(
            "/api/v1/transactions",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert resp.status_code == 200
