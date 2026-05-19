from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_date", "date"),
        Index("ix_transactions_category", "category"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    category = Column(String(100), nullable=False)
    description = Column(Text)
    source = Column(String(100))
    is_anomaly = Column(Boolean, nullable=False, default=False, server_default="false")
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Forecast(Base):
    __tablename__ = "forecasts"
    __table_args__ = (
        Index("ix_forecasts_forecast_date", "forecast_date"),
        Index("ix_forecasts_category", "category"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    forecast_date = Column(Date, nullable=False)
    predicted_amount = Column(Numeric(12, 2), nullable=False)
    category = Column(String(100), nullable=False)
    confidence_lower = Column(Numeric(12, 2))
    confidence_upper = Column(Numeric(12, 2))
    model_version = Column(String(50))
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Asset(Base):
    __tablename__ = "assets"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, unique=True)
    name = Column(String(255))
    asset_type = Column(String(50))
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    price_history: list["PriceHistory"] = relationship(
        "PriceHistory", back_populates="asset", cascade="all, delete-orphan"
    )


class PriceHistory(Base):
    __tablename__ = "price_history"
    __table_args__ = (
        UniqueConstraint("asset_id", "date", name="uq_price_history_asset_date"),
        Index("ix_price_history_asset_id", "asset_id"),
        Index("ix_price_history_date", "date"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    asset_id = Column(
        BigInteger, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    date = Column(Date, nullable=False)
    open = Column(Numeric(16, 4))
    high = Column(Numeric(16, 4))
    low = Column(Numeric(16, 4))
    close = Column(Numeric(16, 4), nullable=False)
    volume = Column(BigInteger)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    asset: "Asset" = relationship("Asset", back_populates="price_history")
