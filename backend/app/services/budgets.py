"""Budgets and the savings goal: `GET`/`PUT /budgets`, `GET`/`PUT /goal`
(contracts/api-endpoints.md#budgets-goal-insights-health; decision 0021)."""

import datetime as dt
import logging
from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol

from app.db.records import BudgetRecord, GoalRecord
from app.db.repositories.budgets import BudgetRepository, GoalRepository
from app.db.session import Database
from app.domain.budget import Month
from app.errors import InvalidFields, NotFound
from app.services.receipt_pipeline import Today
from app.services.views import BudgetView, GoalView, cents, optional_cents

logger = logging.getLogger(__name__)


class BudgetInput(Protocol):
    """A budget as sent by the client (`api.schemas.Budget`)."""

    @property
    def category(self) -> str: ...
    @property
    def monthly_limit(self) -> Decimal: ...


class GoalInput(Protocol):
    """The goal as sent by the client (`api.schemas.Goal`)."""

    @property
    def target_amount(self) -> Decimal: ...
    @property
    def target_date(self) -> dt.date: ...
    @property
    def monthly_income(self) -> Decimal | None: ...


def budget_view(record: BudgetRecord) -> BudgetView:
    return BudgetView(category=record.category, monthly_limit=cents(record.monthly_limit))


def goal_view(record: GoalRecord) -> GoalView:
    return GoalView(
        target_amount=cents(record.target_amount),
        target_date=record.target_date,
        monthly_income=optional_cents(record.monthly_income),
    )


class BudgetService:
    def __init__(self, db: Database, today: Today) -> None:
        self._db = db
        self._today = today

    def budgets(self) -> list[BudgetView]:
        """Every budget, by category."""
        with self._db.transaction() as session:
            records = BudgetRepository(session).all()
        return [budget_view(record) for record in records]

    def replace_budgets(self, budgets: Sequence[BudgetInput]) -> list[BudgetView]:
        """Replace the whole list. A category listed twice is `InvalidFields` on
        `<i>.category` (each repeat after the first); a limit ≤ 0 on `<i>.monthly_limit`."""
        problems: list[tuple[str, str]] = []
        seen: set[str] = set()
        for index, budget in enumerate(budgets):
            if budget.category in seen:
                problems.append(
                    (f"{index}.category", f"The category {budget.category} is listed twice.")
                )
            seen.add(budget.category)
            if budget.monthly_limit <= 0:
                problems.append((f"{index}.monthly_limit", "The limit must be greater than 0."))
        if problems:
            raise InvalidFields(problems)
        records = [BudgetRecord(b.category, cents(b.monthly_limit)) for b in budgets]
        with self._db.transaction() as session:
            stored = BudgetRepository(session).replace(records)
        logger.info("Budgets replaced: %d categories", len(stored))
        return [budget_view(record) for record in stored]

    def goal(self) -> GoalView:
        """The goal; `NotFound` if none is set."""
        with self._db.transaction() as session:
            record = GoalRepository(session).get()
        if record is None:
            raise NotFound("No savings goal is set.")
        return goal_view(record)

    def put_goal(self, goal: GoalInput) -> GoalView:
        """Set the goal. `InvalidFields` for `target_amount` ≤ 0, `monthly_income` < 0, or a
        `target_date` before the current month (Europe/Berlin)."""
        problems: list[tuple[str, str]] = []
        if goal.target_amount <= 0:
            problems.append(("target_amount", "The target amount must be greater than 0."))
        this_month = Month.of(self._today())
        if goal.target_date < this_month.first:
            problems.append(
                ("target_date", f"The target date must not be before {this_month.first}.")
            )
        if goal.monthly_income is not None and goal.monthly_income < 0:
            problems.append(("monthly_income", "The monthly income must not be negative."))
        if problems:
            raise InvalidFields(problems)
        record = GoalRecord(
            target_amount=cents(goal.target_amount),
            target_date=goal.target_date,
            monthly_income=optional_cents(goal.monthly_income),
        )
        with self._db.transaction() as session:
            stored = GoalRepository(session).put(record)
        logger.info("Savings goal set")
        return goal_view(stored)
