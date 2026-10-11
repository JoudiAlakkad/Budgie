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
  `Pfandbon`, `Pfand zurück`). A plain `Pfand` is not one: it can be a cost or a return,
  so it is only flagged (`deposit_sign_suspect`), never changed.

Folding is `categorize.fold` (lowercase, `ä ö ü ß` -> `ae oe ue ss`, accents dropped);
`-` counts as a space (`EC-Karte` = `EC Karte`) and whitespace runs become one space. A
word is a run of `a-z0-9`, so `.` ends one too.
The input is cut to `MAX_DESCRIPTION_CHARS` first and both regexes are linear (fixed
alternatives, no repeated groups), so a hostile description can't stall the pipeline; a
fixed-size test guards this (workflow.md, CI checks).

Since decision 0027 (the first v3 host test):
- `is_non_item` is also true for a description with no letter at all (`219,000`,
  `4711`, `-`, `12,99 €`). A blank description is kept (the user names it), and so is a
  quantity line (`3 × 0,89`), which `merge_quantity_lines` handles.
- `is_quantity_line(description)` reads a line that is only a quantity and a price
  (`24 x 0,49 €`, `1,066 kg x 0,99 €/kg`, `-53 x 0,25 €`) as the signed `(N, price)`.
- `merge_quantity_lines(lines)` merges each quantity line into the next item, else the
  previous one, if that has a name and isn't a deposit (unless N x price is negative):
  qty |N|, and the line's amount when it equals N x price within 0.02.
- `paid_total(dropped, total)`: the agreed amount of the dropped `zu zahlen` /
  `gesamtbetrag` lines replaces a null or different total.
- A bare `rueckgabe` is no longer a return word: `Pfand / Rückgabe` (ALDI's charge line)
  and `Pfand-Rückgabe` are a plain Pfand.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.domain.categorize import MAX_DESCRIPTION_CHARS, fold, is_deposit
from app.domain.money import SUM_TOLERANCE, cents

MATCH_TOLERANCE = SUM_TOLERANCE
"""How far two amounts may differ and still be the same (the sum check's 0.02)."""

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
    "pfandrueckgabe",
    "pfandbon",
    "pfand zurueck",
    "zurueck pfand",
    "leergutrueckgabe",
    "flaschenrueckgabe",
    "flaschen rueckgabe",
    "leergutruecknahme",
    "leergutrueckname",
    "pfandruecknahme",
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


def has_letter(text: str) -> bool:
    """True if `text` has a letter of any script (`€`, `×`, `%` and digits are none)."""
    return any(ch.isalpha() for ch in text[:MAX_DESCRIPTION_CHARS])


def is_non_item(description: str) -> bool:
    """True if the description starts with a payment, change or tax word, or has no
    letter at all (decision 0027); a blank one and a quantity line are kept."""
    text = folded(description)
    if not has_letter(text):
        return bool(description[:MAX_DESCRIPTION_CHARS].strip()) and (
            is_quantity_line(description) is None
        )
    if not (_NON_ITEM.match(text) or _AMOUNT_ONLY.match(text) or _TAX_ROW.match(text)):
        return False
    # `Zurück Leergut` is money back for bottles, not the change; `SUMME LEERGUT` is
    # still a total.
    return not (_CHANGE.match(text) and _DEPOSIT_RETURN.search(text))


# --- quantity lines, the paid total (decision 0027) -----------------------------------

_NUMBER = r"[0-9]{1,6}(?:[.,][0-9]{1,3})?"
_SIGN = "[-+\u2212]?"
# `24 x 0,49 €`, `1,066 kg x 0,99 €/kg`, `-53 x 0,25 €`, `3 × 0,89`, `2 stk x 1.50 eur`.
# Every part is optional or bounded and the alternatives are fixed, so a failed
# fullmatch backtracks only a few steps per position.
_QUANTITY_LINE = re.compile(
    rf"(?P<qsign>{_SIGN}) ?(?P<qty>{_NUMBER}) ?(?P<unit>kg|g|l|stueck|stk|st)?\.? ?[x\u00d7] ?"
    rf"(?P<psign>{_SIGN}) ?(?P<price>{_NUMBER}) ?(?:€|eur)? ?(?:/ ?(?:kg|stk|st))?"
    # a tax class the model copied after the price (`2 x 0,49 € A`, `3 x 0,89 1`)
    r"(?: [a-d0-9])?"
)
_PAID_LINE = re.compile(r"(?:zu zahlen|gesamtbetrag)(?![a-z0-9])")


def _signed(sign: str, number: str) -> Decimal:
    value = Decimal(number.replace(",", "."))
    return -value if sign in ("-", "\u2212") else value


def is_quantity_line(description: str) -> tuple[Decimal, Decimal] | None:
    """`(N, price)`, both signed as printed, if the description is only a quantity line;
    else None. `-` keeps its meaning here (unlike `folded`), and N may not be 0."""
    text = " ".join(fold(description[:MAX_DESCRIPTION_CHARS]).split())
    found = _QUANTITY_LINE.fullmatch(text)
    if found is None:
        return None
    qty = _signed(found["qsign"], found["qty"])
    if qty == 0:
        return None
    return qty, _signed(found["psign"], found["price"])


def quantity_unit(description: str) -> str | None:
    """The weight or volume unit of a quantity line (`1,066 kg x 0,99 €/kg` -> `kg`);
    None for pieces (`stk`, `st`, none) or a line that isn't one."""
    found = _QUANTITY_LINE.fullmatch(" ".join(fold(description[:MAX_DESCRIPTION_CHARS]).split()))
    if found is None or found["unit"] not in ("kg", "g", "l"):
        return None
    return found["unit"]


def is_deposit_line(description: str) -> bool:
    """A Pfand or Leergut item: `categorize.is_deposit` (a word starting or ending with
    `pfand` / `leergut`) or a deposit-return word (`Flaschenrückgabe`)."""
    return is_deposit(description) or is_deposit_return(description)


@dataclass(frozen=True)
class ItemLine:
    """One item as the guard sees it: the redacted description, the amount, the qty."""

    description: str
    amount: Decimal
    qty: Decimal | None = None
    unit: str | None = None


def is_non_item_line(line: ItemLine) -> bool:
    """`is_non_item`, except that a negative line without letters is kept: it is a
    discount printed as `-0,50` or `-30%` (found by `/code-review`)."""
    if line.amount < 0 and not has_letter(folded(line.description)):
        return False
    return is_non_item(line.description)


def _has_name(description: str) -> bool:
    return (
        has_letter(folded(description))
        and is_quantity_line(description) is None
        and not is_non_item(description)
    )


def merge_quantity_lines(lines: Sequence[ItemLine]) -> tuple[list[ItemLine], int]:
    """The items with each quantity line merged into its product, and the merge count.

    The neighbour is the previous item when its amount already is N x price within 0.02
    (`RINDERHACK 4,17` / `3 x 1,39 €` / `BANANEN`: the line belongs to the item above),
    else the next item, else the previous one if its amount is the unit price. It
    qualifies if it has a name (a letter, not a quantity line, not a non-item), hasn't
    absorbed a line yet, and is a deposit exactly when N x price is negative (`-53 x 0,25
    €` goes only into `LEERGUT`, a positive line never into a deposit). The merged
    item keeps the neighbour's description, takes qty |N|, and the line's amount if that
    is N x price within 0.02, else keeps its own. A line without one stays an item.
    """
    parsed = [is_quantity_line(line.description) for line in lines]
    result: list[ItemLine | None] = list(lines)
    absorbed: set[int] = set()
    merged = 0
    for index, quantity in enumerate(parsed):
        if quantity is None:
            continue
        n, price = quantity
        product = n * price
        line = lines[index]

        def qualifies(neighbour: int, product: Decimal = product) -> bool:
            if not 0 <= neighbour < len(lines) or neighbour in absorbed:
                return False
            target = result[neighbour]
            if target is None or parsed[neighbour] is not None:
                return False
            if not _has_name(target.description):
                return False
            # A negative line (`-53 x 0,25 €`) is a return: only into a deposit line. A
            # positive one never goes into a deposit line.
            return is_deposit_line(target.description) == (product < 0)

        # The previous item only when its amount already is N x price (it comes first then)
        # or the unit price; any other amount of it is a real item's and must not be
        # replaced (found by the reviewer: BROT 2.49 / 3 x 0,89 / PFAND lost the 2.49).
        before = index - 1
        previous_amount = lines[before].amount if before >= 0 else None
        previous_is_line_total = previous_amount is not None and (
            abs(previous_amount - product) <= MATCH_TOLERANCE
        )
        previous_is_price = previous_amount is not None and (
            abs(previous_amount - price) <= MATCH_TOLERANCE
        )
        if previous_is_line_total:
            order: tuple[int, ...] = (before, index + 1)
        elif previous_is_price:
            order = (index + 1, before)
        else:
            order = (index + 1,)
        for neighbour in order:
            if not qualifies(neighbour):
                continue
            target = result[neighbour]
            if target is None:  # qualifies() checked it; this narrows the type
                continue
            matches = abs(line.amount - product) <= MATCH_TOLERANCE
            result[neighbour] = ItemLine(
                target.description,
                line.amount if matches else target.amount,
                abs(n),
                quantity_unit(line.description),
            )
            result[index] = None
            absorbed.add(neighbour)
            merged += 1
            break
    return [line for line in result if line is not None], merged


def paid_total(
    dropped: Sequence[ItemLine], total: Decimal | None, items_sum: Decimal | None = None
) -> Decimal | None:
    """The total to use instead of `total`, or None to keep it.

    From the dropped lines with a positive amount, those starting with `zu zahlen` or
    `gesamtbetrag` (not `summe`: tax tables print one too), in cents. They must agree
    within 0.02. A null `total` takes the first one; another `total` only when the kept
    items (`items_sum`) add up to the paid amount and not to `total`.
    """
    paid = [
        cents(line.amount)
        for line in dropped
        if line.amount > 0 and _PAID_LINE.match(folded(line.description))
    ]
    if not paid or max(paid) - min(paid) > MATCH_TOLERANCE:
        return None
    if total is None:
        return paid[0]
    if abs(total - paid[0]) <= MATCH_TOLERANCE:
        return None
    # A model total replaced only when the kept items add up to the paid line and not to
    # the model's total (found by the reviewer: a misread ZU ZAHLEN 219.0 or 0.0 must not
    # overwrite a correct 54.40).
    if items_sum is None:
        return None
    matches_paid = abs(cents(items_sum) - paid[0]) <= MATCH_TOLERANCE
    matches_model = abs(cents(items_sum) - cents(total)) <= MATCH_TOLERANCE
    return paid[0] if matches_paid and not matches_model else None
