"""The non-item guard and the deposit-return words (decision 0026;
docs/wiki/backend/domain-logic.md#non_itemspy-f13).

A prompt is never the only safety net: the pipeline drops an item that is really a
payment, change or tax line, and negates a positive item that is a deposit return.

- `is_non_item(description)`: the folded description *starts* with one of the
  `NON_ITEM_WORDS` as a whole word (`SUMME`, `Summe EUR`, `MwSt 19%`, `EC Karte`), so
  `BARILLA SPAGHETTI`, `Visavis Kekse` and `Steuerberater-Kalender` stay items. The
  `AMOUNT_ONLY_WORDS` (`bar`, `visa`, `mastercard`, `total`, `netto`, `brutto`, `steuer`)
  are also product or shop names (a Bar Riegel, a VISA gift card, the chain Netto), so
  they count only when nothing but an amount follows: numbers and signs, a lone tax class
  letter `a`-`d`, `eur`, `euro`, `zahlung`, `kontaktlos` or `contactless` (`BAR 20,00
  EUR`, `Brutto 19% A 15,02`, `VISA ****1234`; not `BAR NUSS SCHOKO`, `VISA
  Geschenkkarte 50`). A tax-table row (`A 19 % 1,66 0,32`) is a non-item too. A
  change line that is a deposit return (`Zurück Leergut`) is not a non-item, but a
  total of returns (`SUMME LEERGUT`) is, or the returns would count twice.
- `is_deposit_return(description)`: one of the `DEPOSIT_RETURN_WORDS` is a whole word
  anywhere in the folded description (`LEERGUT`, `Leergut-Bon`, `Pfandrückgabe`,
  `Pfand-Rückgabe`, `Pfandbon`, `Pfand zurück`). A plain `Pfand` is not one: it can be a
  cost or a return, so it is only flagged (`deposit_sign_suspect`), never changed.

Folding is `categorize.fold` (lowercase, `ä ö ü ß` -> `ae oe ue ss`, accents dropped);
`-` counts as a space (`EC-Karte` = `EC Karte`) and whitespace runs become one space. A
word is a run of `a-z0-9`, so `.` ends one too.
The input is cut to `MAX_DESCRIPTION_CHARS` first and both regexes are linear (fixed
alternatives, no repeated groups), so a hostile description can't stall the pipeline; a
fixed-size test guards this (workflow.md, CI checks).
"""

import re

from app.domain.categorize import MAX_DESCRIPTION_CHARS, fold

NON_ITEM_WORDS = (
    "summe",
    "zwischensumme",
    "zw summe",
    "zw. summe",
    "gesamtsumme",
    "gesamtbetrag",
    "zu zahlen",
    "gesamt",
    "gegeben",
    "rueckgeld",
    "wechselgeld",
    "zurueck",
    "ec karte",
    "ec karten",
    "ec kartenzahlung",
    "ec zahlung",
    "ec cash",
    "kartenzahlung",
    "kreditkarte",
    "girocard",
    "mwst",
    "ust",
)
"""Payment, change and tax-table words; an item starting with one is dropped."""

AMOUNT_ONLY_WORDS = (
    "bar",
    "visa",
    "mastercard",
    "total",
    "netto",
    "brutto",
    "steuer",
)
"""Payment and tax words that are also names: dropped only when an amount alone follows."""

DEPOSIT_RETURN_WORDS = (
    "leergut",
    "leergutbon",
    "rueckgabe",
    "pfandrueckgabe",
    "pfandbon",
    "pfand zurueck",
    "zurueck pfand",
    "leergutrueckgabe",
    "flaschenrueckgabe",
)
"""Words that always mean money back for returned bottles."""


def _alternation(words: tuple[str, ...]) -> str:
    # Longest first, so a word that starts another (`leergut`, `leergutbon`) can't
    # shadow it; the lookarounds make each a whole word either way.
    return "|".join(re.escape(word) for word in sorted(words, key=len, reverse=True))


_NON_ITEM = re.compile(rf"(?:{_alternation(NON_ITEM_WORDS)})(?![a-z0-9])")
# After the word only an amount: non-letters, a lone tax class letter, eur/euro/zahlung.
# The alternatives start with different characters, so the loop never backtracks far.
_AMOUNT_TAIL = (
    r"(?:[^a-z]|[a-d](?![a-z0-9])|euro?(?![a-z0-9])|zahlung(?![a-z0-9])"
    r"|kontaktlos(?![a-z0-9])|contactless(?![a-z0-9]))*$"
)
_AMOUNT_ONLY = re.compile(rf"(?:{_alternation(AMOUNT_ONLY_WORDS)})(?![a-z0-9]){_AMOUNT_TAIL}")
_CHANGE = re.compile(r"zurueck(?![a-z0-9])")
# A row of the tax table: a class letter and a rate, then only numbers (`A 19 % 1,66`).
_TAX_ROW = re.compile(r"[a-d] ?\d{1,2}(?:[.,]\d{1,2})? ?%[^a-z]*$")
_DEPOSIT_RETURN = re.compile(rf"(?<![a-z0-9])(?:{_alternation(DEPOSIT_RETURN_WORDS)})(?![a-z0-9])")


def folded(description: str) -> str:
    """The description as the rules see it: cut, folded, whitespace runs as one space."""
    return " ".join(fold(description[:MAX_DESCRIPTION_CHARS]).replace("-", " ").split())


def is_deposit_return(description: str) -> bool:
    """True if a whole word of the description says money back for bottles."""
    return _DEPOSIT_RETURN.search(folded(description)) is not None


def is_non_item(description: str) -> bool:
    """True if the description starts with a payment, change or tax word."""
    text = folded(description)
    if not (_NON_ITEM.match(text) or _AMOUNT_ONLY.match(text) or _TAX_ROW.match(text)):
        return False
    # `Zurück Leergut` is money back for bottles, not the change; `SUMME LEERGUT` is
    # still a total.
    return not (_CHANGE.match(text) and _DEPOSIT_RETURN.search(text))
