"""The F05 tables, `item_categories` (F07), the column types and the repositories
(persistence.md)."""

import datetime as dt
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db.models import DecimalText, MoneyCents
from app.db.records import (
    DuplicateCandidate,
    ExpenseChanges,
    ExpenseFilter,
    ExpenseRecord,
    FlagRecord,
    LineItemChange,
    NewExpense,
    NewLineItem,
)
from app.db.repositories.expenses import ExpenseRepository
from app.db.repositories.item_categories import ItemCategoryRepository
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


def test_get_after_transition_in_the_same_session_is_fresh(db: Database) -> None:
    receipt_id = new_receipt(db)
    with db.transaction() as session:
        repo = ReceiptRepository(session)
        before = repo.get(receipt_id)
        assert repo.transition(receipt_id, "uploaded", "failed", error="llm_error")
        after = repo.get(receipt_id)

    assert before is not None and before.status == "uploaded"
    assert after is not None and (after.status, after.error) == ("failed", "llm_error")


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


def changes(stored: ExpenseRecord, *items: LineItemChange, **values: object) -> ExpenseChanges:
    """`stored` with `values` and `items` (the stored items unchanged if none are given)."""
    if not items:
        items = tuple(
            LineItemChange(
                id=i.id,
                description=i.description,
                normalized_name=i.normalized_name,
                qty=i.qty,
                unit=i.unit,
                unit_price=i.unit_price,
                amount=i.amount,
                category=i.category,
                category_source=i.category_source,
            )
            for i in stored.line_items
        )
    fields = {
        name: getattr(stored, name)
        for name in (
            "merchant",
            "date",
            "currency",
            "subtotal",
            "tax",
            "total",
            "source",
            "review_status",
            "flags",
            "unreadable_fields",
            "confirmed",
        )
    }
    return ExpenseChanges(**(fields | values), line_items=items)  # type: ignore[arg-type]


def edited_item(
    item_id: int | None, description: str, amount: str = "1.00", category: str = "other"
) -> LineItemChange:
    return LineItemChange(
        id=item_id,
        description=description,
        normalized_name=description.lower(),
        qty=None,
        unit="kg",
        unit_price=None,
        amount=Decimal(amount),
        category=category,
        category_source="user",
    )


def test_update_writes_the_merged_state(db: Database) -> None:
    receipt_id = new_receipt(db)
    with db.transaction() as session:
        stored = ExpenseRepository(session).insert(expense(receipt_id, confirmed=True))

    with db.transaction() as session:
        updated = ExpenseRepository(session).update(
            stored.id,
            changes(
                stored,
                merchant="Edeka",
                date=None,
                currency="CHF",
                subtotal=Decimal("0.005"),  # rounded half up by the column
                tax=Decimal("0.40"),
                total=None,
                source="ai_corrected",
                review_status="rejected",
                flags=(FlagRecord("total", "missing_total", "The total is missing."),),
                unreadable_fields=(),
                confirmed=False,
            ),
        )
    with db.transaction() as session:
        again = ExpenseRepository(session).get(stored.id)

    assert again == updated
    assert (updated.merchant, updated.date, updated.currency) == ("Edeka", None, "CHF")
    assert (updated.subtotal, updated.tax, updated.total) == (
        Decimal("0.01"),
        Decimal("0.40"),
        None,
    )
    assert (updated.source, updated.review_status, updated.confirmed) == (
        "ai_corrected",
        "rejected",
        False,
    )
    assert updated.flags == (FlagRecord("total", "missing_total", "The total is missing."),)
    assert updated.unreadable_fields == ()
    assert updated.receipt_id == receipt_id
    assert updated.created_at == stored.created_at
    assert updated.updated_at >= stored.updated_at
    assert updated.line_items == stored.line_items


def test_update_replaces_the_items_in_order(db: Database) -> None:
    with db.transaction() as session:
        stored = ExpenseRepository(session).insert(
            expense(line_items=(item("BROT"), item("MILCH"), item("PFAND", "-0.25")))
        )
    brot, milch, _pfand = stored.line_items

    with db.transaction() as session:
        updated = ExpenseRepository(session).update(
            stored.id,
            changes(
                stored,
                edited_item(None, "WASSER", "0.49"),
                edited_item(milch.id, "MILCH 1L", "1.29"),
                edited_item(brot.id, "BROT", "2.49", category="groceries.staples"),
            ),
        )

    wasser, milch_after, brot_after = updated.line_items
    assert [i.position for i in updated.line_items] == [0, 1, 2]
    assert wasser.id not in {i.id for i in stored.line_items}
    assert (wasser.description, wasser.amount, wasser.unit) == ("WASSER", Decimal("0.49"), "kg")
    assert milch_after.id == milch.id
    assert (milch_after.description, milch_after.normalized_name) == ("MILCH 1L", "milch 1l")
    assert (milch_after.amount, milch_after.category) == (Decimal("1.29"), "other")
    assert (brot_after.id, brot_after.category, brot_after.category_source) == (
        brot.id,
        "groceries.staples",
        "user",
    )
    with db.engine.connect() as conn:  # PFAND is gone
        assert conn.execute(text("SELECT count(*) FROM line_items")).scalar() == 3


def test_update_rejects_unknown_expenses_and_foreign_items(db: Database) -> None:
    with db.transaction() as session:
        repo = ExpenseRepository(session)
        mine, other = repo.insert(expense()), repo.insert(expense())

    with pytest.raises(NotFound), db.transaction() as session:
        ExpenseRepository(session).update(999, changes(mine))
    foreign = edited_item(other.line_items[0].id, "BROT")
    with pytest.raises(ValueError, match="not an item"), db.transaction() as session:
        ExpenseRepository(session).update(mine.id, changes(mine, foreign))
    own = edited_item(mine.line_items[0].id, "BROT")
    with pytest.raises(ValueError, match="repeated"), db.transaction() as session:
        ExpenseRepository(session).update(mine.id, changes(mine, own, own))

    with db.transaction() as session:
        assert ExpenseRepository(session).get(other.id) == other
        assert ExpenseRepository(session).get(mine.id) == mine


def test_set_confirmed(db: Database) -> None:
    with db.transaction() as session:
        stored = ExpenseRepository(session).insert(expense())

    with db.transaction() as session:
        repo = ExpenseRepository(session)
        assert repo.set_confirmed(stored.id, True) is True
        confirmed = repo.get(stored.id)  # fresh in the same session
        assert repo.set_confirmed(999, True) is False
    with db.transaction() as session:
        repo = ExpenseRepository(session)
        assert repo.get(stored.id) == confirmed
        assert repo.set_confirmed(stored.id, False) is True
        unconfirmed = repo.get(stored.id)

    assert confirmed is not None and confirmed.confirmed is True
    assert confirmed.updated_at >= stored.updated_at
    assert unconfirmed is not None and unconfirmed.confirmed is False
    assert confirmed.line_items == stored.line_items


def test_delete_expense_keeps_the_receipt(db: Database) -> None:
    receipt_id = new_receipt(db)
    with db.transaction() as session:
        repo = ExpenseRepository(session)
        stored = repo.insert(expense(receipt_id))
        other = repo.insert(expense()).id

    with db.transaction() as session:
        repo = ExpenseRepository(session)
        deleted = repo.delete(stored.id)
        assert repo.delete(stored.id) is None
        assert repo.get(stored.id) is None

    assert deleted == stored
    with db.transaction() as session:
        assert ReceiptRepository(session).get(receipt_id) is not None
        assert ExpenseRepository(session).get(other) is not None
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM line_items")).scalar() == 1


def test_one_expense_per_receipt(db: Database) -> None:
    receipt_id = new_receipt(db)
    with db.transaction() as session:
        ExpenseRepository(session).insert(expense(receipt_id))
    with pytest.raises(StorageError), db.transaction() as session:
        ExpenseRepository(session).insert(expense(receipt_id))


def test_ids_of_deleted_rows_are_never_reused(db: Database) -> None:
    receipt_id = new_receipt(db)
    with db.transaction() as session:
        expense_id = ExpenseRepository(session).insert(expense(receipt_id)).id
    with db.transaction() as session:
        ReceiptRepository(session).delete(receipt_id)

    again = new_receipt(db)
    with db.transaction() as session:
        again_expense = ExpenseRepository(session).insert(expense(again)).id

    assert again > receipt_id
    assert again_expense > expense_id


# ---------------------------------------------------------------- duplicate candidates (F07)


def test_duplicate_candidates_match_date_and_total_within_a_cent(db: Database) -> None:
    with db.transaction() as session:
        repo = ExpenseRepository(session)
        same = repo.insert(expense(merchant="REWE Markt GmbH", total=Decimal("15.15"))).id
        cent_below = repo.insert(expense(merchant="Aldi", total=Decimal("15.14"))).id
        cent_above = repo.insert(expense(merchant=None, total=Decimal("15.16"))).id
        repo.insert(expense(total=Decimal("15.17")))
        repo.insert(expense(total=Decimal("15.13")))
        repo.insert(expense(total=Decimal("15.15"), date=dt.date(2026, 10, 2)))
        repo.insert(expense(total=None))
        confirmed = repo.insert(expense(total=Decimal("15.15"), confirmed=True)).id

    with db.transaction() as session:
        found = ExpenseRepository(session).duplicate_candidates(
            dt.date(2026, 10, 1), Decimal("15.15")
        )
        without = ExpenseRepository(session).duplicate_candidates(
            dt.date(2026, 10, 1), Decimal("15.154"), exclude_id=same
        )

    assert found == [
        DuplicateCandidate(same, "REWE Markt GmbH", dt.date(2026, 10, 1), Decimal("15.15")),
        DuplicateCandidate(cent_below, "Aldi", dt.date(2026, 10, 1), Decimal("15.14")),
        DuplicateCandidate(cent_above, None, dt.date(2026, 10, 1), Decimal("15.16")),
        DuplicateCandidate(confirmed, "REWE", dt.date(2026, 10, 1), Decimal("15.15")),
    ]
    # An unrounded total is compared as stored (15.154 -> 15.15).
    assert [c.id for c in without] == [cent_below, cent_above, confirmed]


# ---------------------------------------------------------------- item_categories (F07)


def table(db: Database) -> dict[str, tuple[str, str]]:
    with db.transaction() as session:
        return ItemCategoryRepository(session).table()


def test_sync_seed_inserts_missing_names_and_is_idempotent(db: Database) -> None:
    seed = {"banane": "groceries.fresh", "pfand": "deposit"}

    with db.transaction() as session:
        first = ItemCategoryRepository(session).sync_seed(seed)
    with db.transaction() as session:
        repo = ItemCategoryRepository(session)
        stamp = repo.get("banane")
        second = repo.sync_seed(seed)

    assert (first.inserted, first.updated) == (2, 0)
    assert (second.inserted, second.updated) == (0, 0)
    assert table(db) == {"banane": ("groceries.fresh", "seed"), "pfand": ("deposit", "seed")}
    with db.transaction() as session:
        assert ItemCategoryRepository(session).get("banane") == stamp  # untouched


def test_sync_seed_updates_seed_rows_and_never_touches_user_rows(db: Database) -> None:
    with db.transaction() as session:
        repo = ItemCategoryRepository(session)
        repo.sync_seed({"kaffee": "drinks", "cola": "drinks"})
        repo.upsert_user("cola", "alcohol")
        repo.upsert_user("zwiebeln", "groceries.fresh")

    with db.transaction() as session:
        result = ItemCategoryRepository(session).sync_seed(
            {"kaffee": "groceries.staples", "cola": "drinks", "zwiebeln": "other"}
        )

    assert (result.inserted, result.updated) == (0, 1)
    assert table(db) == {
        "kaffee": ("groceries.staples", "seed"),  # the seed's new value
        "cola": ("alcohol", "user"),
        "zwiebeln": ("groceries.fresh", "user"),
    }


def test_sync_seed_keeps_names_the_seed_no_longer_has(db: Database) -> None:
    with db.transaction() as session:
        ItemCategoryRepository(session).sync_seed({"alt": "other"})
    with db.transaction() as session:
        ItemCategoryRepository(session).sync_seed({})

    assert table(db) == {"alt": ("other", "seed")}


def test_upsert_user_overrides_a_seed_entry_and_stamps_it(db: Database) -> None:
    with db.transaction() as session:
        repo = ItemCategoryRepository(session)
        repo.sync_seed({"banane": "groceries.fresh"})
        seeded = repo.get("banane")
        record = repo.upsert_user("banane", "snacks_sweets")

    assert seeded is not None
    assert (record.normalized_name, record.category, record.source) == (
        "banane",
        "snacks_sweets",
        "user",
    )
    assert record.updated_at >= seeded.updated_at
    assert record.updated_at.tzinfo is None  # naive UTC
    assert table(db) == {"banane": ("snacks_sweets", "user")}


def test_put_seed_restores_a_seed_entry(db: Database) -> None:
    with db.transaction() as session:
        repo = ItemCategoryRepository(session)
        repo.upsert_user("banane", "other")
        assert repo.delete("banane") is True
        assert repo.delete("banane") is False
        repo.put_seed("banane", "groceries.fresh")

    assert table(db) == {"banane": ("groceries.fresh", "seed")}


def test_search_is_a_substring_match_sorted_by_name(db: Database) -> None:
    with db.transaction() as session:
        repo = ItemCategoryRepository(session)
        repo.sync_seed(
            {"bananen": "groceries.fresh", "banane": "groceries.fresh", "milch": "groceries.fresh"}
        )
        repo.upsert_user("100%_saft", "drinks")

    with db.transaction() as session:
        repo = ItemCategoryRepository(session)
        names = [r.normalized_name for r in repo.search("anan")]
        everything = [r.normalized_name for r in repo.search(None)]
        literal = [r.normalized_name for r in repo.search("%_")]
        wildcard = [r.normalized_name for r in repo.search("_")]

    assert names == ["banane", "bananen"]
    assert everything == ["100%_saft", "banane", "bananen", "milch"]
    # `%` and `_` match themselves, not any text.
    assert literal == ["100%_saft"]
    assert wildcard == ["100%_saft"]
