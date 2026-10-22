"""Plain data structures for trips, stops, transport legs, expenses and route options.

These classes hold data only. Calculations live in ``calculations.py`` and
``comparison.py``; database access lives in ``storage.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

BASE_CURRENCY = "NZD"

TRANSPORT_MODES = ("flight", "train", "bus", "ferry", "taxi/private transfer", "other")
BOOKING_STATUSES = ("estimated", "researched", "booked")
COST_BASES = ("per_person", "group")
EXPENSE_CATEGORIES = ("accommodation", "food", "activities", "misc")


@dataclass
class Trip:
    name: str
    start_date: date | None = None
    end_date: date | None = None
    travellers: int = 1
    budget_nzd: Decimal = Decimal("0")
    # Exchange rates are always "NZD per 1 unit of foreign currency".
    rates: dict[str, Decimal] = field(default_factory=dict)
    notes: str = ""
    is_demo: bool = False
    # Personal trade-off settings for "is the cheaper route worth it?".
    # How much one hour of one traveller's time is worth, in NZD (0 = not set).
    time_value_nzd_per_hour: Decimal = Decimal("0")
    # Extra hours of "disruption" added to a leg marked as disruptive
    # (overnight, very early start, etc.) when weighing time against money.
    disruption_penalty_hours: Decimal = Decimal("0")
    # Optional names, used to record who paid for what. Ideally one per traveller.
    traveller_names: list[str] = field(default_factory=list)
    id: int | None = None


@dataclass
class Destination:
    trip_id: int | None
    name: str
    country: str = ""
    arrival: date | None = None
    departure: date | None = None
    latitude: float | None = None
    longitude: float | None = None
    notes: str = ""
    position: int = 0
    id: int | None = None


@dataclass
class TransportLeg:
    trip_id: int | None
    origin: str
    destination: str
    mode: str = "other"
    # Local reference times as free text, e.g. "2027-06-13 08:30". No timezone maths.
    depart_local: str = ""
    arrive_local: str = ""
    duration_hours: Decimal = Decimal("0")
    base_fare: Decimal = Decimal("0")
    extra_costs: Decimal = Decimal("0")
    currency: str = BASE_CURRENCY
    basis: str = "per_person"
    status: str = "estimated"
    notes: str = ""
    source_url: str = ""
    # Overnight, very early or otherwise disruptive travel. Adds the trip's
    # disruption penalty hours in time-value comparisons (not to raw hours).
    disruptive: bool = False
    # Optional deadlines: book before this date / cancel free of charge until this date.
    book_by: date | None = None
    cancel_by: date | None = None
    paid_by: str = ""  # traveller name; "" = not paid yet / not recorded
    position: int = 0
    # None means the leg is part of the active itinerary; otherwise it belongs
    # to a route option and is excluded from active totals.
    option_id: int | None = None
    id: int | None = None


@dataclass
class Expense:
    trip_id: int | None
    description: str
    category: str = "misc"
    destination_id: int | None = None
    unit_price: Decimal = Decimal("0")
    quantity: Decimal = Decimal("1")
    currency: str = BASE_CURRENCY
    basis: str = "group"
    status: str = "estimated"
    # Accommodation only: when True and a destination is linked, the quantity
    # is the stop's number of nights (recalculated live from its dates).
    nights_from_stop: bool = False
    notes: str = ""
    book_by: date | None = None
    cancel_by: date | None = None
    paid_by: str = ""
    option_id: int | None = None
    id: int | None = None


@dataclass
class RouteOption:
    trip_id: int | None
    name: str
    origin: str
    destination: str
    notes: str = ""
    # The leg whose base fare is the "headline" fare for break-even analysis.
    headline_leg_id: int | None = None
    applied_at: str = ""
    id: int | None = None


@dataclass
class TodoItem:
    """A trip checklist item (visa, insurance, vaccinations…)."""

    trip_id: int | None
    title: str
    due: date | None = None
    done: bool = False
    notes: str = ""
    id: int | None = None


@dataclass
class CostSnapshot:
    """A leg's or expense's cost fields as saved at one moment.

    For legs, ``amount`` is base fare + additional costs and ``quantity`` is 1.
    For expenses, ``amount`` is the unit price.
    """

    record_type: str  # "leg" or "expense"
    record_id: int
    recorded_at: str  # ISO timestamp, local time
    status: str
    amount: Decimal
    quantity: Decimal
    currency: str
    basis: str


@dataclass
class TripBundle:
    """A whole trip with all its records, used for import/export and seeding.

    Inside a bundle, ``id`` fields act as local keys: expenses reference
    destinations by ``destination_id``, legs/expenses reference options by
    ``option_id`` and options reference legs by ``headline_leg_id``. Storage
    remaps them to fresh database ids when the bundle is saved.
    """

    trip: Trip
    destinations: list[Destination] = field(default_factory=list)
    legs: list[TransportLeg] = field(default_factory=list)
    expenses: list[Expense] = field(default_factory=list)
    options: list[RouteOption] = field(default_factory=list)
    todos: list[TodoItem] = field(default_factory=list)
