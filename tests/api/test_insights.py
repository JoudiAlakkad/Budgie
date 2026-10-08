"""`GET /insights/summary` (contracts/api-endpoints.md; decision 0021)."""

import datetime as dt
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.dependencies import dispose_databases, get_today

TODAY = dt.date(2026, 10, 15)


@pytest.fixture
def api(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)
    app.dependency_overrides[get_today] = lambda: lambda: TODAY
    with TestClient(app) as client:
        yield client
    dispose_databases()


def add(
    api: TestClient, day: str, *items: tuple[str, float], confirm: bool = True, merchant="REWE"
) -> int:
    """An expense of seed-categorised items; confirmed unless `confirm` is False."""
    body = {
        "merchant": merchant,
        "date": day,
        "total": round(sum(amount for _, amount in items), 2),
        "line_items": [{"description": d, "amount": a} for d, a in items],
    }
    created = api.post("/api/expenses", json=body)
    assert created.status_code == 201, created.text
    expense_id = int(created.json()["id"])
    if confirm:
        confirmed = api.post(f"/api/expenses/{expense_id}/confirm")
        assert confirmed.status_code == 200, confirmed.text
    return expense_id


def summary(api: TestClient, month: str | None = None) -> dict[str, Any]:
    params = {"month": month} if month is not None else {}
    response = api.get("/api/insights/summary", params=params)
    assert response.status_code == 200, response.text
    return dict(response.json())


def rows(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["category"]: row for row in body["categories"]}


def test_an_empty_database(api: TestClient) -> None:
    assert summary(api, "2026-10") == {
        "month": "2026-10",
        "total_spent": 0.0,
        "total_budget": None,
        "projected_total": 0.0,
        "categories": [],
        "goal": None,
    }


def test_the_default_month_is_today_in_berlin(api: TestClient) -> None:
    add(api, "2026-10-03", ("Cola", 2.0))

    body = summary(api)

    assert body["month"] == "2026-10"
    assert body["total_spent"] == 2.0


def test_the_current_month_is_projected(api: TestClient) -> None:
    api.put("/api/budgets", json=[{"category": "eating_out", "monthly_limit": 60}])
    add(api, "2026-10-03", ("Cappuccino", 3.8), ("Burger", 27.5), merchant="Cafe")

    body = summary(api, "2026-10")

    assert body["categories"] == [
        {
            "category": "eating_out",
            "spent": 31.3,
            "budget": 60.0,
            "projected": 64.69,
            "state": "on_pace_to_overrun",
        }
    ]
    assert (body["total_spent"], body["projected_total"], body["total_budget"]) == (
        31.3,
        64.69,
        60.0,
    )


def test_a_past_month_projects_its_spend(api: TestClient) -> None:
    add(api, "2026-09-03", ("Cappuccino", 3.8))

    body = summary(api, "2026-09")

    assert body["categories"][0]["projected"] == body["categories"][0]["spent"] == 3.8
    assert body["projected_total"] == body["total_spent"] == 3.8


def test_drafts_deposits_and_discounts_are_not_spend(api: TestClient) -> None:
    add(api, "2026-10-02", ("Mineralwasser", 3.54), ("Pfand", 1.5), ("Rabatt", -0.5))
    add(api, "2026-10-04", ("Cola", 9.99), confirm=False, merchant="Kiosk")

    body = summary(api, "2026-10")

    assert list(rows(body)) == ["drinks"]
    assert body["total_spent"] == 3.54


def test_month_boundaries_are_inclusive(api: TestClient) -> None:
    add(api, "2026-08-31", ("Cola", 100.0), merchant="A")
    add(api, "2026-09-01", ("Cola", 1.0), merchant="B")
    add(api, "2026-09-30", ("Cola", 2.0), merchant="C")
    add(api, "2026-10-01", ("Cola", 200.0), merchant="D")

    assert summary(api, "2026-09")["total_spent"] == 3.0


def test_rows_order_states_and_budget_only_categories(api: TestClient) -> None:
    api.put(
        "/api/budgets",
        json=[
            {"category": "electronics", "monthly_limit": 25},
            {"category": "groceries.fresh", "monthly_limit": 120},
            {"category": "health", "monthly_limit": 15},
        ],
    )
    add(api, "2026-10-01", ("Kopfhörer", 59.99), merchant="MediaMarkt")
    add(api, "2026-10-02", ("Banane", 1.89), ("Milch", 1.19))
    add(api, "2026-10-05", ("Cola", 1.89), merchant="Kiosk")

    body = summary(api, "2026-10")

    assert [(r["category"], r["state"]) for r in body["categories"]] == [
        ("electronics", "over"),
        ("groceries.fresh", "under"),
        ("drinks", "under"),  # 1.89 < 3.08; no budget is under
        ("health", "under"),
    ]
    assert rows(body)["health"] == {
        "category": "health",
        "spent": 0.0,
        "budget": 15.0,
        "projected": 0.0,
        "state": "under",
    }
    assert rows(body)["drinks"]["budget"] is None
    assert body["total_budget"] == 160.0


def test_goal_progress(api: TestClient) -> None:
    api.put(
        "/api/goal",
        json={"target_amount": 1200, "target_date": "2027-03-31", "monthly_income": 1400},
    )
    add(api, "2026-10-03", ("Cappuccino", 300.0), merchant="Cafe")  # projected 620.00

    assert summary(api, "2026-10")["goal"] == {
        "target_amount": 1200.0,
        "target_date": "2027-03-31",
        "saved_this_month": 780.0,
        "required_per_month": 200.0,
        "on_track": True,
    }
    # September: 7 months to the target, nothing spent
    september = summary(api, "2026-09")["goal"]
    assert (september["required_per_month"], september["saved_this_month"]) == (171.43, 1400.0)


def test_goal_without_income_and_after_its_date(api: TestClient) -> None:
    api.put("/api/goal", json={"target_amount": 1000, "target_date": "2026-12-01"})

    assert summary(api, "2026-10")["goal"] == {
        "target_amount": 1000.0,
        "target_date": "2026-12-01",
        "saved_this_month": None,
        "required_per_month": 333.33,
        "on_track": None,
    }
    # A month after the target clamps to one month.
    assert summary(api, "2027-02")["goal"]["required_per_month"] == 1000.0


@pytest.mark.parametrize("month", ["2026-13", "2026-1", "x", "0000-01", "0000-12"])
def test_an_invalid_month_is_422(api: TestClient, month: str) -> None:
    response = api.get("/api/insights/summary", params={"month": month})

    assert response.status_code == 422
    assert response.json()["fields"][0]["field"] == "query.month"


@pytest.mark.parametrize("month", ["0001-01", "9999-12"])
def test_the_calendar_ends_are_valid_months(api: TestClient, month: str) -> None:
    """9999-12 has no next month; the summary must not need one (was a 500)."""
    api.put(
        "/api/goal",
        json={"target_amount": 100, "target_date": "9999-12-31", "monthly_income": 10},
    )

    body = summary(api, month)

    assert body["month"] == month
    assert body["goal"]["target_date"] == "9999-12-31"


def test_leaks_stay_501(api: TestClient) -> None:
    response = api.get("/api/insights/leaks")

    assert response.status_code == 501
    assert "F09" in response.json()["detail"]
