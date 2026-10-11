"""The non-item guard and the deposit-return words (decision 0026;
docs/wiki/backend/domain-logic.md#non_itemspy-f13).

A prompt is never the only safety net: the pipeline drops an item that is really a
payment, change or tax line, and negates a positive item that is a deposit return.

- `is_non_item(description)`: the folded description *starts* with one of the
  `NON_ITEM_WORDS` as a whole word (`SUMME`, `Summe EUR`, `MwSt 19%`, `EC-Karte`, `BAR`),
  so `BARILLA SPAGHETTI`, `Barbecue Sauce`, `Visavis Kekse` and `Steuerberater-Kalender`
  stay items. `netto` and `brutto` (`TAX_AMOUNT_WORDS`) count only when nothing but
  numbers and signs follow, because Netto is also a shop name. A deposit return
  (`Zurück Leergut`) is never a non-item.
- `is_deposit_return(description)`: one of the `DEPOSIT_RETURN_WORDS` is a whole word
  anywhere in the folded description (`LEERGUT`, `Leergut-Bon`, `Pfandrückgabe`,
  `Pfand-Rückgabe`, `Pfandbon`, `Pfand zurück`). A plain `Pfand` is not one: it can be a
  cost or a return, so it is only flagged (`deposit_sign_suspect`), never changed.

Folding is `categorize.fold` (lowercase, `ä ö ü ß` -> `ae oe ue ss`, accents dropped);
whitespace runs become one space. A word is a run of `a-z0-9`, so `-` and `.` end one.
The input is cut to `MAX_DESCRIPTION_CHARS` first and both regexes are linear (fixed
alternatives, no repeated groups), so a hostile description can't stall the pipeline; a
fixed-size test guards this (workflow.md, CI checks).
"""

import re

from app.domain.categorize import MAX_DESCRIPTION_CHARS, fold

NON_ITEM_WORDS = (
    "summe",
    "zwischensumme",
    "gesamtsumme",
    "zu zahlen",
    "gesamt",
    "bar",
    "gegeben",
    "rueckgeld",
    "zurueck",
    "ec-karte",
    "ec-kartenzahlung",
    "kartenzahlung",
    "girocard",
    "visa",
    "mastercard",
    "mwst",
    "ust",
    "steuer",
)
"""Payment, change and tax-table words; an item starting with one is dropped."""

TAX_AMOUNT_WORDS = (
    "netto",
    "brutto",
)
"""Tax-table words that are also names (the chain Netto): an item is dropped only when
nothing but numbers and signs follow the word (`NETTO 12,62`, not `NETTO Spuelmittel`)."""

DEPOSIT_RETURN_WORDS = (
    "leergut",
    "leergutbon",
    "rueckgabe",
    "pfandrueckgabe",
    "pfandbon",
    "pfand zurueck",
)
"""Words that always mean money back for returned bottles."""


def _alternation(words: tuple[str, ...]) -> str:
    # Longest first, so a word that starts another (`leergut`, `leergutbon`) can't
    # shadow it; the lookarounds make each a whole word either way.
    return "|".join(re.escape(word) for word in sorted(words, key=len, reverse=True))


_NON_ITEM = re.compile(rf"(?:{_alternation(NON_ITEM_WORDS)})(?![a-z0-9])")
_TAX_AMOUNT = re.compile(rf"(?:{_alternation(TAX_AMOUNT_WORDS)})(?![a-z0-9])[^a-z]*$")
_DEPOSIT_RETURN = re.compile(rf"(?<![a-z0-9])(?:{_alternation(DEPOSIT_RETURN_WORDS)})(?![a-z0-9])")


def folded(description: str) -> str:
    """The description as the rules see it: cut, folded, whitespace runs as one space."""
    return " ".join(fold(description[:MAX_DESCRIPTION_CHARS]).split())


def is_deposit_return(description: str) -> bool:
    """True if a whole word of the description says money back for bottles."""
    return _DEPOSIT_RETURN.search(folded(description)) is not None


def is_non_item(description: str) -> bool:
    """True if the description starts with a payment, change or tax word."""
    text = folded(description)
    starts_with_non_item = _NON_ITEM.match(text) or _TAX_AMOUNT.match(text)
    return starts_with_non_item is not None and _DEPOSIT_RETURN.search(text) is None
