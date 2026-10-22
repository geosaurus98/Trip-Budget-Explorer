"""Compare two route options between the same start and end points.

All money figures are whole-group NZD unless a name says ``per_person``.
All durations are the entered door-to-door hours.

Break-even ("maximum extra repositioning spend") definition
-----------------------------------------------------------
Each option has one *headline fare*: the base fare of its main leg (for
example the flight). The option with the lower headline fare is the
"cheap-fare option".

* initial allowance = headline fare (other option) − headline fare (cheap-fare option)
  How much more you could spend on everything else and still break even.
* extra costs = non-headline costs (cheap-fare option) − non-headline costs (other option)
  Non-headline costs are every other cost of the option: other legs' fares,
  baggage, transfers, extra hotel nights, meals, etc.
* remaining allowance = initial allowance − extra costs
  (algebraically equal to total(other) − total(cheap-fare option)).

If the remaining allowance is negative the cheaper fare has already lost its
saving; the excess is reported instead of a positive allowance.

Time value ("is it worth it?")
------------------------------
* effective hours = door-to-door hours + disruption penalty hours for each
  leg marked disruptive (overnight, very early start…).
* time cost (group NZD) = time value per person-hour × travellers × effective hours.
* generalised cost = total cost + time cost. The option with the lower
  generalised cost is the better deal for you.
* break-even time value (NZD per person-hour) = cost saving ÷ (extra effective
  hours × travellers). If one traveller-hour is worth less than this to you,
  the cheaper-but-slower option is worth it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .calculations import cost_lines, leg_components_nzd, per_person
from .models import Destination, Expense, RouteOption, TransportLeg, Trip
from .money import MissingRateError, round_money


@dataclass
class OptionSummary:
    option: RouteOption
    legs: list[TransportLeg]
    expenses: list[Expense]
    total_nzd: Decimal
    per_person_nzd: Decimal | None
    transport_nzd: Decimal
    accommodation_nzd: Decimal
    other_expenses_nzd: Decimal
    headline_leg: TransportLeg | None
    headline_fare_nzd: Decimal
    headline_auto_selected: bool
    travel_hours: Decimal
    transfers: int
    effective_hours: Decimal = Decimal("0")  # travel hours + disruption penalties
    disruptive_legs: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def non_headline_nzd(self) -> Decimal:
        return self.total_nzd - self.headline_fare_nzd


def _headline_leg(option: RouteOption, legs: list[TransportLeg], trip: Trip) -> tuple[TransportLeg | None, bool]:
    """The chosen headline leg, else the leg with the largest base fare (auto)."""
    for leg in legs:
        if option.headline_leg_id is not None and leg.id == option.headline_leg_id:
            return leg, False
    best, best_fare = None, None
    for leg in legs:
        try:
            fare, _ = leg_components_nzd(leg, trip)
        except MissingRateError:
            continue
        if best_fare is None or fare > best_fare:
            best, best_fare = leg, fare
    return best, True


def route_warnings(option: RouteOption, legs: list[TransportLeg]) -> list[str]:
    warnings = []
    if not legs:
        return ["No transport legs entered yet."]
    norm = lambda s: (s or "").strip().lower()  # noqa: E731
    if norm(legs[0].origin) != norm(option.origin):
        warnings.append(f"First leg starts at '{legs[0].origin}', not the option start '{option.origin}'.")
    if norm(legs[-1].destination) != norm(option.destination):
        warnings.append(f"Last leg ends at '{legs[-1].destination}', not the option end '{option.destination}'.")
    for prev, nxt in zip(legs, legs[1:]):
        if norm(prev.destination) != norm(nxt.origin):
            warnings.append(f"Gap between legs: '{prev.destination}' → '{nxt.origin}'.")
    return warnings


def summarize_option(
    trip: Trip,
    option: RouteOption,
    legs: list[TransportLeg],
    expenses: list[Expense],
    destinations: list[Destination],
) -> OptionSummary:
    lines = cost_lines(trip, legs, expenses, destinations)
    warnings = route_warnings(option, legs)
    warnings += [f"{line.description}: {line.problem}" for line in lines if line.problem]

    def total_of(predicate) -> Decimal:
        return sum((l.total_nzd for l in lines if l.total_nzd is not None and predicate(l)), Decimal("0"))

    transport = total_of(lambda l: l.kind == "transport")
    accommodation = total_of(lambda l: l.category == "accommodation")
    other = total_of(lambda l: l.kind == "expense" and l.category != "accommodation")
    total = transport + accommodation + other

    headline, auto = _headline_leg(option, legs, trip)
    headline_fare = Decimal("0")
    if headline is not None:
        try:
            headline_fare, _ = leg_components_nzd(headline, trip)
        except MissingRateError:
            pass

    travel_hours = sum((leg.duration_hours for leg in legs), Decimal("0"))
    disruptive = sum(1 for leg in legs if leg.disruptive)
    return OptionSummary(
        option=option,
        legs=legs,
        expenses=expenses,
        total_nzd=total,
        per_person_nzd=per_person(total, trip.travellers),
        transport_nzd=transport,
        accommodation_nzd=accommodation,
        other_expenses_nzd=other,
        headline_leg=headline,
        headline_fare_nzd=headline_fare,
        headline_auto_selected=auto,
        travel_hours=travel_hours,
        transfers=max(len(legs) - 1, 0),
        effective_hours=travel_hours + disruptive * trip.disruption_penalty_hours,
        disruptive_legs=disruptive,
        warnings=warnings,
    )


@dataclass
class BreakEven:
    cheap_fare_label: str | None  # "A", "B" or None when headline fares are equal
    initial_allowance_nzd: Decimal
    extra_costs_nzd: Decimal
    remaining_allowance_nzd: Decimal  # may be negative

    @property
    def excess_nzd(self) -> Decimal:
        """How far extra costs exceed the fare saving (0 if they don't)."""
        return max(-self.remaining_allowance_nzd, Decimal("0"))

    @property
    def saving_survives(self) -> bool:
        return self.remaining_allowance_nzd > 0


def time_cost_nzd(summary: OptionSummary, value_per_person_hour: Decimal, travellers: int) -> Decimal:
    """Group NZD value of the option's effective travel time."""
    return round_money(value_per_person_hour * travellers * summary.effective_hours)


def generalised_cost_nzd(summary: OptionSummary, value_per_person_hour: Decimal, travellers: int) -> Decimal:
    return summary.total_nzd + time_cost_nzd(summary, value_per_person_hour, travellers)


@dataclass
class TimeValueVerdict:
    value_per_person_hour: Decimal  # 0 means "not set"
    time_cost_a_nzd: Decimal
    time_cost_b_nzd: Decimal
    generalised_a_nzd: Decimal
    generalised_b_nzd: Decimal
    better: str | None  # lower generalised cost; None if equal or value not set
    break_even_value_per_person_hour: Decimal | None  # only for a cheaper-but-slower trade-off
    verdict: str


def _time_value(a: OptionSummary, b: OptionSummary, travellers: int, value: Decimal) -> TimeValueVerdict:
    cost_diff = b.total_nzd - a.total_nzd
    cheaper = None if cost_diff == 0 else ("A" if cost_diff > 0 else "B")
    break_even = None
    if cheaper and travellers > 0:
        cheap, other = (a, b) if cheaper == "A" else (b, a)
        extra_hours = cheap.effective_hours - other.effective_hours
        if extra_hours > 0:
            break_even = round_money(abs(cost_diff) / (extra_hours * travellers))

    gen_a = generalised_cost_nzd(a, value, travellers)
    gen_b = generalised_cost_nzd(b, value, travellers)
    better = None
    if value > 0 and gen_a != gen_b:
        better = "A" if gen_a < gen_b else "B"

    if break_even is None:
        if cheaper is None:
            verdict = "Same cost, so the decision is purely about time and comfort."
        else:
            verdict = (f"No trade-off: option {cheaper} is cheaper without taking longer "
                       "(including disruption), so it wins at any time value.")
    elif value <= 0:
        verdict = (f"Option {cheaper} is worth it only if one traveller-hour is worth less than "
                   f"NZD {break_even:,.2f} to you. Set your time value in Settings for a direct verdict.")
    elif better == cheaper:
        verdict = (f"Worth it: at NZD {value:,.2f} per person-hour, option {cheaper}'s saving outweighs its extra "
                   f"time (break-even NZD {break_even:,.2f}).")
    elif better is None:
        verdict = f"Break-even: at NZD {value:,.2f} per person-hour both options cost you the same overall."
    else:
        verdict = (f"Not worth it: at NZD {value:,.2f} per person-hour, option {cheaper}'s extra time costs more "
                   f"than it saves (break-even NZD {break_even:,.2f}). Option {better} is the better deal.")

    return TimeValueVerdict(
        value_per_person_hour=value,
        time_cost_a_nzd=time_cost_nzd(a, value, travellers),
        time_cost_b_nzd=time_cost_nzd(b, value, travellers),
        generalised_a_nzd=gen_a,
        generalised_b_nzd=gen_b,
        better=better,
        break_even_value_per_person_hour=break_even,
        verdict=verdict,
    )


@dataclass
class Comparison:
    a: OptionSummary
    b: OptionSummary
    travellers: int
    cost_difference_nzd: Decimal  # total B − total A
    time_difference_hours: Decimal  # hours B − hours A
    cheaper: str | None  # "A", "B" or None if equal
    faster: str | None
    savings_per_extra_hour_nzd: Decimal | None
    verdict: str
    break_even: BreakEven
    time_value: TimeValueVerdict
    warnings: list[str] = field(default_factory=list)

    def per_person(self, value: Decimal) -> Decimal | None:
        return per_person(value, self.travellers)


def _break_even(a: OptionSummary, b: OptionSummary) -> BreakEven:
    if a.headline_fare_nzd == b.headline_fare_nzd:
        label, cheap, other = None, a, b
    elif a.headline_fare_nzd < b.headline_fare_nzd:
        label, cheap, other = "A", a, b
    else:
        label, cheap, other = "B", b, a
    initial = other.headline_fare_nzd - cheap.headline_fare_nzd
    extra = cheap.non_headline_nzd - other.non_headline_nzd
    return BreakEven(
        cheap_fare_label=label,
        initial_allowance_nzd=initial,
        extra_costs_nzd=extra,
        remaining_allowance_nzd=initial - extra,
    )


def compare_options(
    a: OptionSummary, b: OptionSummary, travellers: int, time_value_nzd_per_hour: Decimal = Decimal("0")
) -> Comparison:
    cost_diff = b.total_nzd - a.total_nzd
    time_diff = b.travel_hours - a.travel_hours
    cheaper = None if cost_diff == 0 else ("A" if cost_diff > 0 else "B")
    faster = None if time_diff == 0 else ("A" if time_diff > 0 else "B")
    saving, extra_hours = abs(cost_diff), abs(time_diff)

    savings_per_hour: Decimal | None = None
    if cheaper is None and faster is None:
        verdict = "Both options cost the same and take the same time."
    elif cheaper is None:
        verdict = f"Same cost; option {faster} is {extra_hours} h faster, so it is the better choice."
    elif faster is None:
        verdict = f"Same door-to-door time; option {cheaper} saves {_fmt(saving)} with no extra travel time."
    elif cheaper == faster:
        verdict = f"Option {cheaper} is both cheaper (by {_fmt(saving)}) and faster (by {extra_hours} h)."
    else:
        savings_per_hour = round_money(saving / extra_hours)
        verdict = (
            f"Option {cheaper} saves {_fmt(saving)} for the group but takes {extra_hours} h longer: "
            f"{_fmt(savings_per_hour)} saved per extra travel hour."
        )

    warnings = []
    norm = lambda s: (s or "").strip().lower()  # noqa: E731
    if norm(a.option.origin) != norm(b.option.origin) or norm(a.option.destination) != norm(b.option.destination):
        warnings.append(
            "The two options do not share the same start and end points, so the comparison may not be like-for-like."
        )

    return Comparison(
        a=a,
        b=b,
        travellers=travellers,
        cost_difference_nzd=cost_diff,
        time_difference_hours=time_diff,
        cheaper=cheaper,
        faster=faster,
        savings_per_extra_hour_nzd=savings_per_hour,
        verdict=verdict,
        break_even=_break_even(a, b),
        time_value=_time_value(a, b, travellers, time_value_nzd_per_hour),
        warnings=warnings,
    )


def _fmt(value: Decimal) -> str:
    return f"NZD {round_money(value):,.2f}"
