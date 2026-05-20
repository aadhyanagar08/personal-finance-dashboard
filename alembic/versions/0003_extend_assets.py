"""extend assets with portfolio columns

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-20
"""
from collections.abc import Sequence
from typing import Optional, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Optional[str] = "0002"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


def upgrade() -> None:
    op.add_column("assets", sa.Column("exchange", sa.String(20)))
    op.add_column("assets", sa.Column("yf_symbol", sa.String(20)))
    op.add_column("assets", sa.Column("quantity", sa.Numeric(16, 6)))
    op.add_column("assets", sa.Column("market_price", sa.Numeric(16, 4)))
    op.add_column("assets", sa.Column("market_price_currency", sa.String(10), server_default="CAD"))
    op.add_column("assets", sa.Column("book_value_cad", sa.Numeric(16, 4)))
    op.add_column("assets", sa.Column("market_value_cad", sa.Numeric(16, 4)))
    op.add_column("assets", sa.Column("unrealized_return_cad", sa.Numeric(16, 4)))


def downgrade() -> None:
    for col in ("unrealized_return_cad", "market_value_cad", "book_value_cad",
                "market_price_currency", "market_price", "quantity", "yf_symbol", "exchange"):
        op.drop_column("assets", col)
