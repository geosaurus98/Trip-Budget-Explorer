"""What-if scenarios for route comparisons: how robust is a saving?

Scenarios never touch saved data; they build adjusted copies of the trip,
legs and expenses and re-run the normal calculations on them.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from .comparison import Comparison
from .models import Expense, TransportLeg, Trip
from .money import round_money

HUNDRED = Decimal(100)


def _factor(change_pct: Decimal) -> Decimal:
    if change_pct <= -HUNDRED:
        raise ValueError("A change of −100% or less would make prices zero or negative")
    return 1 + change_pct / HUNDRED


def with_rate_change(trip: Trip, change_pct: Decimal) -> Trip:
    """Copy of the trip with every foreign exchange rate moved by ``change_pct``.

    +10% means each foreign currency costs 10% more NZD (NZD prices are unchanged).
    """
    factor = _factor(change_pct)
    return replace(trip, rates={code: rate * factor for code, rate in trip.rates.items()})


def with_price_change(
    legs: list[TransportLeg], expenses: list[Expense], change_pct: Decimal
) -> tuple[list[TransportLeg], list[Expense]]:
    """Copies with every fare, additional cost and unit price moved by ``change_pct``."""
    factor = _factor(change_pct)
    return (
        [replace(l, base_fare=l.base_fare * factor, extra_costs=l.extra_costs * factor) for l in legs],
        [replace(e, unit_price=e.unit_price * factor) for e in expenses],
    )


@dataclass
class SavingMargin:
    cheaper: str  # "A" or "B"
    # Cheaper option's costs may rise this much before the saving is gone.
    # None if it costs nothing, so no percentage rise can remove the saving.
    cheaper_rise_pct: Decimal | None
    other_fall_pct: Decimal  # or the other option's costs may fall this much


def saving_margin(c: Comparison) -> SavingMargin | None:
    """How far prices can move before the cheaper option stops being cheaper (None if equal)."""
    if c.cheaper is None:
        return None
    cheap, other = (c.a, c.b) if c.cheaper == "A" else (c.b, c.a)
    saving = other.total_nzd - cheap.total_nzd
    return SavingMargin(
        cheaper=c.cheaper,
        cheaper_rise_pct=round_money(saving / cheap.total_nzd * HUNDRED) if cheap.total_nzd else None,
        other_fall_pct=round_money(saving / other.total_nzd * HUNDRED),
    )
