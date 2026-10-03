"""The static frontend mount never claims `/api` paths, so routing 404/405 stay in the API."""

import pytest
from starlette.routing import Match
from starlette.staticfiles import StaticFiles

from app.main import FrontendMount


def _match(path: str, root_path: str = "") -> Match:
    mount = FrontendMount("/", app=StaticFiles(directory=".", check_dir=False))
    scope = {"type": "http", "path": path, "root_path": root_path, "method": "GET"}
    return mount.matches(scope)[0]


@pytest.mark.parametrize(
    ("path", "root_path"),
    [("/api", ""), ("/api/", ""), ("/api/nope", ""), ("/budgie/api/nope", "/budgie")],
)
def test_api_paths_are_declined(path: str, root_path: str) -> None:
    assert _match(path, root_path) is Match.NONE


@pytest.mark.parametrize(
    ("path", "root_path"),
    [("/", ""), ("/index.html", ""), ("/apiary.js", ""), ("/budgie/app.js", "/budgie")],
)
def test_other_paths_go_to_the_frontend(path: str, root_path: str) -> None:
    assert _match(path, root_path) is Match.FULL
