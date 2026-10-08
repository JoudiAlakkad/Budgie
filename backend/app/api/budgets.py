"""`/api/budgets` and `/api/goal` (contracts/api-endpoints.md; decision 0021)."""

from fastapi import APIRouter, Depends

from app.api.errors import error_responses
from app.api.schemas import Budget, Goal
from app.services.budgets import BudgetService
from app.services.dependencies import get_budget_service

router = APIRouter(tags=["budgets"])


@router.get("/budgets", response_model=list[Budget], summary="List the monthly budgets")
def list_budgets(service: BudgetService = Depends(get_budget_service)) -> list[Budget]:
    return [Budget.model_validate(view, from_attributes=True) for view in service.budgets()]


@router.put("/budgets", response_model=list[Budget], summary="Replace the whole budget list")
def put_budgets(
    body: list[Budget], service: BudgetService = Depends(get_budget_service)
) -> list[Budget]:
    views = service.replace_budgets(body)
    return [Budget.model_validate(view, from_attributes=True) for view in views]


@router.get(
    "/goal",
    response_model=Goal,
    summary="Get the savings goal",
    responses=error_responses(404),
)
def get_goal(service: BudgetService = Depends(get_budget_service)) -> Goal:
    return Goal.model_validate(service.goal(), from_attributes=True)


@router.put("/goal", response_model=Goal, summary="Set the savings goal")
def put_goal(body: Goal, service: BudgetService = Depends(get_budget_service)) -> Goal:
    return Goal.model_validate(service.put_goal(body), from_attributes=True)
