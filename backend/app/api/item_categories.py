"""`/api/item-categories`, the lookup table (contracts/api-endpoints.md; decision 0020)."""

from fastapi import APIRouter, Depends, Query, Response

from app.api.errors import error_responses
from app.api.schemas import ItemCategory, ItemCategoryUpdate
from app.services.dependencies import get_item_category_service
from app.services.item_categories import ItemCategoryService

router = APIRouter(prefix="/item-categories", tags=["item-categories"])


@router.get("", response_model=list[ItemCategory], summary="List the item lookup table")
def list_item_categories(
    q: str | None = Query(None, description="substring of the normalized name"),
    service: ItemCategoryService = Depends(get_item_category_service),
) -> list[ItemCategory]:
    return [ItemCategory.model_validate(view, from_attributes=True) for view in service.search(q)]


@router.put(
    "/{normalized_name}",
    response_model=ItemCategory,
    summary="Set the category for an item name (source `user`)",
)
def put_item_category(
    normalized_name: str,
    body: ItemCategoryUpdate,
    service: ItemCategoryService = Depends(get_item_category_service),
) -> ItemCategory:
    view = service.put(normalized_name, body.category)
    return ItemCategory.model_validate(view, from_attributes=True)


@router.delete(
    "/{normalized_name}",
    status_code=204,
    response_class=Response,
    summary="Remove a user entry so the seed applies again",
    responses=error_responses(404, 409),
)
def delete_item_category(
    normalized_name: str, service: ItemCategoryService = Depends(get_item_category_service)
) -> None:
    service.delete(normalized_name)
