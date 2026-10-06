"""Static checks on the frontend (F06 acceptance criteria; frontend/api-client.md).

1. No HTML-injection sinks (`innerHTML`, `outerHTML`, `insertAdjacentHTML`,
   `document.write`) in the frontend's scripts.
2. `fetch(` only in `frontend/js/api.js`.
3. `frontend/js/categories.js` lists the contract's `Category` Literal, in order.
4. Every literal path passed to `api.get|post|patch|put|delete(` exists in
   `docs/openapi.json` with that method.

The scanned sources are `frontend/js/**/*.js` and the inline scripts of `frontend/*.html`.
Comments are stripped first, so a comment may name a sink. Each check skips with a reason
while its input doesn't exist yet: the frontend is written in parallel to the backend.
"""

import json
import re
from pathlib import Path
from typing import get_args

import pytest

from app.api.schemas import Category

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
JS_DIR = FRONTEND / "js"
API_JS = JS_DIR / "api.js"
CATEGORIES_JS = JS_DIR / "categories.js"
OPENAPI = ROOT / "docs" / "openapi.json"
API_PREFIX = "/api"

SINKS = re.compile(r"\b(innerHTML|outerHTML|insertAdjacentHTML)\b|\bdocument\s*\.\s*write(ln)?\b")
FETCH = re.compile(r"(?<![\w.$])fetch\s*\(")
# `/* … */` anywhere, and `// …` at a line start or after whitespace (not in `http://`).
BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
LINE_COMMENT = re.compile(r"(^|\s)//[^\n]*")
INLINE_SCRIPT = re.compile(r"<script\b[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# api.get('/receipts'), api.patch(`/expenses/${id}`, body): the first argument as a literal.
API_CALL = re.compile(r"\bapi\s*\.\s*(get|post|patch|put|delete)\s*\(\s*(['\"`])(.*?)\2", re.DOTALL)
PLACEHOLDER = re.compile(r"\$\{[^}]*\}")
CATEGORIES_BLOCK = re.compile(
    r"export\s+const\s+CATEGORIES\s*=\s*(?:Object\.freeze\(\s*)?\[(.*?)\]", re.DOTALL
)
CATEGORY_LINE = re.compile(r'^\s*"([^"]+)",\s*$')


def _strip_js_comments(code: str) -> str:
    return LINE_COMMENT.sub(r"\1", BLOCK_COMMENT.sub("", code))


def _sources() -> dict[Path, str]:
    """Each script's code without comments: JS files and the HTML pages' inline scripts."""
    sources: dict[Path, str] = {}
    if JS_DIR.is_dir():
        for path in sorted(JS_DIR.rglob("*.js")):
            sources[path] = _strip_js_comments(path.read_text(encoding="utf-8"))
    if FRONTEND.is_dir():
        for path in sorted(FRONTEND.glob("*.html")):
            html = HTML_COMMENT.sub("", path.read_text(encoding="utf-8"))
            scripts = "\n".join(INLINE_SCRIPT.findall(html))
            sources[path] = _strip_js_comments(scripts)
    return sources


def _require_scripts() -> dict[Path, str]:
    sources = _sources()
    if not any(path.suffix == ".js" for path in sources):
        pytest.skip(f"{JS_DIR.relative_to(ROOT)} has no scripts yet (the F06 frontend)")
    return sources


def _where(path: Path, code: str, start: int) -> str:
    return f"{path.relative_to(ROOT)}:{code.count(chr(10), 0, start) + 1}"


# ---------------------------------------------------------------- the helpers themselves


@pytest.mark.parametrize(
    ("code", "sinks"),
    [
        ("el.innerHTML = x", ["innerHTML"]),
        (
            "el.outerHTML=x; el.insertAdjacentHTML('beforeend', x)",
            ["outerHTML", "insertAdjacentHTML"],
        ),
        ("document.write(x); document . writeln(y)", ["document.write", "document . writeln"]),
        ("el.textContent = x; // never innerHTML", []),
        ("/* no innerHTML here */ el.append(x)", []),
        ('const url = "http://x"; el.innerText = url', []),
    ],
)
def test_sink_pattern(code: str, sinks: list[str]) -> None:
    found = [m.group(0) for m in SINKS.finditer(_strip_js_comments(code))]
    assert found == sinks


@pytest.mark.parametrize(
    ("code", "calls"),
    [
        ("fetch('/api/x')", 1),
        ("await window.fetch(url)", 0),  # a property access, not a bare call
        ("prefetch(x); refetch (y)", 0),
        ("await fetch (url, opts)", 1),
    ],
)
def test_fetch_pattern(code: str, calls: int) -> None:
    assert len(FETCH.findall(code)) == calls


@pytest.mark.parametrize(
    ("raw", "path"),
    [
        ("/receipts", "/api/receipts"),
        ("/receipts?status=failed", "/api/receipts"),
        ("/receipts/${id}/extract", "/api/receipts/{}/extract"),
        ("/expenses/${expense.id}", "/api/expenses/{}"),
        ("/expenses${query}", "/api/expenses"),
        ("/api/health", "/api/health"),
    ],
)
def test_path_normalisation(raw: str, path: str) -> None:
    assert _normalise(raw) == path


# ---------------------------------------------------------------- 1. no injection sinks


def test_no_html_injection_sinks() -> None:
    hits = [
        f"{_where(path, code, m.start())}: {m.group(0)}"
        for path, code in _require_scripts().items()
        for m in SINKS.finditer(code)
    ]

    assert hits == [], "build DOM nodes with textContent/createElement instead"


# ---------------------------------------------------------------- 2. fetch only in api.js


def test_fetch_only_in_api_js() -> None:
    hits = [
        _where(path, code, m.start())
        for path, code in _require_scripts().items()
        if path != API_JS
        for m in FETCH.finditer(code)
    ]

    assert hits == [], "call the API through js/api.js"


# ---------------------------------------------------------------- 3. categories match the contract


def test_js_categories_match_the_contract() -> None:
    if not CATEGORIES_JS.is_file():
        pytest.skip(f"{CATEGORIES_JS.relative_to(ROOT)} doesn't exist yet (the F06 frontend)")
    code = CATEGORIES_JS.read_text(encoding="utf-8")
    block = CATEGORIES_BLOCK.search(code)
    assert block is not None, "categories.js must `export const CATEGORIES = [ … ]`"
    lines = [line for line in block.group(1).splitlines() if line.strip()]
    malformed = [line for line in lines if not CATEGORY_LINE.match(line)]
    assert malformed == [], 'write one category per line as `"name",`'

    names = [CATEGORY_LINE.match(line).group(1) for line in lines]  # type: ignore[union-attr]

    assert names == list(get_args(Category))


# ---------------------------------------------------------------- 4. API paths exist


def _normalise(raw: str) -> str:
    """A JS path as the spec writes it: `/api` prefix, no query, `{}` for each `${…}`."""
    path = raw.split("?", 1)[0]
    # A placeholder glued to the end of a segment (`/expenses${query}`) is a query string.
    path = re.sub(r"(?<=[^/])\$\{[^}]*\}$", "", path)
    path = PLACEHOLDER.sub("{}", path)
    if path != API_PREFIX and not path.startswith(API_PREFIX + "/"):
        path = API_PREFIX + path
    return path


def _spec_operations() -> set[tuple[str, str]]:
    spec = json.loads(OPENAPI.read_text(encoding="utf-8"))
    return {
        (method.upper(), re.sub(r"\{[^}]+\}", "{}", path))
        for path, operations in spec["paths"].items()
        for method in operations
    }


def test_every_api_path_in_the_js_exists_in_the_spec() -> None:
    sources = _require_scripts()
    operations = _spec_operations()
    calls = [(path, code, m) for path, code in sources.items() for m in API_CALL.finditer(code)]
    if not calls:
        pytest.skip("no api.get/post/patch/put/delete call with a literal path yet")

    unknown = [
        f"{_where(path, code, m.start())}: {m.group(1).upper()} {m.group(3)}"
        for path, code, m in calls
        if (m.group(1).upper(), _normalise(m.group(3))) not in operations
    ]

    assert unknown == [], "not in docs/openapi.json"
