"""`/api/receipts` (contracts/api-endpoints.md#receipts)."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Response

from app.api.errors import error_responses
from app.api.schemas import Receipt, ReceiptStatus, ReceiptUpload
from app.errors import NotImplementedYet
from app.services.dependencies import get_receipt_pipeline, get_receipt_service
from app.services.receipt_pipeline import ReceiptPipeline
from app.services.receipts import ReceiptService

router = APIRouter(prefix="/receipts", tags=["receipts"])


@router.post(
    "",
    response_model=Receipt,
    status_code=202,
    summary="Upload a receipt image; extraction runs in the background",
    responses=error_responses(413),
)
def upload_receipt(
    form: Annotated[ReceiptUpload, File()],
    background_tasks: BackgroundTasks,
    service: ReceiptService = Depends(get_receipt_service),
    pipeline: ReceiptPipeline = Depends(get_receipt_pipeline),
) -> Receipt:
    # A form model gives the multipart schema a stable name in the spec (`ReceiptUpload`).
    view = service.upload(form.file.file)
    # Runs after the 202 is sent (decision 0007).
    background_tasks.add_task(pipeline.run, view.id)
    return Receipt.model_validate(view, from_attributes=True)


@router.get("", response_model=list[Receipt], summary="List receipts, newest first")
def list_receipts(
    status: ReceiptStatus | None = None,
    service: ReceiptService = Depends(get_receipt_service),
) -> list[Receipt]:
    return [Receipt.model_validate(view, from_attributes=True) for view in service.list(status)]


@router.get(
    "/{id:int}",
    response_model=Receipt,
    summary="Get a receipt, with its expense once one exists",
    responses=error_responses(404),
)
def get_receipt(id: int, service: ReceiptService = Depends(get_receipt_service)) -> Receipt:
    return Receipt.model_validate(service.get(id), from_attributes=True)


@router.get(
    "/{id:int}/image",
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
def get_receipt_image(id: int, service: ReceiptService = Depends(get_receipt_service)) -> Response:
    image = service.image(id)
    return Response(content=image.data, media_type=image.media_type)


@router.post(
    "/{id:int}/extract",
    response_model=Receipt,
    status_code=202,
    summary="Retry extraction of a failed or extracted receipt",
    responses=error_responses(404, 409),
)
def extract_receipt(id: int) -> Receipt:
    raise NotImplementedYet("F05")


@router.delete(
    "/{id:int}",
    status_code=204,
    response_class=Response,
    summary="Delete a receipt, its image and its expense",
    responses=error_responses(404),
)
def delete_receipt(id: int, service: ReceiptService = Depends(get_receipt_service)) -> None:
    service.delete(id)
