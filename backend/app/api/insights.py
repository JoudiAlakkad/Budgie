"""`/api/insights/*` (contracts/api-endpoints.md). Summary since F08, leaks since F09."""

from fastapi import APIRouter, Depends, Query

from app.api.schemas import MONTH_PATTERN, InsightsSummary, Leak
from app.services.dependencies import get_insights_service
from app.services.insights import InsightsService

router = APIRouter(prefix="/insights", tags=["insights"])

_MONTH = Query(None, pattern=MONTH_PATTERN, description="YYYY-MM; default: the current month")


@router.get("/summary", response_model=InsightsSummary, summary="Spending, budgets and goal")
def insights_summary(
    month: str | None = _MONTH, service: InsightsService = Depends(get_insights_service)
) -> InsightsSummary:
    return InsightsSummary.model_validate(service.summary(month), from_attributes=True)


@router.get("/leaks", response_model=list[Leak], summary="Detected spending leaks")
def insights_leaks(
    month: str | None = _MONTH, service: InsightsService = Depends(get_insights_service)
) -> list[Leak]:
    return [Leak.model_validate(leak, from_attributes=True) for leak in service.leaks(month)]
