"""Item name normalisation, the lookup, the seed file and the `LookupCategorizer`
(decisions 0013, 0020; docs/wiki/backend/domain-logic.md#categorizepy-f7)."""

import re
import time
from decimal import Decimal
from typing import get_args

import pytest
import yaml

from app.api import schemas
from app.domain import categorize
from app.domain.categorize import (
    MAX_DESCRIPTION_CHARS,
    Normalized,
    fold,
    is_deposit,
    lookup,
    normalize,
)
from app.services.categorization import (
    CategorizedItem,
    ItemCategorizer,
    LookupCategorizer,
    paired_unit,
)
from app.services.dependencies import get_item_categorizer
from app.services.item_categories import (
    KNOWN_CATEGORIES,
    SEED_FILE,
    InvalidSeed,
    load_seed,
    parse_seed,
    read_seed,
)

# Real item strings: from the spike runs (data/spike) and the recorded responses.
REAL_STRINGS = [
    ("BIO BANANE 1 KG", "banane", "1", "kg"),
    ("KARTOFFELN FK 2,5KG", "kartoffeln", "2.5", "kg"),
    ("ÄPFEL PINK LADY 1KG", "aepfel pink lady", "1", "kg"),
    ("BIO EIER OKT 12 STK.", "eier okt", "12", "st"),
    ("BIO EIER 10 STK.", "eier", "10", "st"),
    ("RISPENTOMATEN KG-WARE", "rispentomaten", None, None),
    ("JOGHURT NACH GRIECH. A", "joghurt nach griechisch", None, None),
    ("Taschentücher Box 100s", "taschentuecher box", None, None),
    ("PFAND 0,25", "pfand", None, None),
    ("Pfandrückgabe", "pfand", None, None),
    ("LEERGUT", "leergut", None, None),
    ("VOLLMILCH 3,5%", "vollmilch", None, None),
    ("H-MILCH", "milch", None, None),
    ("PFLÜCKSALAT", "pfluecksalat", None, None),
    ("KAFFEE GEMAHLEN", "kaffee gemahlen", None, None),
    ("RUCOLA", "rucola", None, None),
    ("BROT", "brot", None, None),
    ("ZU ZAHLEN", "zu zahlen", None, None),
    ("ZURÜCK", "zurueck", None, None),
    ("BIOPLASTIK-KNOBENBUTE", "bioplastik knobenbute", None, None),
    ("Bio Olivenöl 10.0% FL", "olivenoel fl", None, None),
    ("Diesel-Schwefelarm addit.", "diesel schwefelarm additiviert", None, None),
    ("takimeki triangle cake chocolate 90g", "takimeki triangle cake chocolate", "90", "g"),
    # a count wins over a measure; the price goes
    (
        "takimeki triangle cake chocolate 90g 3 x 0.99",
        "takimeki triangle cake chocolate",
        "3",
        "st",
    ),
    ("wasserfilterpatronen nachfüll 1x Stück", "wasserfilterpatronen nachfuell", "1", "st"),
    (
        "3x50 bögen frischehaltedoseklemmen 2700ml",
        "3x50 boegen frischehaltedoseklemmen",
        "2700",
        "ml",
    ),
    ("15atck 301 lavendel", "15atck lavendel", None, None),
    # redaction placeholders (decision 0017) are not part of the name
    ("Bon-Nr: [id] Müsli", "bon nr muesli", None, None),
    ("", "", None, None),
]


@pytest.mark.parametrize(("description", "name", "qty", "unit"), REAL_STRINGS)
def test_normalize_real_receipt_strings(
    description: str, name: str, qty: str | None, unit: str | None
) -> None:
    expected = Normalized(name, Decimal(qty) if qty is not None else None, unit)

    assert normalize(description) == expected


@pytest.mark.parametrize(
    ("description", "name", "qty", "unit"),
    [
        # quantity and unit forms
        ("Zucker 500g", "zucker", "500", "g"),
        ("Zucker 500 G", "zucker", "500", "g"),
        ("Zucker 500gr", "zucker", "500", "g"),
        ("Cola 1,5l", "cola", "1.5", "l"),
        ("Cola 0.33 L", "cola", "0.33", "l"),
        ("Saft 1 ltr", "saft", "1", "l"),
        ("Sahne 200ml", "sahne", "200", "ml"),
        ("Wein 75cl", "wein", "75", "cl"),
        ("2x Cola 0,5l", "cola", "2", "st"),
        ("Cola 3 x", "cola", "3", "st"),
        ("Broetchen 6 St", "broetchen", "6", "st"),
        ("Eier 6 Stueck", "eier", "6", "st"),
        # a unit needs a number in front, and a number glued into a word stays
        ("G Gurke", "gurke", None, None),
        ("7UP", "7up", None, None),
        ("Gin 0,7L 40%", "gin", "0.7", "l"),
        # prices, percentages, negative amounts
        ("Rabatt -0,50", "rabatt", None, None),
        ("Preisvorteil 1,00-", "preisvorteil", None, None),
        ("Milch 1,5% 1,09", "milch", None, None),
        # qualifiers and own brands
        ("BIO Organic Frisch Lachs", "lachs", None, None),
        ("ja! Gouda", "gouda", None, None),
        ("K-Classic Butter 250g", "butter", "250", "g"),
        ("Gut&Günstig Milch 1,5l", "milch", "1.5", "l"),
        ("GUT & GÜNSTIG Sahne", "sahne", None, None),
        ("REWE Bio Bananen", "bananen", None, None),
        ("Kartoffeln mk lose", "kartoffeln", None, None),
        # abbreviations
        ("TK Erbsen 1kg", "tiefkuehl erbsen", "1", "kg"),
        ("SCHOKO Riegel", "schokolade riegel", None, None),
        ("Mineralw. 0,5l", "mineralwasser", "0.5", "l"),
        # punctuation, case, accents, whitespace
        ("Coca-Cola  Zero", "coca cola zero", None, None),
        ("Café Crème", "cafe creme", None, None),
        ("  STRASSE\tSÜSS ", "strasse suess", None, None),
        ("Süßkartoffeln", "suesskartoffeln", None, None),
        ("***", "", None, None),
        # deposit canonicalisation (decision 0020)
        ("Einwegpfand", "pfand", None, None),
        ("Mehrweg  PFAND", "pfand", None, None),
        ("PFANDRÜCKGABE", "pfand", None, None),
        ("Leergut-Bon", "leergut", None, None),
        ("2x Pfand 0,25", "pfand", "2", "st"),
        ("Pfannkuchen", "pfannkuchen", None, None),
    ],
)
def test_normalize_rules(description: str, name: str, qty: str | None, unit: str | None) -> None:
    expected = Normalized(name, Decimal(qty) if qty is not None else None, unit)

    assert normalize(description) == expected


@pytest.mark.parametrize("description", [name for name, *_ in REAL_STRINGS])
def test_normalize_is_idempotent_on_the_name(description: str) -> None:
    name = normalize(description).name

    assert normalize(name).name == name


def test_only_the_first_max_chars_are_read() -> None:
    long = "banane " + "x" * (MAX_DESCRIPTION_CHARS + 50) + " apfel"

    assert normalize(long).name == "banane " + "x" * (MAX_DESCRIPTION_CHARS - len("banane "))


@pytest.mark.parametrize(
    ("text", "folded"),
    [("ÄÖÜäöüß", "aeoeueaeoeuess"), ("Crème brûlée", "creme brulee"), ("ABC", "abc")],
)
def test_fold(text: str, folded: str) -> None:
    assert fold(text) == folded


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("pfand", True),
        ("leergut", True),
        ("Pfandrückgabe", True),
        ("pfandrueckgabe", True),
        ("einwegpfand", True),
        ("apfel", False),
        ("pfannkuchen", False),
        ("leer", False),
        ("", False),
    ],
)
def test_is_deposit(name: str, expected: bool) -> None:
    assert is_deposit(name) is expected


# ---------------------------------------------------------------- lookup

TABLE = {
    "banane": ("groceries.fresh", "seed"),
    "zwiebeln": ("groceries.fresh", "user"),
}


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("banane", ("groceries.fresh", "seed")),
        ("zwiebeln", ("groceries.fresh", "user")),
        ("bananen", ("uncategorized", "none")),  # exact match only
        ("banan", ("uncategorized", "none")),
        ("", ("uncategorized", "none")),
    ],
)
def test_lookup_is_an_exact_match(name: str, expected: tuple[str, str]) -> None:
    assert lookup(name, TABLE) == expected


def test_an_empty_name_never_matches_even_if_the_table_has_it() -> None:
    assert lookup("", {"": ("other", "user")}) == ("uncategorized", "none")


def test_a_user_entry_overrides_the_seed() -> None:
    table = {**TABLE, "banane": ("snacks_sweets", "user")}

    assert lookup("banane", table) == ("snacks_sweets", "user")


# ---------------------------------------------------------------- LookupCategorizer


def test_lookup_categorizer_normalises_then_looks_up() -> None:
    item = LookupCategorizer().categorize("BIO BANANE 1 KG", TABLE)

    assert item == CategorizedItem(
        normalized_name="banane",
        qty=Decimal("1"),
        unit="kg",
        category="groceries.fresh",
        category_source="seed",
    )


def test_lookup_categorizer_leaves_unknown_names_uncategorized() -> None:
    item = LookupCategorizer().categorize("ZWIEBELKUCHEN", TABLE)

    assert (item.normalized_name, item.category, item.category_source) == (
        "zwiebelkuchen",
        "uncategorized",
        "none",
    )


def test_the_provider_is_the_lookup_categorizer() -> None:
    categorizer: ItemCategorizer = get_item_categorizer()

    assert isinstance(categorizer, LookupCategorizer)


@pytest.mark.parametrize(
    ("qty", "item_qty", "item_unit", "unit"),
    [
        ("1", "1", "kg", "kg"),  # the normaliser's own qty: its unit stays
        ("1.0", "1", "kg", "kg"),
        ("3", "90", "g", None),  # the model's qty 3 with `90g`: "3 g" would be wrong
        (None, "1", "kg", None),
        ("1", None, None, None),
    ],
)
def test_the_unit_goes_with_its_own_qty(
    qty: str | None, item_qty: str | None, item_unit: str | None, unit: str | None
) -> None:
    item = CategorizedItem(
        "x", Decimal(item_qty) if item_qty else None, item_unit, "uncategorized", "none"
    )

    assert paired_unit(Decimal(qty) if qty else None, item) == unit


# ---------------------------------------------------------------- the seed file


def test_every_seed_key_is_normalised_and_every_value_a_known_category() -> None:
    seed = load_seed()

    assert len(seed) >= 150
    not_normalised = {name: normalize(name).name for name in seed if normalize(name).name != name}
    assert not_normalised == {}
    assert set(seed.values()) <= set(get_args(schemas.Category))


def test_the_seed_has_the_deposit_and_discount_entries() -> None:
    seed = load_seed()

    assert {name: seed[name] for name in ("pfand", "leergut", "rabatt", "preisvorteil")} == {
        "pfand": "deposit",
        "leergut": "deposit",
        "rabatt": "discount",
        "preisvorteil": "discount",
    }


def test_no_seed_name_is_listed_twice() -> None:
    """`parse_seed` rejects a repeat; the raw YAML must not have one hidden either."""
    path = categorize.__file__.replace("categorize.py", SEED_FILE)
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    names = [name for names in data.values() for name in names]

    assert len(names) == len(set(names)) == len(load_seed())


def test_known_categories_mirror_the_api_literal() -> None:
    assert set(get_args(schemas.Category)) == KNOWN_CATEGORIES


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (["banane"], "must map categories"),
        ({"food": ["banane"]}, "unknown category"),
        ({"other": "banane"}, "expected a list"),
        ({"other": [1]}, "non-empty string"),
        ({"other": ["  "]}, "non-empty string"),
        ({"other": ["banane"], "drinks": ["banane"]}, "listed twice"),
    ],
)
def test_parse_seed_rejects_a_broken_seed(data: object, message: str) -> None:
    with pytest.raises(InvalidSeed, match=message):
        parse_seed(data)


def test_read_seed_uses_safe_load(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "seed.yaml"
    path.write_text("other: !!python/object/apply:os.system ['true']\n", encoding="utf-8")

    with pytest.raises(yaml.YAMLError):
        read_seed(path)


# ---------------------------------------------------------------- slow-regex guard

# Same budget as the redaction guard (workflow.md, CI checks): linear code stays far below.
SLOW_S = 2.0

HOSTILE_UNITS = [
    "1",
    "1,",
    "1.",
    "1 ",
    "1x",
    "1 x ",
    "12 stk.",
    "1,5l",
    "1kg-",
    "-1",
    "100s",
    "3,5%",
    "%",
    "[id]",
    "[",
    "a-",
    "&",
    "ä",
    "pfan",
    "bio ",
    " ",
    "\t",
    ".",
    "*",
]


def hostile_runs(size: int) -> list[str]:
    runs = [(unit * (size // len(unit) + 1))[:size] for unit in HOSTILE_UNITS]
    receipt = "BIO BANANE 1 KG 1,99 Pfand 0,25 12 STK. 3 x 0.99 [id] Gut&Günstig "
    runs.append((receipt * (size // len(receipt) + 1))[:size])
    return runs


@pytest.mark.parametrize("size", [MAX_DESCRIPTION_CHARS, 65_536])
def test_normalize_finishes_on_hostile_input(size: int) -> None:
    for text in hostile_runs(size):
        start = time.perf_counter()
        normalize(text)
        elapsed = time.perf_counter() - start
        assert elapsed < SLOW_S, (text[:20], elapsed)


def test_every_regex_finishes_on_hostile_input_of_the_bounded_size() -> None:
    patterns = [value for value in vars(categorize).values() if isinstance(value, re.Pattern)]
    assert len(patterns) >= 6
    for pattern in patterns:
        for text in hostile_runs(MAX_DESCRIPTION_CHARS):
            start = time.perf_counter()
            for _ in pattern.finditer(text):
                pass
            elapsed = time.perf_counter() - start
            assert elapsed < SLOW_S, (pattern.pattern, text[:20], elapsed)
