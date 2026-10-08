"""`/api/expenses` (contracts/api-endpoints.md#expenses). The CSV export is a stub until F10."""

import datetime as dt

from fastapi import APIRouter, Depends, Query, Response

from app.api.errors import error_responses
from app.api.schemas import (
    Expense,
    ExpenseCreate,
    ExpenseUpdate,
    LineItemCategory,
    ReviewStatus,
)
from app.errors import NotImplementedYet
from app.services.dependencies import get_expense_service
from app.services.expenses import ExpenseService

router = APIRouter(prefix="/expenses", tags=["expenses"])


@router.get("", response_model=list[Expense], summary="List expenses, newest date first")
def list_expenses(
    review_status: ReviewStatus | None = None,
    confirmed: bool | None = None,
    from_: dt.date | None = Query(None, alias="from", description="inclusive"),
    to: dt.date | None = Query(None, description="inclusive"),
    category: LineItemCategory | None = Query(
        None, description="expenses with at least one item in this category"
    ),
    has_receipt: bool | None = Query(
        None, description="false: only expenses without a receipt (manual); true: with one"
    ),
    service: ExpenseService = Depends(get_expense_service),
) -> list[Expense]:
    views = service.list(
        review_status=review_status,
        confirmed=confirmed,
        date_from=from_,
        date_to=to,
        category=category,
        has_receipt=has_receipt,
    )
    return [Expense.model_validate(view, from_attributes=True) for view in views]


@router.post(
    "",
    response_model=Expense,
    status_code=201,
    summary="Create a manual expense, optionally for a failed receipt",
    responses=error_responses(404, 409),
)
def create_expense(
    body: ExpenseCreate, service: ExpenseService = Depends(get_expense_service)
) -> Expense:
    return Expense.model_validate(service.create(body), from_attributes=True)


# Declared before /{id:int}; the int converter also keeps `export.csv` from matching an id route.
@router.get(
    "/export.csv",
    response_class=Response,
    summary="Confirmed expenses as CSV, one row per line item",
    responses={
        200: {
            "description": "CSV file (contracts/csv-export.md)",
            "content": {"text/csv": {"schema": {"type": "string"}}},
        }
    },
)
def export_csv(
    from_: dt.date | None = Query(None, alias="from", description="inclusive"),
    to: dt.date | None = Query(None, description="inclusive"),
) -> Response:
    raise NotImplementedYet("F10")


@router.get(
    "/{id:int}", response_model=Expense, summary="Get an expense", responses=error_responses(404)
)
def get_expense(id: int, service: ExpenseService = Depends(get_expense_service)) -> Expense:
    return Expense.model_validate(service.get(id), from_attributes=True)


@router.patch(
    "/{id:int}",
    response_model=Expense,
    summary="Edit an expense; a confirmed expense becomes unconfirmed",
    responses=error_responses(404),
)
def update_expense(
    id: int, body: ExpenseUpdate, service: ExpenseService = Depends(get_expense_service)
) -> Expense:
    return Expense.model_validate(service.update(id, body), from_attributes=True)


@router.post(
    "/{id:int}/confirm",
    response_model=Expense,
    summary="Confirm an expense",
    responses=error_responses(404),
)
def confirm_expense(id: int, service: ExpenseService = Depends(get_expense_service)) -> Expense:
    return Expense.model_validate(service.confirm(id), from_attributes=True)


@router.delete(
    "/{id:int}",
    status_code=204,
    response_class=Response,
    summary="Delete an expense, its receipt and its image",
    responses=error_responses(404),
)
def delete_expense(id: int, service: ExpenseService = Depends(get_expense_service)) -> None:
    service.delete(id)
