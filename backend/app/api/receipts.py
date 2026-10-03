"""`/api/receipts` (contracts/api-endpoints.md#receipts). Stubs until F05."""

from typing import Annotated

from fastapi import APIRouter, File, Response

from app.api.errors import error_responses
from app.api.schemas import Receipt, ReceiptStatus, ReceiptUpload
from app.errors import NotImplementedYet

router = APIRouter(prefix="/receipts", tags=["receipts"])


@router.post(
    "",
    response_model=Receipt,
    status_code=202,
    summary="Upload a receipt image; extraction runs in the background",
    responses=error_responses(413),
)
def upload_receipt(form: Annotated[ReceiptUpload, File()]) -> Receipt:
    # A form model gives the multipart schema a stable name in the spec (`ReceiptUpload`).
    raise NotImplementedYet("F05")


@router.get("", response_model=list[Receipt], summary="List receipts, newest first")
def list_receipts(status: ReceiptStatus | None = None) -> list[Receipt]:
    raise NotImplementedYet("F05")


@router.get(
    "/{id}",
    response_model=Receipt,
    summary="Get a receipt, with its expense once one exists",
    responses=error_responses(404),
)
def get_receipt(id: int) -> Receipt:
    raise NotImplementedYet("F05")


@router.get(
    "/{id}/image",
    response_class=Response,
    summary="The receipt image",
    responses={
        200: {
            "description": "The image bytes",
            "content": {
                "image/jpeg": {"schema": {"type": "string", "format": "binary"}},
                "image/png": {"schema": {"type": "string", "format": "binary"}},
                "image/webp": {"schema": {"type": "string", "format": "binary"}},
            },
        },
        **error_responses(404),
    },
)
def get_receipt_image(id: int) -> Response:
    raise NotImplementedYet("F05")


@router.post(
    "/{id}/extract",
    response_model=Receipt,
    status_code=202,
    summary="Retry extraction of a failed or extracted receipt",
    responses=error_responses(404, 409),
)
def extract_receipt(id: int) -> Receipt:
    raise NotImplementedYet("F05")


@router.delete(
    "/{id}",
    status_code=204,
    response_class=Response,
    summary="Delete a receipt, its image and its expense",
    responses=error_responses(404),
)
def delete_receipt(id: int) -> None:
    raise NotImplementedYet("F05")
