"""`GET /insights/summary`: spending against budgets, the forecast and goal progress
(decision 0021); `GET /insights/leaks`: the potential leaks (decision 0023)."""

from app.db.repositories.budgets import BudgetRepository, GoalRepository
from app.db.repositories.expenses import ExpenseRepository
from app.db.session import Database
from app.domain import budget
from app.domain import leaks as leak_rules
from app.errors import InvalidFields
from app.services.receipt_pipeline import Today
from app.services.views import (
    CategorySpendView,
    GoalProgressView,
    InsightsSummaryView,
    LeakView,
)


class InsightsService:
    def __init__(self, db: Database, today: Today) -> None:
        self._db = db
        self._today = today

    def summary(self, month: str | None = None) -> InsightsSummaryView:
        """The summary for `month` (`YYYY-MM`); without one, the current Europe/Berlin month.

        Budgets, the goal and the spend are read in one transaction.
        """
        today = self._today()
        target = budget.Month.of(today) if month is None else _parse_month(month)
        with self._db.transaction() as session:
            limits = {b.category: b.monthly_limit for b in BudgetRepository(session).all()}
            stored_goal = GoalRepository(session).get()
            spend = ExpenseRepository(session).confirmed_spend_by_category(
                target.first, target.last
            )
        goal = (
            budget.Goal(
                stored_goal.target_amount, stored_goal.target_date, stored_goal.monthly_income
            )
            if stored_goal is not None
            else None
        )
        return summary_view(budget.summarize(target, today, spend, limits, goal))

    def leaks(self, month: str | None = None) -> list[LeakView]:
        """The potential leaks of `month` (`YYYY-MM`); without one, the current
        Europe/Berlin month.

        Budgets, the month's spend, the spend of up to `SPIKE_HISTORY_MONTHS` previous
        months (those before 0001-01 are skipped) and, for the current month only, the
        visits per category are read in one transaction.
        """
        today = self._today()
        target = budget.Month.of(today) if month is None else _parse_month(month)
        history_months = [
            previous
            for back in range(1, leak_rules.SPIKE_HISTORY_MONTHS + 1)
            if (previous := target.shifted_or_none(-back)) is not None
        ]
        with self._db.transaction() as session:
            expenses = ExpenseRepository(session)
            limits = {b.category: b.monthly_limit for b in BudgetRepository(session).all()}
            spend = expenses.confirmed_spend_by_category(target.first, target.last)
            history = [
                expenses.confirmed_spend_by_category(previous.first, previous.last)
                for previous in history_months
            ]
            visits = (
                expenses.confirmed_visits_by_category(target.first, target.last)
                if target == budget.Month.of(today)
                else {}
            )
        found = leak_rules.detect_leaks(target, today, spend, limits, history, visits)
        return [
            LeakView(leak.type, leak.category, leak.merchant, leak.amount, leak.explanation)
            for leak in found
        ]


def _parse_month(month: str) -> budget.Month:
    """The `?month=` value; a month the calendar lacks (`0000-01`, which the API pattern
    accepts) is `InvalidFields` on `query.month`, like the pattern's own 422."""
    try:
        return budget.Month.parse(month)
    except ValueError:
        raise InvalidFields(
            [("query.month", "The month must be YYYY-MM between 0001-01 and 9999-12.")]
        ) from None


def summary_view(summary: budget.Summary) -> InsightsSummaryView:
    """The domain summary as a view; the domain already rounds to cents."""
    progress = summary.goal
    return InsightsSummaryView(
        month=str(summary.month),
        total_spent=summary.total_spent,
        total_budget=summary.total_budget,
        projected_total=summary.projected_total,
        categories=[
            CategorySpendView(row.category, row.spent, row.budget, row.projected, row.state)
            for row in summary.categories
        ],
        goal=(
            GoalProgressView(
                target_amount=progress.target_amount,
                target_date=progress.target_date,
                saved_this_month=progress.saved_this_month,
                required_per_month=progress.required_per_month,
                on_track=progress.on_track,
            )
            if progress is not None
            else None
        ),
    )
