"""`/api/item-categories`, the lookup table (contracts/api-endpoints.md). Stubs until F07."""

from fastapi import APIRouter, Query, Response

from app.api.errors import error_responses
from app.api.schemas import ItemCategory, ItemCategoryUpdate
from app.errors import NotImplementedYet

router = APIRouter(prefix="/item-categories", tags=["item-categories"])


@router.get("", response_model=list[ItemCategory], summary="List the item lookup table")
def list_item_categories(
    q: str | None = Query(None, description="substring of the normalized name"),
) -> list[ItemCategory]:
    raise NotImplementedYet("F07")


@router.put(
    "/{normalized_name}",
    response_model=ItemCategory,
    summary="Set the category for an item name (source `user`)",
)
def put_item_category(normalized_name: str, body: ItemCategoryUpdate) -> ItemCategory:
    raise NotImplementedYet("F07")


@router.delete(
    "/{normalized_name}",
    status_code=204,
    response_class=Response,
    summary="Remove a user entry so the seed applies again",
    responses=error_responses(404, 409),
)
def delete_item_category(normalized_name: str) -> None:
    raise NotImplementedYet("F07")
