"""Spread the active budget over the days of the trip.

Allocation rules (all amounts whole-group NZD):

* Accommodation linked to a stop with valid dates: split evenly over its nights.
* Transport legs: on the date in "Departure (local)" if it starts with
  YYYY-MM-DD, otherwise on the departure date of the stop the leg leaves from.
* Food linked to a stop: split over the stop's nights. Food not linked to a
  stop: split over every night of the trip (needs trip dates).
* Activities and misc linked to a stop: on the stop's arrival date.
* Anything else is "not tied to a date".

Splits are done in whole cents, with leftover cents on the first days, so
days + undated always add up exactly to the budget total.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from .calculations import cost_lines
from .models import Destination, Expense, TransportLeg, Trip


def split_evenly(amount: Decimal, parts: int) -> list[Decimal]:
    """Split a cent amount into ``parts`` cent amounts that sum exactly to it."""
    if parts <= 0:
        raise ValueError("parts must be positive")
    cents = int((amount * 100).to_integral_value())
    sign = -1 if cents < 0 else 1
    base, remainder = divmod(abs(cents), parts)
    return [Decimal(sign * (base + (1 if i < remainder else 0))) / 100 for i in range(parts)]


def nights_of(arrival: date | None, departure: date | None) -> list[date]:
    if arrival is None or departure is None or departure <= arrival:
        return []
    return [arrival + timedelta(days=i) for i in range((departure - arrival).days)]


def _leg_date(leg: TransportLeg, stops_by_name: dict[str, Destination]) -> date | None:
    try:
        return date.fromisoformat(leg.depart_local.strip()[:10])
    except ValueError:
        pass
    stop = stops_by_name.get(leg.origin.strip().lower())
    return stop.departure if stop else None


@dataclass
class DayRow:
    day: date
    stop: str  # where you sleep that night ("" if unknown)
    by_category: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))

    @property
    def total(self) -> Decimal:
        return sum(self.by_category.values(), Decimal("0"))


@dataclass
class DailyBreakdown:
    days: list[DayRow]
    undated: dict[str, Decimal]
    undated_items: list[str]

    @property
    def undated_total(self) -> Decimal:
        return sum(self.undated.values(), Decimal("0"))


def daily_breakdown(
    trip: Trip,
    legs: list[TransportLeg],
    expenses: list[Expense],
    destinations: list[Destination],
) -> DailyBreakdown:
    dest_by_id = {d.id: d for d in destinations if d.id is not None}
    stops_by_name = {d.name.strip().lower(): d for d in destinations}
    trip_nights = nights_of(trip.start_date, trip.end_date)

    allocated: dict[date, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    undated: dict[str, Decimal] = defaultdict(Decimal)
    undated_items: list[str] = []

    def spread(category: str, amount: Decimal, days: list[date]) -> None:
        for day, part in zip(days, split_evenly(amount, len(days))):
            allocated[day][category] += part

    by_leg_id = {leg.id: leg for leg in legs}
    by_exp_id = {exp.id: exp for exp in expenses}
    for line in cost_lines(trip, legs, expenses, destinations):
        if line.total_nzd is None:
            continue  # unpriced (missing rate): excluded from totals too
        days: list[date] = []
        if line.kind == "transport":
            leg_day = _leg_date(by_leg_id[line.record_id], stops_by_name)
            days = [leg_day] if leg_day else []
        else:
            exp = by_exp_id[line.record_id]
            stop = dest_by_id.get(exp.destination_id)
            if exp.category in ("accommodation", "food") and stop is not None:
                days = nights_of(stop.arrival, stop.departure) or ([stop.arrival] if stop.arrival else [])
            elif exp.category == "food":
                days = trip_nights
            elif stop is not None and stop.arrival:
                days = [stop.arrival]
        if days:
            spread(line.category, line.total_nzd, days)
        else:
            undated[line.category] += line.total_nzd
            undated_items.append(line.description)

    all_days = set(allocated) | set(trip_nights)
    rows = []
    if all_days:
        first, last = min(all_days), max(all_days)
        for offset in range((last - first).days + 1):
            day = first + timedelta(days=offset)
            sleeping = next((d.name for d in destinations if day in nights_of(d.arrival, d.departure)), "")
            rows.append(DayRow(day=day, stop=sleeping, by_category=allocated.get(day, defaultdict(Decimal))))
    return DailyBreakdown(days=rows, undated=dict(undated), undated_items=undated_items)
