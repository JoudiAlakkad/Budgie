"""`/api/expenses` (contracts/api-endpoints.md#expenses). Stubs until F05, F06 and F10."""

import datetime as dt

from fastapi import APIRouter, Query, Response

from app.api.errors import error_responses
from app.api.schemas import (
    Expense,
    ExpenseCreate,
    ExpenseUpdate,
    LineItemCategory,
    ReviewStatus,
)
from app.errors import NotImplementedYet

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
) -> list[Expense]:
    raise NotImplementedYet("F05")


@router.post(
    "",
    response_model=Expense,
    status_code=201,
    summary="Create a manual expense, optionally for a failed receipt",
    responses=error_responses(404, 409),
)
def create_expense(body: ExpenseCreate) -> Expense:
    raise NotImplementedYet("F06")


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
def get_expense(id: int) -> Expense:
    raise NotImplementedYet("F05")


@router.patch(
    "/{id:int}",
    response_model=Expense,
    summary="Edit an expense; a confirmed expense becomes unconfirmed",
    responses=error_responses(404),
)
def update_expense(id: int, body: ExpenseUpdate) -> Expense:
    raise NotImplementedYet("F06")


@router.post(
    "/{id:int}/confirm",
    response_model=Expense,
    summary="Confirm an expense",
    responses=error_responses(404),
)
def confirm_expense(id: int) -> Expense:
    raise NotImplementedYet("F06")


@router.delete(
    "/{id:int}",
    status_code=204,
    response_class=Response,
    summary="Delete an expense, its receipt and its image",
    responses=error_responses(404),
)
def delete_expense(id: int) -> None:
    raise NotImplementedYet("F06")
