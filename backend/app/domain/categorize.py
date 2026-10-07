"""Item name normalisation and the exact-match category lookup (decisions 0013, 0020;
docs/wiki/backend/domain-logic.md#categorizepy-f7).

`normalize` turns a receipt line into the key of the `item_categories` table, with the
quantity and unit it read from the line; `lookup` finds the key in a table the caller
loaded. Everything is deterministic and pure: no model, no database.

Steps of `normalize`, in order:
1. cut the text to `MAX_DESCRIPTION_CHARS`, lowercase it, fold `ä ö ü ß` to `ae oe ue ss`,
   drop other accents (`é` -> `e`) and redaction placeholders (`[id]`, `[card]`, `[iban]`)
2. take out the quantity and unit: a count (`2x`, `3 x`, `12 stk`, `2 st`) wins over a
   measure (`1 kg`, `500g`, `2,5kg`, `1,5l`); the first match of the winning kind counts,
   and every quantity token is removed from the name
3. drop pack counts (`100s`), percentages (`3,5%`) and prices or other bare numbers
   (`0,99`, `1.99`, `301`)
4. deposit canonicalisation (decision 0020): a name containing `pfand` becomes `pfand`,
   one containing `leergut` becomes `leergut`
5. per word: expand abbreviations (`tk` -> `tiefkuehl`, `h-milch` -> `milch`), drop
   qualifiers (`bio`, `frisch`, own brands such as `ja!`, `k-classic`, `gut&guenstig`),
   split the rest on punctuation (`coca-cola` -> `coca cola`) and check the parts again;
   one-letter parts are dropped
6. join with single spaces

All regexes are linear (no nested or adjacent unbounded quantifiers over the same
characters), and the input is cut first, so a hostile line can't stall a request; a
fixed-size test guards this (workflow.md, CI checks).
"""

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

# Item names are short; anything after this is ignored. Bounds the work per line.
MAX_DESCRIPTION_CHARS = 1024

UNCATEGORIZED = "uncategorized"
NO_SOURCE = "none"

# `item_categories` as the lookup sees it: normalized name -> (category, source).
CategoryTable = Mapping[str, tuple[str, str]]

DEPOSIT_MARKERS = ("pfand", "leergut")

_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})

# A count of pieces: `2x`, `3 x`, `12 stk`, `12 stk.`, `2 st`, `1 stueck`.
_COUNT = re.compile(r"(?<![\w.,])(\d{1,4})[ ]?(x|stk|stueck|st)(?!\w)\.?")
# A measure: `1 kg`, `500g`, `2,5kg`, `1,5l`, `0.33 l`, `250 ml`.
_MEASURE = re.compile(r"(?<![\w.,])(\d{1,6}(?:[.,]\d{1,3})?)[ ]?(kg|gr|g|ltr|l|ml|cl)(?!\w)\.?")
# A pack count printed after the name, e.g. `Box 100s` (100 sheets); dropped, not a qty.
_PACK = re.compile(r"(?<![\w.,])\d{1,4}s(?!\w)")
# The placeholders redaction writes into a description (decision 0017).
_PLACEHOLDER = re.compile(r"\[(?:id|card|iban)\]")
_PERCENT = re.compile(r"(?<![\w.,])\d{1,6}(?:[.,]\d{1,3})?[ ]?%")
# Prices and bare numbers, e.g. `0,99`, `-1.99`, `301`; not digits inside a word (`7up`).
_NUMBER = re.compile(r"(?<![\w.,])-?\d{1,12}(?:[.,]\d{1,3})?(?![\w%])")
_NON_WORD = re.compile(r"[^a-z0-9]+")

_UNITS = {"kg": "kg", "gr": "g", "g": "g", "ltr": "l", "l": "l", "ml": "ml", "cl": "cl"}
COUNT_UNIT = "st"

# Words dropped from a name: quality labels and own-brand prefixes that say nothing about
# what the item is. Matched against a whole word (with its inner `-`, `&`, `!`) and against
# each part after splitting on punctuation.
QUALIFIERS = frozenset(
    {
        # labels
        "bio",
        "organic",
        "oeko",
        "frisch",
        "fresh",
        "kg-ware",
        "kgware",
        "ware",
        "lose",
        "stk",
        "stueck",
        # potato varieties (festkochend, mehligkochend, vorwiegend festkochend)
        "fk",
        "mk",
        "vfk",
        # own brands of the German chains
        "ja!",
        "ja",
        "k-classic",
        "k-bio",
        "gut&guenstig",
        "gut&gunstig",
        "gut",
        "guenstig",
        "rewe",
        "edeka",
        "aldi",
        "lidl",
        "penny",
        "netto",
        "kaufland",
        "milsani",
        "milbona",
        "alnatura",
        "enerbio",
        "naturgut",
        "dmbio",
    }
)

# Receipt abbreviations, matched like the qualifiers. Values are already normalised.
ABBREVIATIONS = {
    "tk": "tiefkuehl",
    "h-milch": "milch",
    "hmilch": "milch",
    "griech": "griechisch",
    "schoko": "schokolade",
    "mineralw": "mineralwasser",
    "addit": "additiviert",
}


@dataclass(frozen=True)
class Normalized:
    """A normalised item name and the quantity and unit read from the line."""

    name: str
    qty: Decimal | None = None
    unit: str | None = None


def fold(text: str) -> str:
    """Lowercase, `ä ö ü ß` -> `ae oe ue ss`, other accents dropped."""
    folded = text.lower().translate(_FOLD)
    decomposed = unicodedata.normalize("NFKD", folded)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def is_deposit(name: str) -> bool:
    """True for a Pfand or Leergut line (decision 0019); matches on the folded text."""
    folded = fold(name)
    return any(marker in folded for marker in DEPOSIT_MARKERS)


def _decimal(text: str) -> Decimal:
    return Decimal(text.replace(",", "."))


def _quantity(text: str) -> tuple[Decimal | None, str | None]:
    """The first count, else the first measure; (None, None) if there is neither."""
    count = _COUNT.search(text)
    if count is not None:
        return Decimal(count.group(1)), COUNT_UNIT
    measure = _MEASURE.search(text)
    if measure is not None:
        return _decimal(measure.group(1)), _UNITS[measure.group(2)]
    return None, None


def _word(word: str) -> str | None:
    """A word's replacement: its expansion, None for a qualifier, else the word itself."""
    if word in ABBREVIATIONS:
        return ABBREVIATIONS[word]
    if word in QUALIFIERS:
        return None
    return word


def _words(token: str) -> list[str]:
    """One whitespace token as normalised words."""
    core = token.strip(".,;:!?*'\"()[]{}/+#=<>|_~`")
    for candidate in (token, core):
        if candidate in ABBREVIATIONS or candidate in QUALIFIERS:
            replaced = _word(candidate)
            return [replaced] if replaced else []
    words = []
    for part in _NON_WORD.split(core):
        replaced = _word(part)
        if replaced and len(replaced) > 1:
            words.append(replaced)
    return words


def normalize(description: str) -> Normalized:
    """The lookup key of a receipt line, e.g. `BIO BANANE 1 KG` -> `banane` (1, kg)."""
    text = _PLACEHOLDER.sub(" ", fold(description[:MAX_DESCRIPTION_CHARS]))
    qty, unit = _quantity(text)
    text = _COUNT.sub(" ", text)
    text = _MEASURE.sub(" ", text)
    text = _PACK.sub(" ", text)
    text = _PERCENT.sub(" ", text)
    text = _NUMBER.sub(" ", text)
    for marker in DEPOSIT_MARKERS:
        if marker in text:
            return Normalized(marker, qty, unit)
    words = [word for token in text.split() for word in _words(token)]
    return Normalized(" ".join(words), qty, unit)


def lookup(name: str, table: CategoryTable) -> tuple[str, str]:
    """`(category, source)` for an exact match, else `("uncategorized", "none")`."""
    if not name:
        return UNCATEGORIZED, NO_SOURCE
    entry = table.get(name)
    if entry is None:
        return UNCATEGORIZED, NO_SOURCE
    return entry
