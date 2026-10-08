"""The F06 pages are served from `FRONTEND_DIR` beside the API (decision 0005)."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.dependencies import dispose_databases

PAGES = {
    "index.html": "<!doctype html><title>Budgie</title><h1>Upload</h1>",
    "review.html": "<!doctype html><title>Budgie</title><h1>Review</h1>",
}


@pytest.fixture
def frontend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temp frontend with the two F06 pages, named by the FRONTEND_DIR env var."""
    root = tmp_path / "frontend"
    (root / "js").mkdir(parents=True)
    for name, html in PAGES.items():
        (root / name).write_text(html, encoding="utf-8")
    (root / "js" / "api.js").write_text("export const api = {};\n", encoding="utf-8")
    (root / "js" / "dom.js").write_text("export function formatDate() {}\n", encoding="utf-8")
    monkeypatch.setenv("FRONTEND_DIR", str(root))
    return root


@pytest.mark.parametrize(
    ("path", "page"),
    [("/", "index.html"), ("/index.html", "index.html"), ("/review.html", "review.html")],
)
def test_pages_are_served_as_html(frontend: Path, tmp_path: Path, path: str, page: str) -> None:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'budgie.db'}",
        upload_dir=str(tmp_path / "uploads"),
    )
    assert settings.frontend_dir == str(frontend)

    with TestClient(create_app(settings)) as client:
        response = client.get(path)
        script = client.get("/js/api.js")
    dispose_databases()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.text == PAGES[page]
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]


@pytest.fixture
def client(frontend: Path, tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'budgie.db'}",
        upload_dir=str(tmp_path / "uploads"),
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client
    dispose_databases()


@pytest.mark.parametrize("path", ["/", "/index.html", "/review.html", "/js/dom.js"])
def test_frontend_files_must_be_revalidated(client: TestClient, path: str) -> None:
    """F08: without it, a heuristically cached old module broke the pages' imports."""
    response = client.get(path)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["etag"]
    assert response.headers["last-modified"]


@pytest.mark.parametrize("path", ["/", "/js/dom.js"])
def test_revalidation_with_the_etag_is_a_304_with_the_header(client: TestClient, path: str) -> None:
    etag = client.get(path).headers["etag"]

    response = client.get(path, headers={"If-None-Match": etag})

    assert response.status_code == 304
    assert response.content == b""
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["etag"] == etag


def test_a_changed_file_is_sent_again(client: TestClient, frontend: Path) -> None:
    etag = client.get("/js/dom.js").headers["etag"]
    (frontend / "js" / "dom.js").write_text(
        "export function formatDate() { return 1; }\n", encoding="utf-8"
    )

    response = client.get("/js/dom.js", headers={"If-None-Match": etag})

    assert response.status_code == 200
    assert "return 1" in response.text


@pytest.mark.parametrize("path", ["/api/health", "/api/budgets", "/api/nope"])
def test_api_responses_dont_get_the_header(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.status_code in (200, 404)
    assert "cache-control" not in response.headers
