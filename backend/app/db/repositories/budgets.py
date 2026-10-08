"""The budget list `budgets` and the single savings goal `savings_goal` (persistence.md)."""

from collections.abc import Iterable

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.db.models import BudgetRow, GoalRow
from app.db.records import BudgetRecord, GoalRecord

GOAL_ID = 1


class BudgetRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def all(self) -> tuple[BudgetRecord, ...]:
        """Every budget, by category."""
        rows = self._session.scalars(select(BudgetRow).order_by(BudgetRow.category))
        return tuple(BudgetRecord(row.category, row.monthly_limit) for row in rows)

    def replace(self, budgets: Iterable[BudgetRecord]) -> tuple[BudgetRecord, ...]:
        """Replace the whole list: delete, then insert, inside the caller's transaction, so
        a failure leaves the old list. Categories must be unique (the service checks)."""
        self._session.execute(delete(BudgetRow))
        self._session.add_all(
            BudgetRow(category=budget.category, monthly_limit=budget.monthly_limit)
            for budget in budgets
        )
        self._session.flush()
        self._session.expire_all()  # read back what the column types stored
        return self.all()

    def count(self) -> int:
        return self._session.scalar(select(func.count()).select_from(BudgetRow)) or 0


class GoalRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self) -> GoalRecord | None:
        row = self._session.get(GoalRow, GOAL_ID, populate_existing=True)
        if row is None:
            return None
        return GoalRecord(row.target_amount, row.target_date, row.monthly_income)

    def put(self, goal: GoalRecord) -> GoalRecord:
        """Insert or overwrite the one goal row: one SQLite `INSERT ... ON CONFLICT DO
        UPDATE` with no read first (like `ItemCategoryRepository`; decision 0006)."""
        table = GoalRow.__table__
        statement = sqlite_insert(table).values(
            id=GOAL_ID,
            target_amount=goal.target_amount,
            target_date=goal.target_date,
            monthly_income=goal.monthly_income,
        )
        excluded = statement.excluded
        statement = statement.on_conflict_do_update(
            index_elements=[table.c.id],
            set_={
                "target_amount": excluded.target_amount,
                "target_date": excluded.target_date,
                "monthly_income": excluded.monthly_income,
            },
        )
        self._session.execute(statement)
        stored = self.get()  # the statement bypasses the identity map
        assert stored is not None
        return stored
