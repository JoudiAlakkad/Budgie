"""The F05 tables, the column types and the repositories (persistence.md)."""

import datetime as dt
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db.models import DecimalText, MoneyCents
from app.db.records import ExpenseFilter, FlagRecord, NewExpense, NewLineItem
from app.db.repositories.expenses import ExpenseRepository
from app.db.repositories.receipts import ReceiptRepository
from app.db.session import Database
from app.errors import NotFound, StorageError

NOW = dt.datetime(2026, 10, 5, 12, 0, 0)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = Database(f"sqlite:///{tmp_path / 'budgie.db'}")
    database.init_db()
    yield database
    database.dispose()


def item(description: str = "BROT", amount: str = "2.49", category: str = "uncategorized"):
    return NewLineItem(
        description=description,
        normalized_name=description.lower(),
        qty=Decimal("1"),
        unit=None,
        unit_price=Decimal(amount),
        amount=Decimal(amount),
        category=category,
        category_source="none",
    )


def expense(receipt_id: int | None = None, **changes: object) -> NewExpense:
    values: dict[str, object] = {
        "receipt_id": receipt_id,
        "merchant": "REWE",
        "date": dt.date(2026, 10, 1),
        "currency": "EUR",
        "subtotal": None,
        "tax": None,
        "total": Decimal("2.49"),
        "source": "ai",
        "review_status": "needs_review",
        "flags": (FlagRecord("line_items[0]", "uncategorized_item", "no category"),),
        "unreadable_fields": ("tax",),
        "line_items": (item(),),
    }
    return NewExpense(**(values | changes))  # type: ignore[arg-type]


def new_receipt(db: Database, uploaded_at: dt.datetime = NOW) -> int:
    with db.transaction() as session:
        return ReceiptRepository(session).create("a.jpg", "jpeg", uploaded_at).id


# ---------------------------------------------------------------- column types


@pytest.mark.parametrize(
    ("value", "stored", "read"),
    [
        (Decimal("1.99"), 199, Decimal("1.99")),
        (Decimal("-0.25"), -25, Decimal("-0.25")),
        (Decimal("0.005"), 1, Decimal("0.01")),  # half up
        (Decimal("-0.005"), -1, Decimal("-0.01")),
        (Decimal("9999999999.99"), 999999999999, Decimal("9999999999.99")),
        (None, None, None),
    ],
)
def test_money_is_stored_as_cents(
    value: Decimal | None, stored: int | None, read: Decimal | None
) -> None:
    money = MoneyCents()

    assert money.process_bind_param(value, None) == stored  # type: ignore[arg-type]
    assert money.process_result_value(stored, None) == read  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [Decimal("1.234"), Decimal("-1"), Decimal("0.0001"), None])
def test_qty_round_trips_unrounded(value: Decimal | None) -> None:
    column = DecimalText()

    stored = column.process_bind_param(value, None)  # type: ignore[arg-type]
    assert column.process_result_value(stored, None) == value  # type: ignore[arg-type]


def test_money_column_holds_integers_in_sqlite(db: Database) -> None:
    receipt_id = new_receipt(db)
    with db.transaction() as session:
        ExpenseRepository(session).insert(expense(receipt_id, total=Decimal("15.15")))
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT total, typeof(total) FROM expenses")).one() == (
            1515,
            "integer",
        )
        assert conn.execute(text("SELECT qty FROM line_items")).scalar() == "1"


# ---------------------------------------------------------------- transaction


def test_transaction_commits_and_rolls_back(db: Database) -> None:
    with pytest.raises(NotFound), db.transaction() as session:
        ReceiptRepository(session).create("x.jpg", "jpeg", NOW)
        raise NotFound()  # not a storage error: passes unchanged and rolls back

    with db.transaction() as session:
        assert ReceiptRepository(session).list() == []


def test_transaction_maps_database_errors(db: Database) -> None:
    with pytest.raises(StorageError), db.transaction() as session:
        session.execute(text("SELECT * FROM no_such_table"))


def test_transaction_maps_os_errors(db: Database) -> None:
    with pytest.raises(StorageError), db.transaction():
        raise OSError("disk full")


def test_transaction_on_unusable_database_is_storage_error(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("", encoding="utf-8")
    broken = Database(f"sqlite:///{tmp_path / 'file' / 'budgie.db'}")

    with pytest.raises(StorageError), broken.transaction() as session:
        ReceiptRepository(session).list()
    broken.dispose()


# ---------------------------------------------------------------- receipts


def test_create_get_and_list_newest_first(db: Database) -> None:
    first = new_receipt(db, NOW)
    second = new_receipt(db, NOW + dt.timedelta(seconds=1))
    same_time = new_receipt(db, NOW + dt.timedelta(seconds=1))

    with db.transaction() as session:
        repo = ReceiptRepository(session)
        record = repo.get(first)
        listed = [r.id for r in repo.list()]
        assert repo.get(999) is None

    assert record is not None
    assert (record.status, record.image_path, record.image_type, record.uploaded_at) == (
        "uploaded",
        "a.jpg",
        "jpeg",
        NOW,
    )
    assert record.error is None and record.raw_model_output is None
    assert listed == [same_time, second, first]


def test_list_filters_by_status(db: Database) -> None:
    a, b = new_receipt(db), new_receipt(db)
    with db.transaction() as session:
        repo = ReceiptRepository(session)
        assert repo.transition(b, "uploaded", "extracting")
        assert [r.id for r in repo.list("extracting")] == [b]
        assert [r.id for r in repo.list("uploaded")] == [a]
        assert repo.list("failed") == []


def test_transition_is_guarded_by_the_current_status(db: Database) -> None:
    receipt_id = new_receipt(db)
    with db.transaction() as session:
        repo = ReceiptRepository(session)
        assert repo.transition(receipt_id, "uploaded", "extracting", model_name="m")
        assert not repo.transition(receipt_id, "uploaded", "extracting")
        assert not repo.transition(999, "extracting", "failed")
        assert repo.transition(
            receipt_id, ("failed", "extracting"), "failed", error="llm_timeout", latency_ms=12
        )
        record = repo.get(receipt_id)

    assert record is not None
    assert (record.status, record.error, record.model_name, record.latency_ms) == (
        "failed",
        "llm_timeout",
        "m",
        12,
    )


def test_transition_rejects_unknown_fields(db: Database) -> None:
    receipt_id = new_receipt(db)
    with pytest.raises(ValueError), db.transaction() as session:
        ReceiptRepository(session).transition(receipt_id, "uploaded", "failed", image_path="x")


def test_reset_interrupted_fails_uploaded_and_extracting_only(db: Database) -> None:
    uploaded, extracting, extracted = new_receipt(db), new_receipt(db), new_receipt(db)
    with db.transaction() as session:
        repo = ReceiptRepository(session)
        repo.transition(extracting, "uploaded", "extracting")
        repo.transition(extracted, "uploaded", "extracted")
        assert repo.reset_interrupted() == 2
        statuses = {r.id: (r.status, r.error) for r in repo.list()}

    assert statuses == {
        uploaded: ("failed", "interrupted"),
        extracting: ("failed", "interrupted"),
        extracted: ("extracted", None),
    }


def test_delete_cascades_to_expense_and_items(db: Database) -> None:
    receipt_id = new_receipt(db)
    with db.transaction() as session:
        ExpenseRepository(session).insert(expense(receipt_id))
    with db.transaction() as session:
        deleted = ReceiptRepository(session).delete(receipt_id)
        assert ReceiptRepository(session).delete(receipt_id) is None

    assert deleted is not None and deleted.image_path == "a.jpg"
    with db.engine.connect() as conn:
        for table in ("receipts", "expenses", "line_items"):
            assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0


# ---------------------------------------------------------------- expenses


def test_insert_and_read_back(db: Database) -> None:
    receipt_id = new_receipt(db)
    items = (item("BROT", "2.49"), item("MILCH", "1.19"), item("PFAND", "-0.25"))
    with db.transaction() as session:
        inserted = ExpenseRepository(session).insert(
            expense(receipt_id, line_items=items, subtotal=Decimal("3.43"))
        )
    with db.transaction() as session:
        repo = ExpenseRepository(session)
        by_id, by_receipt = repo.get(inserted.id), repo.get_by_receipt(receipt_id)
        assert repo.get(999) is None and repo.get_by_receipt(999) is None

    assert by_id == inserted == by_receipt
    assert [i.description for i in inserted.line_items] == ["BROT", "MILCH", "PFAND"]
    assert [i.position for i in inserted.line_items] == [0, 1, 2]
    assert inserted.line_items[2].amount == Decimal("-0.25")
    assert inserted.subtotal == Decimal("3.43")
    assert inserted.flags == (FlagRecord("line_items[0]", "uncategorized_item", "no category"),)
    assert inserted.unreadable_fields == ("tax",)
    assert inserted.confirmed is False
    assert inserted.created_at == inserted.updated_at


def test_list_orders_by_date_nulls_last_then_id(db: Database) -> None:
    with db.transaction() as session:
        repo = ExpenseRepository(session)
        old = repo.insert(expense(date=dt.date(2026, 9, 1))).id
        undated_a = repo.insert(expense(date=None)).id
        new_a = repo.insert(expense(date=dt.date(2026, 10, 1))).id
        new_b = repo.insert(expense(date=dt.date(2026, 10, 1))).id
        undated_b = repo.insert(expense(date=None)).id

    with db.transaction() as session:
        listed = [e.id for e in ExpenseRepository(session).list()]

    assert listed == [new_b, new_a, old, undated_b, undated_a]


def test_list_filters(db: Database) -> None:
    with db.transaction() as session:
        repo = ExpenseRepository(session)
        sept = repo.insert(
            expense(date=dt.date(2026, 9, 30), review_status="accepted", confirmed=True)
        ).id
        oct_drinks = repo.insert(
            expense(
                date=dt.date(2026, 10, 1),
                line_items=(item("BROT"), item("COLA", category="drinks")),
            )
        ).id
        undated = repo.insert(expense(date=None)).id

    def ids(**filters: object) -> list[int]:
        with db.transaction() as session:
            found = ExpenseRepository(session).list(ExpenseFilter(**filters))  # type: ignore[arg-type]
            return [e.id for e in found]

    assert ids() == [oct_drinks, sept, undated]
    assert ids(review_status="accepted") == [sept]
    assert ids(confirmed=False) == [oct_drinks, undated]
    assert ids(date_from=dt.date(2026, 10, 1)) == [oct_drinks]
    assert ids(date_to=dt.date(2026, 9, 30)) == [sept]
    assert ids(date_from=dt.date(2026, 9, 30), date_to=dt.date(2026, 10, 1)) == [oct_drinks, sept]
    assert ids(category="drinks") == [oct_drinks]
    assert ids(category="uncategorized") == [oct_drinks, sept, undated]
    assert ids(category="alcohol") == []


def test_by_receipts_maps_receipt_ids(db: Database) -> None:
    a, b = new_receipt(db), new_receipt(db)
    with db.transaction() as session:
        repo = ExpenseRepository(session)
        expense_a = repo.insert(expense(a)).id
        found = repo.by_receipts([a, b])
        assert repo.by_receipts([]) == {}

    assert {key: value.id for key, value in found.items()} == {a: expense_a}


def test_delete_unconfirmed_for_receipt(db: Database) -> None:
    a, b = new_receipt(db), new_receipt(db)
    with db.transaction() as session:
        repo = ExpenseRepository(session)
        repo.insert(expense(a))
        repo.insert(expense(b, confirmed=True))
    with db.transaction() as session:
        repo = ExpenseRepository(session)
        assert repo.delete_unconfirmed_for_receipt(a) == 1
        assert repo.delete_unconfirmed_for_receipt(b) == 0
        assert repo.get_by_receipt(a) is None
        assert repo.get_by_receipt(b) is not None
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM line_items")).scalar() == 1


def test_one_expense_per_receipt(db: Database) -> None:
    receipt_id = new_receipt(db)
    with db.transaction() as session:
        ExpenseRepository(session).insert(expense(receipt_id))
    with pytest.raises(StorageError), db.transaction() as session:
        ExpenseRepository(session).insert(expense(receipt_id))
