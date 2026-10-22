"""Cost and itinerary calculations. No Streamlit or database code here.

The single cost model used everywhere:

    line_nzd = round_to_cents(amount × quantity × rate × basis_multiplier)

where ``rate`` is NZD per 1 unit of the line's currency and
``basis_multiplier`` is the number of travellers for per-person costs and 1
for whole-group costs. There is no separate "rooms" multiplier: a
whole-group nightly price already covers every room the group needs.

Transport legs have two components (base fare and additional costs), each
converted and rounded separately; the leg total is their sum.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .models import Destination, Expense, TransportLeg, Trip
from .money import MissingRateError, convert_to_nzd, round_money

TRANSPORT_CATEGORY = "transport"


def basis_multiplier(basis: str, travellers: int) -> int:
    if basis == "per_person":
        return max(int(travellers), 0)
    if basis == "group":
        return 1
    raise ValueError(f"Unknown cost basis: {basis!r}")


def line_total_nzd(
    unit_amount: Decimal,
    quantity: Decimal,
    currency: str,
    basis: str,
    travellers: int,
    rates: dict[str, Decimal],
) -> Decimal:
    """Group total in NZD, rounded to cents. Raises MissingRateError."""
    raw = unit_amount * quantity * basis_multiplier(basis, travellers)
    return round_money(convert_to_nzd(raw, currency, rates))


# --------------------------------------------------------------------------- dates


def nights_between(arrival: date | None, departure: date | None) -> int | None:
    """Nights at a stop, or None if dates are missing or invalid (departure before arrival)."""
    if arrival is None or departure is None:
        return None
    nights = (departure - arrival).days
    return nights if nights >= 0 else None


def stop_nights(dest: Destination) -> int | None:
    return nights_between(dest.arrival, dest.departure)


def itinerary_issues(trip: Trip, destinations: list[Destination]) -> list[str]:
    """Human-readable warnings. Never blocks saving — drafts may be incomplete."""
    issues: list[str] = []
    if trip.start_date and trip.end_date and trip.end_date < trip.start_date:
        issues.append("Trip end date is before the start date.")
    previous: Destination | None = None
    for index, dest in enumerate(destinations, start=1):
        label = f"Stop {index} ({dest.name or 'unnamed'})"
        if dest.arrival and dest.departure and dest.departure < dest.arrival:
            issues.append(f"{label}: departure is before arrival.")
        if trip.start_date and dest.arrival and dest.arrival < trip.start_date:
            issues.append(f"{label}: arrives before the trip start date.")
        if trip.end_date and dest.departure and dest.departure > trip.end_date:
            issues.append(f"{label}: departs after the trip end date.")
        if previous is not None and previous.departure and dest.arrival:
            if dest.arrival < previous.departure:
                issues.append(
                    f"{label}: arrives {dest.arrival:%d %b} but the previous stop "
                    f"({previous.name}) departs {previous.departure:%d %b} — overlap."
                )
        if dest.latitude is None or dest.longitude is None:
            issues.append(f"{label}: no coordinates, so it is not shown on the map.")
        previous = dest
    return issues


def _norm(place: str) -> str:
    return (place or "").strip().lower()


def missing_connections(
    destinations: list[Destination], legs: list[TransportLeg]
) -> list[tuple[Destination, Destination]]:
    """Consecutive stops that no active transport leg (or chain of legs) joins.

    A chain may pass through places that are not stops (e.g. "Kualanamu
    airport"), but not through another stop. Place names match ignoring case
    and surrounding spaces.
    """
    stop_names = {_norm(d.name) for d in destinations}
    graph: dict[str, set[str]] = defaultdict(set)
    for leg in legs:
        graph[_norm(leg.origin)].add(_norm(leg.destination))

    def reachable(start: str, goal: str) -> bool:
        seen, frontier = {start}, [start]
        while frontier:
            for nxt in graph.get(frontier.pop(), ()):
                if nxt == goal:
                    return True
                if nxt not in seen and nxt not in stop_names:
                    seen.add(nxt)
                    frontier.append(nxt)
        return False

    return [(a, b) for a, b in zip(destinations, destinations[1:]) if not reachable(_norm(a.name), _norm(b.name))]


def legs_in_stop_order(destinations: list[Destination], legs: list[TransportLeg]) -> list[TransportLeg]:
    """Stable sort of legs by the itinerary position of their origin stop.

    A leg starting somewhere that isn't a stop (e.g. an airport) stays right
    after the leg before it, so multi-leg chains are kept together.
    """
    index = {_norm(d.name): i for i, d in enumerate(destinations)}
    keyed, last = [], -1
    for pos, leg in enumerate(legs):
        last = index.get(_norm(leg.origin), last)
        keyed.append((last, pos, leg))
    return [leg for _, _, leg in sorted(keyed, key=lambda t: (t[0], t[1]))]


def unmatched_leg_places(destinations: list[Destination], legs: list[TransportLeg]) -> list[str]:
    """Leg origins/destinations that don't match any stop name (often a typo)."""
    stop_names = {_norm(d.name) for d in destinations}
    seen: dict[str, str] = {}
    for leg in legs:
        for place in (leg.origin, leg.destination):
            if _norm(place) not in stop_names:
                seen.setdefault(_norm(place), place)
    return list(seen.values())


# --------------------------------------------------------------------------- costs


def leg_components_nzd(leg: TransportLeg, trip: Trip) -> tuple[Decimal, Decimal]:
    """(base fare, additional costs) as group NZD, each rounded to cents."""
    base = line_total_nzd(leg.base_fare, Decimal(1), leg.currency, leg.basis, trip.travellers, trip.rates)
    extras = line_total_nzd(leg.extra_costs, Decimal(1), leg.currency, leg.basis, trip.travellers, trip.rates)
    return base, extras


def leg_total_nzd(leg: TransportLeg, trip: Trip) -> Decimal:
    base, extras = leg_components_nzd(leg, trip)
    return base + extras


def expense_quantity(expense: Expense, destinations_by_id: dict[int, Destination]) -> Decimal:
    """Quantity used in the calculation. Linked accommodation uses the stop's live nights."""
    if expense.nights_from_stop and expense.destination_id in destinations_by_id:
        nights = stop_nights(destinations_by_id[expense.destination_id])
        if nights is not None:
            return Decimal(nights)
    return expense.quantity


def expense_total_nzd(expense: Expense, trip: Trip, destinations_by_id: dict[int, Destination]) -> Decimal:
    quantity = expense_quantity(expense, destinations_by_id)
    return line_total_nzd(expense.unit_price, quantity, expense.currency, expense.basis, trip.travellers, trip.rates)


@dataclass
class CostLine:
    """One row of the budget breakdown. ``total_nzd`` is None if a rate is missing."""

    kind: str  # "transport" or "expense"
    record_id: int | None
    category: str
    description: str
    destination: str
    unit_amount: Decimal
    quantity: Decimal
    currency: str
    basis: str
    status: str
    total_nzd: Decimal | None
    problem: str = ""


def cost_lines(
    trip: Trip,
    legs: list[TransportLeg],
    expenses: list[Expense],
    destinations: list[Destination],
) -> list[CostLine]:
    dest_by_id = {d.id: d for d in destinations if d.id is not None}
    lines: list[CostLine] = []
    for leg in legs:
        try:
            total, problem = leg_total_nzd(leg, trip), ""
        except MissingRateError as exc:
            total, problem = None, str(exc)
        lines.append(
            CostLine(
                kind="transport",
                record_id=leg.id,
                category=TRANSPORT_CATEGORY,
                description=f"{leg.origin} → {leg.destination} ({leg.mode})",
                destination="",
                unit_amount=leg.base_fare + leg.extra_costs,
                quantity=Decimal(1),
                currency=leg.currency,
                basis=leg.basis,
                status=leg.status,
                total_nzd=total,
                problem=problem,
            )
        )
    for exp in expenses:
        problem = ""
        try:
            total = expense_total_nzd(exp, trip, dest_by_id)
        except MissingRateError as exc:
            total, problem = None, str(exc)
        if exp.nights_from_stop:
            linked = dest_by_id.get(exp.destination_id)
            if linked is None:
                problem = problem or "Linked to stop nights, but no stop is linked; using entered quantity."
            elif stop_nights(linked) is None:
                problem = problem or "Stop dates are missing/invalid; using entered quantity."
        dest = dest_by_id.get(exp.destination_id)
        lines.append(
            CostLine(
                kind="expense",
                record_id=exp.id,
                category=exp.category,
                description=exp.description,
                destination=dest.name if dest else "",
                unit_amount=exp.unit_price,
                quantity=expense_quantity(exp, dest_by_id),
                currency=exp.currency,
                basis=exp.basis,
                status=exp.status,
                total_nzd=total,
                problem=problem,
            )
        )
    return lines


@dataclass
class BudgetSummary:
    travellers: int
    total_nzd: Decimal
    per_traveller_nzd: Decimal | None
    budget_nzd: Decimal
    remaining_nzd: Decimal  # negative means overspend
    by_category: dict[str, Decimal] = field(default_factory=dict)
    by_status: dict[str, Decimal] = field(default_factory=dict)
    transport_hours: Decimal = Decimal("0")
    lines: list[CostLine] = field(default_factory=list)

    @property
    def problem_lines(self) -> list[CostLine]:
        return [line for line in self.lines if line.problem]

    @property
    def unpriced_lines(self) -> list[CostLine]:
        return [line for line in self.lines if line.total_nzd is None]


def per_person(total: Decimal, travellers: int) -> Decimal | None:
    return round_money(total / travellers) if travellers > 0 else None


def summarize(
    trip: Trip,
    legs: list[TransportLeg],
    expenses: list[Expense],
    destinations: list[Destination],
) -> BudgetSummary:
    """Totals for the active itinerary. Callers pass only active legs/expenses."""
    lines = cost_lines(trip, legs, expenses, destinations)
    by_category: dict[str, Decimal] = defaultdict(Decimal)
    by_status: dict[str, Decimal] = defaultdict(Decimal)
    for line in lines:
        if line.total_nzd is not None:
            by_category[line.category] += line.total_nzd
            by_status[line.status] += line.total_nzd
    total = sum(by_category.values(), Decimal("0"))
    return BudgetSummary(
        travellers=trip.travellers,
        total_nzd=total,
        per_traveller_nzd=per_person(total, trip.travellers),
        budget_nzd=trip.budget_nzd,
        remaining_nzd=trip.budget_nzd - total,
        by_category=dict(by_category),
        by_status=dict(by_status),
        transport_hours=sum((leg.duration_hours for leg in legs), Decimal("0")),
        lines=lines,
    )
