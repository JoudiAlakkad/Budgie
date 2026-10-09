"""`/api/expenses` (contracts/api-endpoints.md#expenses), including the CSV export
(contracts/csv-export.md)."""

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
from app.services.dependencies import get_expense_service, get_export_service
from app.services.expenses import ExpenseService
from app.services.export import CSV_COLUMNS, CSV_MEDIA_TYPE, ExportService

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


_CSV_DESCRIPTION = (
    "RFC 4180 CSV (contracts/csv-export.md): UTF-8 without BOM, comma, CRLF, a header row; "
    "confirmed expenses only, one row per line item, by date, then expense_id, then item "
    "position. Columns: "
    + ", ".join(f"`{name}`" for name in CSV_COLUMNS)
    + ". Money has exactly 2 decimals; an empty field is null (only `item_qty` and "
    "`item_unit`). A value in `merchant`, `item_description`, `item_normalized_name` or "
    "`item_unit` starting with `=`, `+`, `-`, `@`, a tab or a carriage return gets a "
    "leading `'` (formula guard)."
)


# Declared before /{id:int}; the int converter also keeps `export.csv` from matching an id route.
@router.get(
    "/export.csv",
    response_class=Response,
    summary="Confirmed expenses as CSV, one row per line item",
    responses={
        200: {
            "description": _CSV_DESCRIPTION,
            "content": {"text/csv": {"schema": {"type": "string"}}},
            "headers": {
                "Content-Disposition": {
                    "description": 'attachment; filename="budgie-expenses_<from|all>_<to|all>.csv"',
                    "schema": {"type": "string"},
                },
                "Cache-Control": {"description": "no-store", "schema": {"type": "string"}},
            },
        }
    },
)
def export_csv(
    from_: dt.date | None = Query(None, alias="from", description="inclusive"),
    to: dt.date | None = Query(None, description="inclusive"),
    service: ExportService = Depends(get_export_service),
) -> Response:
    # Built fully in memory first: a storage error is a JSON 500, never a truncated file.
    export = service.expenses_csv(from_, to)
    return Response(
        content=export.content.encode("utf-8"),
        media_type=CSV_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{export.filename}"',
            "Cache-Control": "no-store",
        },
    )


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
