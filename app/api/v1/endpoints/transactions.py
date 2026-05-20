from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.limiter import limiter
from app.core.security import verify_token
from app.db.models import Transaction
from app.db.session import get_db
from app.ml.categorizer import TransactionCategorizer, categorize_pending_transactions

router = APIRouter(
    prefix="/transactions",
    tags=["transactions"],
    dependencies=[Depends(verify_token)],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TransactionOut(BaseModel):
    id: int
    date: date
    amount: Decimal
    category: str
    description: Optional[str]
    source: Optional[str]
    is_anomaly: bool

    model_config = {"from_attributes": True}


class TransactionCreate(BaseModel):
    date: date
    amount: Decimal = Field(..., description="Positive = income, negative = expense")
    description: Optional[str] = None
    source: Optional[str] = None
    category: str = "Uncategorized"


class PaginatedTransactions(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[TransactionOut]


class TransactionSummary(BaseModel):
    date_from: date
    date_to: date
    total_income: Decimal
    total_expenses: Decimal
    net_savings: Decimal
    savings_rate: Optional[float] = Field(
        None, description="Net savings / total income, null when income is zero"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_filters(
    date_from: Optional[date],
    date_to: Optional[date],
    category: Optional[str],
    is_anomaly: Optional[bool],
) -> list:
    filters = []
    if date_from:
        filters.append(Transaction.date >= date_from)
    if date_to:
        filters.append(Transaction.date <= date_to)
    if category:
        filters.append(Transaction.category == category)
    if is_anomaly is not None:
        filters.append(Transaction.is_anomaly == is_anomaly)
    return filters


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=PaginatedTransactions,
    summary="List transactions",
    description="Paginated transaction list, optionally filtered by date range, category, or anomaly flag.",
)
@limiter.limit("1000/minute")
async def list_transactions(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    category: Optional[str] = None,
    is_anomaly: Optional[bool] = None,
) -> PaginatedTransactions:
    filters = _build_filters(date_from, date_to, category, is_anomaly)
    where = and_(*filters) if filters else True

    total_result = await db.execute(
        select(func.count()).select_from(Transaction).where(where)
    )
    total = total_result.scalar_one()

    rows_result = await db.execute(
        select(Transaction).where(where).order_by(Transaction.date.desc()).limit(limit).offset(offset)
    )
    items = rows_result.scalars().all()

    return PaginatedTransactions(
        total=total,
        limit=limit,
        offset=offset,
        items=[TransactionOut.model_validate(t) for t in items],
    )


@router.post(
    "",
    response_model=TransactionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create transaction",
    description="Create a new transaction. If no category is supplied the record is saved as 'Uncategorized' and a background task auto-categorizes it.",
)
@limiter.limit("1000/minute")
async def create_transaction(
    request: Request,
    payload: TransactionCreate,
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TransactionOut:
    txn = Transaction(
        date=payload.date,
        amount=payload.amount,
        category=payload.category,
        description=payload.description,
        source=payload.source,
    )
    db.add(txn)
    await db.commit()
    await db.refresh(txn)

    if payload.category == "Uncategorized":
        categorizer = TransactionCategorizer()
        try:
            categorizer.load_model()
            background_tasks.add_task(categorize_pending_transactions, categorizer)
        except Exception:
            pass  # model not yet trained; skip categorization

    return TransactionOut.model_validate(txn)


@router.get(
    "/summary",
    response_model=TransactionSummary,
    summary="Transaction summary",
    description="Returns total income, total expenses, net savings, and savings rate for a date range.",
)
@limiter.limit("1000/minute")
async def transaction_summary(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    date_from: date = Query(..., description="Start of date range (inclusive)"),
    date_to: date = Query(..., description="End of date range (inclusive)"),
) -> TransactionSummary:
    if date_from > date_to:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="date_from must be ≤ date_to")

    result = await db.execute(
        select(
            func.coalesce(
                func.sum(Transaction.amount).filter(Transaction.amount > 0), Decimal("0")
            ).label("income"),
            func.coalesce(
                func.sum(Transaction.amount).filter(Transaction.amount < 0), Decimal("0")
            ).label("expenses"),
        ).where(and_(Transaction.date >= date_from, Transaction.date <= date_to))
    )
    row = result.one()
    income = Decimal(str(row.income))
    expenses = Decimal(str(row.expenses))
    net = income + expenses  # expenses are negative
    savings_rate = float(net / income) if income > 0 else None

    return TransactionSummary(
        date_from=date_from,
        date_to=date_to,
        total_income=income,
        total_expenses=expenses,
        net_savings=net,
        savings_rate=savings_rate,
    )


@router.get(
    "/anomalies",
    response_model=PaginatedTransactions,
    summary="List anomalous transactions",
    description="Returns only transactions flagged as anomalies, with the same pagination and date filters.",
)
@limiter.limit("1000/minute")
async def list_anomalies(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    category: Optional[str] = None,
) -> PaginatedTransactions:
    filters = _build_filters(date_from, date_to, category, is_anomaly=True)
    where = and_(*filters)

    total_result = await db.execute(
        select(func.count()).select_from(Transaction).where(where)
    )
    total = total_result.scalar_one()

    rows_result = await db.execute(
        select(Transaction).where(where).order_by(Transaction.date.desc()).limit(limit).offset(offset)
    )
    items = rows_result.scalars().all()

    return PaginatedTransactions(
        total=total,
        limit=limit,
        offset=offset,
        items=[TransactionOut.model_validate(t) for t in items],
    )
