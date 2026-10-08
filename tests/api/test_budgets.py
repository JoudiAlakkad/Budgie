"""`GET`/`PUT /budgets` and `GET`/`PUT /goal` (contracts/api-endpoints.md; decision 0021)."""

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


def put_budgets(api: TestClient, budgets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    response = api.put("/api/budgets", json=budgets)
    assert response.status_code == 200, response.text
    return list(response.json())


def fields(response: Any) -> list[str]:
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"] == "validation_error"
    return [f["field"] for f in body["fields"]]


# ---------------------------------------------------------------- budgets


def test_budgets_start_empty(api: TestClient) -> None:
    response = api.get("/api/budgets")

    assert response.status_code == 200
    assert response.json() == []


def test_put_replaces_the_whole_list(api: TestClient) -> None:
    put_budgets(
        api,
        [
            {"category": "snacks_sweets", "monthly_limit": 40},
            {"category": "drinks", "monthly_limit": 19.99},
        ],
    )
    answered = put_budgets(api, [{"category": "eating_out", "monthly_limit": 60.5}])

    assert answered == [{"category": "eating_out", "monthly_limit": 60.5}]
    assert api.get("/api/budgets").json() == answered


def test_budgets_are_listed_by_category(api: TestClient) -> None:
    put_budgets(
        api,
        [
            {"category": "transport", "monthly_limit": 50},
            {"category": "drinks", "monthly_limit": 20},
        ],
    )

    assert [b["category"] for b in api.get("/api/budgets").json()] == ["drinks", "transport"]


def test_put_an_empty_list_clears_the_budgets(api: TestClient) -> None:
    put_budgets(api, [{"category": "drinks", "monthly_limit": 20}])

    assert put_budgets(api, []) == []
    assert api.get("/api/budgets").json() == []


def test_a_repeated_category_is_422_and_changes_nothing(api: TestClient) -> None:
    put_budgets(api, [{"category": "drinks", "monthly_limit": 20}])

    response = api.put(
        "/api/budgets",
        json=[
            {"category": "other", "monthly_limit": 1},
            {"category": "drinks", "monthly_limit": 2},
            {"category": "other", "monthly_limit": 3},
        ],
    )

    assert fields(response) == ["2.category"]
    assert api.get("/api/budgets").json() == [{"category": "drinks", "monthly_limit": 20.0}]


@pytest.mark.parametrize(
    ("budget", "field"),
    [
        ({"category": "drinks", "monthly_limit": 0}, "0.monthly_limit"),
        ({"category": "drinks", "monthly_limit": -5}, "0.monthly_limit"),
        ({"category": "drinks", "monthly_limit": 1.234}, "0.monthly_limit"),
        ({"category": "deposit", "monthly_limit": 5}, "0.category"),
        ({"category": "uncategorized", "monthly_limit": 5}, "0.category"),
        ({"category": "drinks", "monthly_limit": 5, "x": 1}, "0.x"),
    ],
)
def test_invalid_budgets_are_422(api: TestClient, budget: dict[str, Any], field: str) -> None:
    assert fields(api.put("/api/budgets", json=[budget])) == [field]


# ---------------------------------------------------------------- goal


def test_no_goal_is_404(api: TestClient) -> None:
    response = api.get("/api/goal")

    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_put_and_get_the_goal(api: TestClient) -> None:
    goal = {"target_amount": 1200, "target_date": "2027-03-31", "monthly_income": 1400.5}

    put = api.put("/api/goal", json=goal)

    assert put.status_code == 200, put.text
    assert put.json() == {
        "target_amount": 1200.0,
        "target_date": "2027-03-31",
        "monthly_income": 1400.5,
    }
    assert api.get("/api/goal").json() == put.json()


def test_put_goal_overwrites_and_income_is_optional(api: TestClient) -> None:
    api.put("/api/goal", json={"target_amount": 1200, "target_date": "2027-03-31"})
    put = api.put(
        "/api/goal", json={"target_amount": 300, "target_date": "2026-10-01"}
    )  # the first day of the current month is allowed

    assert put.status_code == 200, put.text
    assert api.get("/api/goal").json() == {
        "target_amount": 300.0,
        "target_date": "2026-10-01",
        "monthly_income": None,
    }


@pytest.mark.parametrize(
    ("goal", "expected"),
    [
        ({"target_amount": 0, "target_date": "2027-01-01"}, ["target_amount"]),
        ({"target_amount": -1, "target_date": "2027-01-01"}, ["target_amount"]),
        (
            {"target_amount": 100, "target_date": "2027-01-01", "monthly_income": -0.01},
            ["monthly_income"],
        ),
        ({"target_amount": 100, "target_date": "2026-09-30"}, ["target_date"]),
        (
            {"target_amount": 0, "target_date": "2025-01-01", "monthly_income": -1},
            ["target_amount", "target_date", "monthly_income"],
        ),
        ({"target_amount": 100}, ["target_date"]),
        ({"target_amount": 100, "target_date": "2027-01-01", "x": 1}, ["x"]),
    ],
)
def test_invalid_goals_are_422_and_change_nothing(
    api: TestClient, goal: dict[str, Any], expected: list[str]
) -> None:
    assert fields(api.put("/api/goal", json=goal)) == expected
    assert api.get("/api/goal").status_code == 404


def test_the_current_month_follows_the_berlin_clock(settings: Settings) -> None:
    """The target-date rule uses `get_today`, so 2026-10-01 fails once it is November."""
    app = create_app(settings)
    app.dependency_overrides[get_today] = lambda: lambda: dt.date(2026, 11, 1)
    with TestClient(app) as client:
        response = client.put("/api/goal", json={"target_amount": 100, "target_date": "2026-10-31"})
    dispose_databases()

    assert fields(response) == ["target_date"]
