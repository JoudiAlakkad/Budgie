"""Static checks on the frontend (F06 acceptance criteria; frontend/api-client.md).

1. No HTML-injection sinks (`innerHTML`, `outerHTML`, `insertAdjacentHTML`,
   `document.write`) in the frontend's scripts.
2. `fetch(` only in `frontend/js/api.js`.
3. `frontend/js/categories.js` lists the contract's `Category` Literal, in order.
4. Every literal path passed to `api.get|post|patch|put|delete(` exists in
   `docs/openapi.json` with that method; `api.download(` counts as a `GET` (F10).
5. `CATEGORY_LABELS` in `frontend/js/categories.js` equals the domain's copy, which the
   leak explanations use (F09, decision 0023).
6. No user-visible string in `frontend/js/dashboard.js` says "leak" without "potential"
   (F09, decision 0023).

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
from app.domain.leaks import CATEGORY_LABELS

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
# api.download(`/expenses/export.csv${query}`) is a GET that saves the body (F10).
API_CALL = re.compile(
    r"\bapi\s*\.\s*(get|post|patch|put|delete|download)\s*\(\s*(['\"`])(.*?)\2", re.DOTALL
)
HELPER_METHODS = {"download": "GET"}
"""`api.*` helpers whose name isn't their HTTP method."""
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


def _http_method(helper: str) -> str:
    """The HTTP method behind an `api.<helper>(` call."""
    return HELPER_METHODS.get(helper, helper.upper())


def test_download_is_checked_as_a_get() -> None:
    """The regex sees `api.download(...)`, and the check treats it as a GET (F10)."""
    code = "await api.download(`/expenses/export.csv${query}`);"
    [match] = API_CALL.finditer(code)

    assert (_http_method(match.group(1)), _normalise(match.group(3))) == (
        "GET",
        "/api/expenses/export.csv",
    )
    assert ("GET", "/api/expenses/export.csv") in _spec_operations()


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
        pytest.skip("no api.get/post/patch/put/delete/download call with a literal path yet")

    unknown = [
        f"{_where(path, code, m.start())}: {_http_method(m.group(1))} {m.group(3)}"
        for path, code, m in calls
        if (_http_method(m.group(1)), _normalise(m.group(3))) not in operations
    ]

    assert unknown == [], "not in docs/openapi.json"


# ---------------------------------------------------------------- 5. category labels (F09)

DASHBOARD_JS = JS_DIR / "dashboard.js"
LABELS_BLOCK = re.compile(
    r"export\s+const\s+CATEGORY_LABELS\s*=\s*(?:Object\.freeze\(\s*)?\{(.*?)\}", re.DOTALL
)
LABEL_LINE = re.compile(r'^\s*(?:"([^"]+)"|([A-Za-z_$][\w$]*))\s*:\s*"([^"]*)",?\s*$')


def _js_labels(code: str) -> dict[str, str]:
    block = LABELS_BLOCK.search(code)
    assert block is not None, "categories.js must `export const CATEGORY_LABELS = { … }`"
    lines = [line for line in block.group(1).splitlines() if line.strip()]
    malformed = [line for line in lines if not LABEL_LINE.match(line)]
    assert malformed == [], 'write one label per line as `key: "Label",`'
    matches = [LABEL_LINE.match(line) for line in lines]
    return {m.group(1) or m.group(2): m.group(3) for m in matches if m is not None}


def test_label_parser() -> None:
    code = 'export const CATEGORY_LABELS = {\n  "a.b": "A: b",\n  c_d: "C and d",\n};'

    assert _js_labels(code) == {"a.b": "A: b", "c_d": "C and d"}


def test_domain_labels_equal_the_js_labels() -> None:
    """The leak explanations name categories as the UI does (decision 0023)."""
    if not CATEGORIES_JS.is_file():
        pytest.skip(f"{CATEGORIES_JS.relative_to(ROOT)} doesn't exist yet")

    assert _js_labels(CATEGORIES_JS.read_text(encoding="utf-8")) == CATEGORY_LABELS


# ---------------------------------------------------------------- 6. "potential leak" (F09)

# A user-visible "leak" must be a "potential leak" (decision 0023).
BARE_LEAK = re.compile(r"(?<!potential )\bleaks?\b", re.IGNORECASE)
# A literal that is one lowercase token: an id, a class, a key, a path.
TOKEN_LITERAL = re.compile(r"[a-z0-9_\-./?=&#:]*")
# A word joined by `-`, `_`, `/`, `.`, `=`, `?`, `#`, `&` or `:` inside it (`leak-card`,
# `/insights/leaks`, `state.leaks`) is a token, not prose.
TOKEN_WORD = re.compile(r"\w[-_/.=?#&:]\w")
# A `/` after one of these starts a regex literal, not a division.
REGEX_BEFORE = set("(,=:[!&|?{};+-*%<>~^")


def _string_end(code: str, start: int) -> int:
    """The index of the quote closing the string opened at `start`."""
    quote, i = code[start], start + 1
    while i < len(code) and code[i] != quote and code[i] != "\n":
        i += 2 if code[i] == "\\" else 1
    return i


def _regex_end(code: str, start: int) -> int:
    """The index after a regex literal opened at `start` (and its flags)."""
    i, in_class = start + 1, False
    while i < len(code) and code[i] != "\n":
        char = code[i]
        if char == "\\":
            i += 2
            continue
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "/" and not in_class:
            i += 1
            break
        i += 1
    while i < len(code) and code[i].isalpha():
        i += 1
    return i


def _template(code: str, start: int, found: list[tuple[int, str]]) -> int:
    """Reads a template literal whose text starts at `start`; each `${…}` becomes a space
    and is scanned for literals of its own. Returns the index after the closing backtick."""
    i, text = start, []
    while i < len(code):
        if code[i] == "\\":
            text.append(code[i : i + 2])
            i += 2
        elif code[i] == "`":
            i += 1
            break
        elif code.startswith("${", i):
            text.append(" ")
            i = _scan(code, i + 2, found, in_placeholder=True)
        else:
            text.append(code[i])
            i += 1
    found.append((start, "".join(text)))
    return i


def _scan(code: str, i: int, found: list[tuple[int, str]], in_placeholder: bool) -> int:
    """Collects the literals from `i`; inside a `${…}` it returns after the closing brace."""
    depth, previous = 0, ""
    while i < len(code):
        char = code[i]
        if code.startswith("//", i):
            end = code.find("\n", i)
            i = len(code) if end < 0 else end
            continue
        if code.startswith("/*", i):
            end = code.find("*/", i + 2)
            i = len(code) if end < 0 else end + 2
            continue
        if char in "'\"":
            end = _string_end(code, i)
            found.append((i + 1, code[i + 1 : end]))
            i, previous = end + 1, char
            continue
        if char == "`":
            i, previous = _template(code, i + 1, found), char
            continue
        if char == "/" and (previous == "" or previous in REGEX_BEFORE):
            i, previous = _regex_end(code, i), "/"
            continue
        if in_placeholder and char == "{":
            depth += 1
        elif in_placeholder and char == "}":
            if depth == 0:
                return i + 1
            depth -= 1
        if not char.isspace():
            previous = char
        i += 1
    return i


def _string_literals(code: str) -> list[tuple[int, str]]:
    """(offset, text) of every string and template literal outside comments."""
    found: list[tuple[int, str]] = []
    _scan(code, 0, found, in_placeholder=False)
    return sorted(found)


def _visible_text(literal: str) -> str:
    """The prose of a literal: "" for a token literal, else without its token words."""
    if TOKEN_LITERAL.fullmatch(literal.strip()):
        return ""
    return " ".join(word for word in literal.split() if not TOKEN_WORD.search(word))


def _bare_leaks(code: str) -> list[str]:
    texts = [_visible_text(literal) for _, literal in _string_literals(code)]
    return [text for text in texts if BARE_LEAK.search(text)]


@pytest.mark.parametrize(
    ("text", "flagged"),
    [
        ("Potential leaks", False),
        ("potential leak", False),
        ("POTENTIAL LEAK", False),
        ("No potential leaks found in October 2026.", False),
        ("Leaks", True),
        ("No leaks found", True),
        ("Leak detected", True),
        ("a leak, and a potential leak", True),
        ("Potential  leaks", True),  # two spaces: not the phrase
        ("leaky tap", False),
        ("leakage", False),
        ("Spending by category", False),
    ],
)
def test_bare_leak_pattern(text: str, flagged: bool) -> None:
    assert bool(BARE_LEAK.search(text)) is flagged


@pytest.mark.parametrize(
    ("code", "literals"),
    [
        ('h("h2", {}, "Leaks")', ["h2", "Leaks"]),
        ("const a = 'it\\'s'; // \"Leaks\" in a comment", ["it\\'s"]),
        ("/* 'Leaks' */ x = `Found ${n} ${f(\"Leaks\")} here`", ["Found     here", "Leaks"]),
        ("const P = /^\\d{4}-'x'$/; y = 'ok'", ["ok"]),
        ("a = b / c; d = 'e' / 2", ["e"]),
        ("t = `${`in ${'deep'}`}`", [" ", "deep", "in  "]),
    ],
)
def test_string_literal_scanner(code: str, literals: list[str]) -> None:
    assert sorted(text for _, text in _string_literals(code)) == sorted(literals)


@pytest.mark.parametrize(
    ("literal", "visible"),
    [
        ("leaks", ""),
        ("leak-card", ""),
        ("/insights/leaks?month=", ""),
        ("leak-card state-over", ""),
        ("Potential leaks", "Potential leaks"),
        ("Leaks in October", "Leaks in October"),
        ("Showing state.leaks now", "Showing now"),
    ],
)
def test_visible_text(literal: str, visible: str) -> None:
    assert _visible_text(literal) == visible


def test_scan_finds_a_bare_leak() -> None:
    code = 'h("h2", { id: "leaks-heading", className: "leak-list" }, "Leaks this month");'

    assert _bare_leaks(code) == ["Leaks this month"]
    assert _bare_leaks(code.replace("Leaks this", "Potential leaks this")) == []


def test_the_dashboard_never_says_leak_without_potential() -> None:
    if not DASHBOARD_JS.is_file():
        pytest.skip(f"{DASHBOARD_JS.relative_to(ROOT)} doesn't exist yet")

    code = DASHBOARD_JS.read_text(encoding="utf-8")
    hits = _bare_leaks(code)

    # Not vacuous: the scanner reads the page's prose (about 50 texts in F08).
    assert len([lit for _, lit in _string_literals(code) if _visible_text(lit)]) > 20
    assert hits == [], 'the UI says "potential leak", never "leak" alone (decision 0023)'
