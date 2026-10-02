"""Public DTOs: the API contract (docs/wiki/contracts/api-endpoints.md)."""

from typing import Literal

from pydantic import BaseModel


class Health(BaseModel):
    """`GET /api/health`. Always returned with 200 while the app runs."""

    status: Literal["ok"]
    db: Literal["ok", "error"]
    llm: Literal["ok", "down"]
    model: str
