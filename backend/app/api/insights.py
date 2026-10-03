"""`/api/insights/*` (contracts/api-endpoints.md). Summary until F08, leaks until F09."""

from fastapi import APIRouter, Query

from app.api.schemas import InsightsSummary, Leak
from app.errors import NotImplementedYet

router = APIRouter(prefix="/insights", tags=["insights"])

_MONTH = Query(None, pattern=r"^\d{4}-\d{2}$", description="YYYY-MM; default: the current month")


@router.get("/summary", response_model=InsightsSummary, summary="Spending, budgets and goal")
def insights_summary(month: str | None = _MONTH) -> InsightsSummary:
    raise NotImplementedYet("F08")


@router.get("/leaks", response_model=list[Leak], summary="Detected spending leaks")
def insights_leaks(month: str | None = _MONTH) -> list[Leak]:
    raise NotImplementedYet("F09")
