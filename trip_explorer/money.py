"""Decimal helpers for money and currency conversion.

Convention: exchange rates are "NZD per 1 unit of foreign currency", so
``amount_in_nzd = amount_in_foreign * rate``. NZD always has rate 1.

Rounding strategy: conversions are done at full Decimal precision and each
cost line is rounded once to whole cents (ROUND_HALF_UP). Totals are sums of
those rounded lines, so a breakdown always adds up to its total.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .models import BASE_CURRENCY

CENT = Decimal("0.01")


class MissingRateError(ValueError):
    """Raised when a cost uses a currency with no exchange rate entered."""

    def __init__(self, currency: str):
        super().__init__(f"No exchange rate entered for {currency}")
        self.currency = currency


def to_decimal(value) -> Decimal:
    """Convert user/database input to Decimal. Floats go via str to avoid binary noise."""
    if isinstance(value, Decimal):
        result = value
    elif value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError("A number is required")
    elif isinstance(value, bool):
        raise ValueError("Booleans are not numbers")
    elif isinstance(value, (int, float, str)):
        try:
            result = Decimal(str(value).strip().replace(",", ""))
        except InvalidOperation as exc:
            raise ValueError(f"Not a valid number: {value!r}") from exc
    else:
        raise ValueError(f"Not a valid number: {value!r}")
    if not result.is_finite():
        raise ValueError(f"Not a finite number: {value!r}")
    return result


def round_money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def normalise_currency(code: str) -> str:
    return (code or "").strip().upper()


def rate_for(currency: str, rates: dict[str, Decimal]) -> Decimal:
    code = normalise_currency(currency)
    if code == BASE_CURRENCY:
        return Decimal("1")
    if code not in rates:
        raise MissingRateError(code)
    return rates[code]


def convert_to_nzd(amount: Decimal, currency: str, rates: dict[str, Decimal]) -> Decimal:
    """Convert at full precision (unrounded). Round with ``round_money`` at the line level."""
    return amount * rate_for(currency, rates)


def split_evenly(amount: Decimal, parts: int) -> list[Decimal]:
    """Split a cent amount into ``parts`` cent amounts that sum exactly to it."""
    if parts <= 0:
        raise ValueError("parts must be positive")
    cents = int((amount * 100).to_integral_value())
    sign = -1 if cents < 0 else 1
    base, remainder = divmod(abs(cents), parts)
    return [Decimal(sign * (base + (1 if i < remainder else 0))) / 100 for i in range(parts)]


def format_nzd(value: Decimal | None, signed: bool = False) -> str:
    if value is None:
        return "—"
    rounded = round_money(value)
    sign = ""
    if rounded < 0:
        sign = "−"
    elif signed and rounded > 0:
        sign = "+"
    return f"{sign}NZD {abs(rounded):,.2f}"


def format_amount(value: Decimal, currency: str) -> str:
    return f"{normalise_currency(currency)} {value:,.2f}"
