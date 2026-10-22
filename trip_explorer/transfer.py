"""JSON export/import of a whole trip, and CSV export of the budget breakdown.

Imports are fully parsed and validated into a ``TripBundle`` *before* any
database write; ``Repository.save_bundle`` then inserts everything in a
single transaction as a new trip, so a bad file never changes saved data.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date
from decimal import Decimal
from typing import Any

from .calculations import BudgetSummary
from .models import (
    BOOKING_STATUSES,
    COST_BASES,
    EXPENSE_CATEGORIES,
    TRANSPORT_MODES,
    Destination,
    Expense,
    RouteOption,
    TransportLeg,
    Trip,
    TripBundle,
)
from .money import normalise_currency, round_money, to_decimal

FORMAT_NAME = "trip-budget-explorer"
FORMAT_VERSION = 1


class ImportValidationError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


# --------------------------------------------------------------------------- export


def _plain(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _record(obj, exclude: tuple[str, ...] = ("trip_id", "position")) -> dict[str, Any]:
    return {k: _plain(v) for k, v in obj.__dict__.items() if k not in exclude}


def bundle_to_dict(bundle: TripBundle) -> dict[str, Any]:
    trip = _record(bundle.trip, exclude=("id",))
    trip["rates_nzd_per_unit"] = {code: str(rate) for code, rate in trip.pop("rates").items()}
    return {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "rate_convention": "NZD per 1 unit of foreign currency",
        "trip": trip,
        "destinations": [_record(d) for d in bundle.destinations],
        "legs": [_record(l) for l in bundle.legs],
        "expenses": [_record(e) for e in bundle.expenses],
        "route_options": [_record(o) for o in bundle.options],
    }


def export_json(bundle: TripBundle) -> str:
    return json.dumps(bundle_to_dict(bundle), indent=2, ensure_ascii=False)


# --------------------------------------------------------------------------- import


class _Reader:
    """Collects validation errors instead of stopping at the first one."""

    def __init__(self):
        self.errors: list[str] = []

    def text(self, rec: dict, key: str, where: str, required: bool = False) -> str:
        value = rec.get(key, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            self.errors.append(f"{where}: '{key}' must be text")
            return ""
        if required and not value.strip():
            self.errors.append(f"{where}: '{key}' is required")
        return value

    def decimal(self, rec: dict, key: str, where: str, default: str = "0", minimum: Decimal | None = Decimal(0)) -> Decimal:
        raw = rec.get(key, default)
        try:
            value = to_decimal(raw)
        except ValueError:
            self.errors.append(f"{where}: '{key}' is not a valid number ({raw!r})")
            return Decimal(default)
        if minimum is not None and value < minimum:
            self.errors.append(f"{where}: '{key}' must be at least {minimum}")
        return value

    def date(self, rec: dict, key: str, where: str) -> date | None:
        raw = rec.get(key)
        if raw in (None, ""):
            return None
        try:
            return date.fromisoformat(raw)
        except (TypeError, ValueError):
            self.errors.append(f"{where}: '{key}' must be a YYYY-MM-DD date ({raw!r})")
            return None

    def coordinate(self, rec: dict, key: str, where: str, limit: float) -> float | None:
        raw = rec.get(key)
        if raw in (None, ""):
            return None
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not -limit <= raw <= limit:
            self.errors.append(f"{where}: '{key}' must be a number between −{limit} and {limit}")
            return None
        return float(raw)

    def choice(self, rec: dict, key: str, where: str, choices: tuple[str, ...], default: str) -> str:
        value = rec.get(key, default)
        if value not in choices:
            self.errors.append(f"{where}: '{key}' must be one of {', '.join(choices)} (got {value!r})")
            return default
        return value

    def optional_id(self, rec: dict, key: str, where: str) -> int | None:
        value = rec.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            self.errors.append(f"{where}: '{key}' must be an integer id or null")
            return None
        return value

    def records(self, data: dict, key: str) -> list[dict]:
        value = data.get(key, [])
        if not isinstance(value, list) or not all(isinstance(r, dict) for r in value):
            self.errors.append(f"'{key}' must be a list of objects")
            return []
        return value


def parse_bundle(data: Any) -> TripBundle:
    """Validate exported JSON data and build a TripBundle. Raises ImportValidationError."""
    r = _Reader()
    if not isinstance(data, dict):
        raise ImportValidationError(["File must contain a JSON object"])
    if data.get("format") != FORMAT_NAME:
        raise ImportValidationError([f"Not a {FORMAT_NAME} export (missing or wrong 'format')"])
    if data.get("version") != FORMAT_VERSION:
        raise ImportValidationError([f"Unsupported version {data.get('version')!r}; expected {FORMAT_VERSION}"])
    t = data.get("trip")
    if not isinstance(t, dict):
        raise ImportValidationError(["'trip' must be an object"])

    rates: dict[str, Decimal] = {}
    raw_rates = t.get("rates_nzd_per_unit", {})
    if not isinstance(raw_rates, dict):
        r.errors.append("trip: 'rates_nzd_per_unit' must be an object")
        raw_rates = {}
    for code, raw in raw_rates.items():
        try:
            rate = to_decimal(raw)
            if rate <= 0:
                raise ValueError
            rates[normalise_currency(code)] = rate
        except ValueError:
            r.errors.append(f"trip: rate for {code!r} must be a positive number")

    travellers = t.get("travellers", 1)
    if isinstance(travellers, bool) or not isinstance(travellers, int) or travellers < 1:
        r.errors.append("trip: 'travellers' must be a whole number of at least 1")
        travellers = 1

    trip = Trip(
        name=r.text(t, "name", "trip", required=True),
        start_date=r.date(t, "start_date", "trip"),
        end_date=r.date(t, "end_date", "trip"),
        travellers=travellers,
        budget_nzd=r.decimal(t, "budget_nzd", "trip"),
        rates=rates,
        notes=r.text(t, "notes", "trip"),
        is_demo=bool(t.get("is_demo", False)),
        # Optional: files exported before these settings existed still import.
        time_value_nzd_per_hour=r.decimal(t, "time_value_nzd_per_hour", "trip"),
        disruption_penalty_hours=r.decimal(t, "disruption_penalty_hours", "trip"),
    )

    destinations = []
    for i, rec in enumerate(r.records(data, "destinations"), start=1):
        where = f"destination {i}"
        destinations.append(Destination(
            trip_id=None,
            id=r.optional_id(rec, "id", where),
            name=r.text(rec, "name", where, required=True),
            country=r.text(rec, "country", where),
            arrival=r.date(rec, "arrival", where),
            departure=r.date(rec, "departure", where),
            latitude=r.coordinate(rec, "latitude", where, 90),
            longitude=r.coordinate(rec, "longitude", where, 180),
            notes=r.text(rec, "notes", where),
        ))

    options = []
    for i, rec in enumerate(r.records(data, "route_options"), start=1):
        where = f"route option {i}"
        options.append(RouteOption(
            trip_id=None,
            id=r.optional_id(rec, "id", where),
            name=r.text(rec, "name", where, required=True),
            origin=r.text(rec, "origin", where),
            destination=r.text(rec, "destination", where),
            notes=r.text(rec, "notes", where),
            headline_leg_id=r.optional_id(rec, "headline_leg_id", where),
            applied_at=r.text(rec, "applied_at", where),
        ))

    legs = []
    for i, rec in enumerate(r.records(data, "legs"), start=1):
        where = f"leg {i}"
        legs.append(TransportLeg(
            trip_id=None,
            id=r.optional_id(rec, "id", where),
            option_id=r.optional_id(rec, "option_id", where),
            origin=r.text(rec, "origin", where),
            destination=r.text(rec, "destination", where),
            mode=r.choice(rec, "mode", where, TRANSPORT_MODES, "other"),
            depart_local=r.text(rec, "depart_local", where),
            arrive_local=r.text(rec, "arrive_local", where),
            duration_hours=r.decimal(rec, "duration_hours", where),
            base_fare=r.decimal(rec, "base_fare", where),
            extra_costs=r.decimal(rec, "extra_costs", where),
            currency=normalise_currency(r.text(rec, "currency", where, required=True)),
            basis=r.choice(rec, "basis", where, COST_BASES, "per_person"),
            status=r.choice(rec, "status", where, BOOKING_STATUSES, "estimated"),
            notes=r.text(rec, "notes", where),
            source_url=r.text(rec, "source_url", where),
            disruptive=bool(rec.get("disruptive", False)),
        ))

    expenses = []
    for i, rec in enumerate(r.records(data, "expenses"), start=1):
        where = f"expense {i}"
        expenses.append(Expense(
            trip_id=None,
            id=r.optional_id(rec, "id", where),
            option_id=r.optional_id(rec, "option_id", where),
            destination_id=r.optional_id(rec, "destination_id", where),
            description=r.text(rec, "description", where, required=True),
            category=r.choice(rec, "category", where, EXPENSE_CATEGORIES, "misc"),
            unit_price=r.decimal(rec, "unit_price", where),
            quantity=r.decimal(rec, "quantity", where, default="1"),
            currency=normalise_currency(r.text(rec, "currency", where, required=True)),
            basis=r.choice(rec, "basis", where, COST_BASES, "group"),
            status=r.choice(rec, "status", where, BOOKING_STATUSES, "estimated"),
            nights_from_stop=bool(rec.get("nights_from_stop", False)),
            notes=r.text(rec, "notes", where),
        ))

    # Cross-references. Currencies without a rate are allowed (drafts); the app
    # flags those lines as unpriced rather than refusing them.
    dest_ids = {d.id for d in destinations if d.id is not None}
    option_ids = {o.id for o in options if o.id is not None}
    leg_ids = {l.id for l in legs if l.id is not None}
    for kind, items in (("destination", destinations), ("route option", options), ("leg", legs)):
        ids = [x.id for x in items if x.id is not None]
        if len(ids) != len(set(ids)):
            r.errors.append(f"Duplicate {kind} ids")
    for i, leg in enumerate(legs, start=1):
        if leg.option_id is not None and leg.option_id not in option_ids:
            r.errors.append(f"leg {i}: refers to unknown route option {leg.option_id}")
    for i, exp in enumerate(expenses, start=1):
        if exp.option_id is not None and exp.option_id not in option_ids:
            r.errors.append(f"expense {i}: refers to unknown route option {exp.option_id}")
        if exp.destination_id is not None and exp.destination_id not in dest_ids:
            r.errors.append(f"expense {i}: refers to unknown destination {exp.destination_id}")
    for i, opt in enumerate(options, start=1):
        if opt.headline_leg_id is not None and opt.headline_leg_id not in leg_ids:
            r.errors.append(f"route option {i}: headline leg {opt.headline_leg_id} not found")

    if r.errors:
        raise ImportValidationError(r.errors)
    return TripBundle(trip=trip, destinations=destinations, legs=legs, expenses=expenses, options=options)


def parse_json(text: str | bytes) -> TripBundle:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ImportValidationError([f"Invalid JSON: {exc}"]) from exc
    return parse_bundle(data)


# --------------------------------------------------------------------------- CSV


CSV_COLUMNS = [
    "category", "description", "destination", "unit_amount", "quantity", "currency",
    "basis", "status", "group_total_nzd", "problem",
]


def budget_csv(summary: BudgetSummary) -> str:
    """Active-itinerary line items. Amounts are whole-group NZD."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for line in summary.lines:
        writer.writerow([
            line.category, line.description, line.destination, line.unit_amount, line.quantity,
            line.currency, line.basis, line.status,
            "" if line.total_nzd is None else line.total_nzd, line.problem,
        ])
    writer.writerow([])
    padding = [""] * (len(CSV_COLUMNS) - 3)
    for label, value in (
        ("TOTAL (group NZD)", summary.total_nzd),
        ("Per traveller (NZD)", summary.per_traveller_nzd),
        ("Budget (group NZD)", summary.budget_nzd),
        ("Remaining (group NZD; negative = overspend)", summary.remaining_nzd),
    ):
        writer.writerow([label, *padding, "" if value is None else round_money(value), ""])
    return buffer.getvalue()
