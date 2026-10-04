"""`/api/budgets` and `/api/goal` (contracts/api-endpoints.md). Stubs until F08."""

from fastapi import APIRouter

from app.api.errors import error_responses
from app.api.schemas import Budget, Goal
from app.errors import NotImplementedYet

router = APIRouter(tags=["budgets"])


@router.get("/budgets", response_model=list[Budget], summary="List the monthly budgets")
def list_budgets() -> list[Budget]:
    raise NotImplementedYet("F08")


@router.put("/budgets", response_model=list[Budget], summary="Replace the whole budget list")
def put_budgets(body: list[Budget]) -> list[Budget]:
    raise NotImplementedYet("F08")


@router.get(
    "/goal",
    response_model=Goal,
    summary="Get the savings goal",
    responses=error_responses(404),
)
def get_goal() -> Goal:
    raise NotImplementedYet("F08")


@router.put("/goal", response_model=Goal, summary="Set the savings goal")
def put_goal(body: Goal) -> Goal:
    raise NotImplementedYet("F08")
