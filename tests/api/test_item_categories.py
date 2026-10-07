"""`GET`, `PUT` and `DELETE /item-categories` (contracts/api-endpoints.md, decision 0020)."""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.services.item_categories import load_seed


def entries(client: TestClient, q: str | None = None) -> list[dict]:
    params = {"q": q} if q is not None else {}
    response = client.get("/api/item-categories", params=params)
    assert response.status_code == 200, response.text
    return list(response.json())


def put(client: TestClient, name: str, category: str) -> dict:
    response = client.put(f"/api/item-categories/{name}", json={"category": category})
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_the_seed_is_listed_sorted_by_name(client: TestClient) -> None:
    listed = entries(client)

    names = [entry["normalized_name"] for entry in listed]
    assert names == sorted(load_seed())
    assert {entry["source"] for entry in listed} == {"seed"}
    banane = next(entry for entry in listed if entry["normalized_name"] == "banane")
    assert banane["category"] == "groceries.fresh"
    # Timestamps are UTC (naive in the database, read as UTC by the DTO).
    assert dt.datetime.fromisoformat(banane["updated_at"].replace("Z", "+00:00")).tzinfo


def test_q_is_a_substring_of_the_name(client: TestClient) -> None:
    assert [e["normalized_name"] for e in entries(client, "banan")] == ["banane", "bananen"]
    assert entries(client, "nothing-like-this") == []
    assert entries(client, "%") == []  # a literal `%`, not a wildcard


def test_put_sets_a_user_entry(client: TestClient) -> None:
    created = put(client, "zwiebelkuchen", "eating_out")

    assert {k: created[k] for k in ("normalized_name", "category", "source")} == {
        "normalized_name": "zwiebelkuchen",
        "category": "eating_out",
        "source": "user",
    }
    assert entries(client, "zwiebelkuchen") == [created]


def test_put_overrides_a_seed_entry(client: TestClient) -> None:
    put(client, "banane", "snacks_sweets")

    [banane] = [e for e in entries(client, "banane") if e["normalized_name"] == "banane"]
    assert (banane["category"], banane["source"]) == ("snacks_sweets", "user")


def test_the_path_name_is_not_normalised_again(client: TestClient) -> None:
    put(client, "BIO Banane", "other")

    assert [e["normalized_name"] for e in entries(client, "BIO")] == ["BIO Banane"]
    banane = [e for e in entries(client, "banane") if e["normalized_name"] == "banane"]
    assert banane[0]["source"] == "seed"


@pytest.mark.parametrize(
    "body",
    [{"category": "food"}, {"category": "uncategorized"}, {}, {"category": "other", "x": 1}],
)
def test_put_rejects_an_invalid_body(client: TestClient, body: dict) -> None:
    response = client.put("/api/item-categories/banane", json=body)

    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"


def test_delete_of_a_user_entry_restores_the_seed(client: TestClient) -> None:
    put(client, "banane", "snacks_sweets")

    response = client.delete("/api/item-categories/banane")

    assert response.status_code == 204
    [banane] = [e for e in entries(client, "banane") if e["normalized_name"] == "banane"]
    assert (banane["category"], banane["source"]) == ("groceries.fresh", "seed")


def test_delete_of_a_user_entry_without_a_seed_value_removes_it(client: TestClient) -> None:
    put(client, "zwiebelkuchen", "eating_out")

    assert client.delete("/api/item-categories/zwiebelkuchen").status_code == 204

    assert entries(client, "zwiebelkuchen") == []
    assert client.delete("/api/item-categories/zwiebelkuchen").status_code == 404


def test_delete_of_a_seed_entry_is_409(client: TestClient) -> None:
    response = client.delete("/api/item-categories/banane")

    assert response.status_code == 409
    assert response.json()["error"] == "invalid_state"
    assert [e["source"] for e in entries(client, "banane") if e["normalized_name"] == "banane"] == [
        "seed"
    ]


def test_delete_of_an_unknown_name_is_404(client: TestClient) -> None:
    response = client.delete("/api/item-categories/zwiebelkuchen")

    assert response.status_code == 404
    assert response.json()["error"] == "not_found"
