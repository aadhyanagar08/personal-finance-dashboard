"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-18

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # transactions                                                         #
    # ------------------------------------------------------------------ #
    op.create_table(
        "transactions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("source", sa.String(100)),
        sa.Column(
            "is_anomaly",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_transactions_date", "transactions", ["date"])
    op.create_index("ix_transactions_category", "transactions", ["category"])

    # ------------------------------------------------------------------ #
    # forecasts                                                            #
    # ------------------------------------------------------------------ #
    op.create_table(
        "forecasts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("forecast_date", sa.Date(), nullable=False),
        sa.Column("predicted_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("confidence_lower", sa.Numeric(12, 2)),
        sa.Column("confidence_upper", sa.Numeric(12, 2)),
        sa.Column("model_version", sa.String(50)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_forecasts_forecast_date", "forecasts", ["forecast_date"])
    op.create_index("ix_forecasts_category", "forecasts", ["category"])

    # ------------------------------------------------------------------ #
    # assets                                                               #
    # ------------------------------------------------------------------ #
    op.create_table(
        "assets",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("name", sa.String(255)),
        sa.Column("asset_type", sa.String(50)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticker", name="uq_assets_ticker"),
    )

    # ------------------------------------------------------------------ #
    # price_history                                                        #
    # ------------------------------------------------------------------ #
    op.create_table(
        "price_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("asset_id", sa.BigInteger(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(16, 4)),
        sa.Column("high", sa.Numeric(16, 4)),
        sa.Column("low", sa.Numeric(16, 4)),
        sa.Column("close", sa.Numeric(16, 4), nullable=False),
        sa.Column("volume", sa.BigInteger()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"], ["assets.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "asset_id", "date", name="uq_price_history_asset_date"
        ),
    )
    op.create_index("ix_price_history_asset_id", "price_history", ["asset_id"])
    op.create_index("ix_price_history_date", "price_history", ["date"])


def downgrade() -> None:
    op.drop_index("ix_price_history_date", table_name="price_history")
    op.drop_index("ix_price_history_asset_id", table_name="price_history")
    op.drop_table("price_history")
    op.drop_table("assets")
    op.drop_index("ix_forecasts_category", table_name="forecasts")
    op.drop_index("ix_forecasts_forecast_date", table_name="forecasts")
    op.drop_table("forecasts")
    op.drop_index("ix_transactions_category", table_name="transactions")
    op.drop_index("ix_transactions_date", table_name="transactions")
    op.drop_table("transactions")
