from __future__ import annotations

import random
from datetime import date, timedelta
from typing import AsyncGenerator

import pandas as pd
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token
from app.db.models import Base
from app.db.session import get_db
from app.main import app

# ---------------------------------------------------------------------------
# DB / client fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def app_client() -> AsyncGenerator[tuple[AsyncClient, async_sessionmaker], None]:
    """
    Yields (AsyncClient, session_maker) backed by a fresh in-memory SQLite DB.

    StaticPool ensures every session — whether opened by the HTTP handler or
    directly in the test — uses the same underlying connection, so committed
    data is immediately visible across both.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, maker

    app.dependency_overrides.pop(get_db, None)
    await engine.dispose()


# ---------------------------------------------------------------------------
# ML data fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_transactions_df() -> pd.DataFrame:
    """120 days of synthetic Food + Transport spend, deterministic (seed=42)."""
    rng = random.Random(42)
    base = date.today() - timedelta(days=120)
    rows = []
    for i in range(120):
        day = base + timedelta(days=i)
        rows.append(
            {
                "date": day,
                "amount": round(40.0 + rng.gauss(0, 6), 2),
                "category": "Food",
                "description": "Groceries",
            }
        )
        rows.append(
            {
                "date": day,
                "amount": round(25.0 + rng.gauss(0, 4), 2),
                "category": "Transport",
                "description": "Gas",
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Auth fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_token() -> str:
    return create_access_token({"sub": "testuser@example.com"})


@pytest.fixture
def auth_headers(auth_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth_token}"}
