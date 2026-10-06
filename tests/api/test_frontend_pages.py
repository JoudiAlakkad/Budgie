"""The F06 pages are served from `FRONTEND_DIR` beside the API (decision 0005)."""

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
