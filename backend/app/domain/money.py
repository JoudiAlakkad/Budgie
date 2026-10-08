"""Rounding money to cents, the one helper for the domain and the services."""

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def cents(value: Decimal) -> Decimal:
    """Round half up to 0.01.

    `quantize` works in the current decimal context, so a caller that needs more than the
    default 28 digits (the sum check in `validation.py`, `SUM_PRECISION`) wraps the call
    in `decimal.localcontext`; this function doesn't fix a precision of its own.
    """
    return value.quantize(CENT, rounding=ROUND_HALF_UP)
