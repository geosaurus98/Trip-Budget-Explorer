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
