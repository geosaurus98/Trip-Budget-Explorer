"""SQLite persistence. The UI talks to ``Repository``; it never writes SQL itself.

Money and durations are stored as TEXT holding Decimal strings so values
round-trip exactly. Dates are ISO strings (YYYY-MM-DD).
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from .models import Destination, Expense, RouteOption, TransportLeg, Trip, TripBundle

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "trips.db"


def default_db_path() -> Path:
    """``TRIP_EXPLORER_DB`` overrides the default ``data/trips.db``."""
    return Path(os.environ.get("TRIP_EXPLORER_DB", DEFAULT_DB_PATH))


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trips (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    start_date TEXT,
    end_date TEXT,
    travellers INTEGER NOT NULL DEFAULT 1,
    budget_nzd TEXT NOT NULL DEFAULT '0',
    notes TEXT NOT NULL DEFAULT '',
    is_demo INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS currency_rates (
    trip_id INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    nzd_per_unit TEXT NOT NULL,
    PRIMARY KEY (trip_id, code)
);
CREATE TABLE IF NOT EXISTS destinations (
    id INTEGER PRIMARY KEY,
    trip_id INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    name TEXT NOT NULL,
    country TEXT NOT NULL DEFAULT '',
    arrival TEXT,
    departure TEXT,
    latitude REAL,
    longitude REAL,
    notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS route_options (
    id INTEGER PRIMARY KEY,
    trip_id INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT '',
    destination TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    headline_leg_id INTEGER,
    applied_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS legs (
    id INTEGER PRIMARY KEY,
    trip_id INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    option_id INTEGER REFERENCES route_options(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    origin TEXT NOT NULL DEFAULT '',
    destination TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'other',
    depart_local TEXT NOT NULL DEFAULT '',
    arrive_local TEXT NOT NULL DEFAULT '',
    duration_hours TEXT NOT NULL DEFAULT '0',
    base_fare TEXT NOT NULL DEFAULT '0',
    extra_costs TEXT NOT NULL DEFAULT '0',
    currency TEXT NOT NULL DEFAULT 'NZD',
    basis TEXT NOT NULL DEFAULT 'per_person',
    status TEXT NOT NULL DEFAULT 'estimated',
    notes TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY,
    trip_id INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    option_id INTEGER REFERENCES route_options(id) ON DELETE CASCADE,
    destination_id INTEGER REFERENCES destinations(id) ON DELETE SET NULL,
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'misc',
    unit_price TEXT NOT NULL DEFAULT '0',
    quantity TEXT NOT NULL DEFAULT '1',
    currency TEXT NOT NULL DEFAULT 'NZD',
    basis TEXT NOT NULL DEFAULT 'group',
    status TEXT NOT NULL DEFAULT 'estimated',
    nights_from_stop INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT ''
);
"""

LEG_FIELDS = (
    "trip_id", "option_id", "position", "origin", "destination", "mode", "depart_local", "arrive_local",
    "duration_hours", "base_fare", "extra_costs", "currency", "basis", "status", "notes", "source_url",
)
EXPENSE_FIELDS = (
    "trip_id", "option_id", "destination_id", "description", "category", "unit_price", "quantity",
    "currency", "basis", "status", "nights_from_stop", "notes",
)
DEST_FIELDS = ("trip_id", "position", "name", "country", "arrival", "departure", "latitude", "longitude", "notes")


def _date_out(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _date_in(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _db_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return int(value)
    return value


class Repository:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    # ------------------------------------------------------------------ plumbing

    @contextmanager
    def _connect(self):
        """One connection per operation; commits on success, rolls back on error."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _insert(conn, table: str, fields: tuple[str, ...], obj) -> int:
        values = [_db_value(getattr(obj, f)) for f in fields]
        placeholders = ", ".join("?" for _ in fields)
        cur = conn.execute(f"INSERT INTO {table} ({', '.join(fields)}) VALUES ({placeholders})", values)
        return cur.lastrowid

    @staticmethod
    def _update(conn, table: str, fields: tuple[str, ...], obj) -> None:
        if obj.id is None:
            raise ValueError("Cannot update a record without an id")
        assignments = ", ".join(f"{f} = ?" for f in fields)
        values = [_db_value(getattr(obj, f)) for f in fields] + [obj.id]
        conn.execute(f"UPDATE {table} SET {assignments} WHERE id = ?", values)

    # ------------------------------------------------------------------ meta

    def get_meta(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    # ------------------------------------------------------------------ trips

    @staticmethod
    def _row_to_trip(row, rates: dict[str, Decimal]) -> Trip:
        return Trip(
            id=row["id"],
            name=row["name"],
            start_date=_date_in(row["start_date"]),
            end_date=_date_in(row["end_date"]),
            travellers=row["travellers"],
            budget_nzd=Decimal(row["budget_nzd"]),
            notes=row["notes"],
            is_demo=bool(row["is_demo"]),
            rates=rates,
        )

    def _rates(self, conn, trip_id: int) -> dict[str, Decimal]:
        rows = conn.execute("SELECT code, nzd_per_unit FROM currency_rates WHERE trip_id = ? ORDER BY code", (trip_id,))
        return {r["code"]: Decimal(r["nzd_per_unit"]) for r in rows}

    def list_trips(self) -> list[Trip]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM trips ORDER BY id").fetchall()
            return [self._row_to_trip(r, self._rates(conn, r["id"])) for r in rows]

    def get_trip(self, trip_id: int) -> Trip | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
            return self._row_to_trip(row, self._rates(conn, trip_id)) if row else None

    def _write_rates(self, conn, trip_id: int, rates: dict[str, Decimal]) -> None:
        conn.execute("DELETE FROM currency_rates WHERE trip_id = ?", (trip_id,))
        conn.executemany(
            "INSERT INTO currency_rates (trip_id, code, nzd_per_unit) VALUES (?, ?, ?)",
            [(trip_id, code, str(rate)) for code, rate in rates.items()],
        )

    def _insert_trip(self, conn, trip: Trip) -> int:
        cur = conn.execute(
            "INSERT INTO trips (name, start_date, end_date, travellers, budget_nzd, notes, is_demo)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (trip.name, _date_out(trip.start_date), _date_out(trip.end_date), trip.travellers,
             str(trip.budget_nzd), trip.notes, int(trip.is_demo)),
        )
        self._write_rates(conn, cur.lastrowid, trip.rates)
        return cur.lastrowid

    def create_trip(self, trip: Trip) -> int:
        with self._connect() as conn:
            return self._insert_trip(conn, trip)

    def update_trip(self, trip: Trip) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE trips SET name = ?, start_date = ?, end_date = ?, travellers = ?, budget_nzd = ?, notes = ?"
                " WHERE id = ?",
                (trip.name, _date_out(trip.start_date), _date_out(trip.end_date), trip.travellers,
                 str(trip.budget_nzd), trip.notes, trip.id),
            )
            self._write_rates(conn, trip.id, trip.rates)

    def delete_trip(self, trip_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM trips WHERE id = ?", (trip_id,))

    # ------------------------------------------------------------------ destinations

    @staticmethod
    def _row_to_destination(row) -> Destination:
        return Destination(
            id=row["id"], trip_id=row["trip_id"], position=row["position"], name=row["name"],
            country=row["country"], arrival=_date_in(row["arrival"]), departure=_date_in(row["departure"]),
            latitude=row["latitude"], longitude=row["longitude"], notes=row["notes"],
        )

    def list_destinations(self, trip_id: int) -> list[Destination]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM destinations WHERE trip_id = ? ORDER BY position, id", (trip_id,)
            ).fetchall()
        return [self._row_to_destination(r) for r in rows]

    def add_destination(self, dest: Destination) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 AS next FROM destinations WHERE trip_id = ?", (dest.trip_id,)
            ).fetchone()
            dest.position = row["next"]
            return self._insert(conn, "destinations", DEST_FIELDS, dest)

    def update_destination(self, dest: Destination) -> None:
        with self._connect() as conn:
            self._update(conn, "destinations", DEST_FIELDS, dest)

    def delete_destination(self, dest_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM destinations WHERE id = ?", (dest_id,))

    def move_destination(self, dest_id: int, offset: int) -> None:
        """Move a stop up (offset −1) or down (+1) in the itinerary order."""
        with self._connect() as conn:
            row = conn.execute("SELECT trip_id FROM destinations WHERE id = ?", (dest_id,)).fetchone()
            if row is None:
                return
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM destinations WHERE trip_id = ? ORDER BY position, id", (row["trip_id"],))]
            self._reorder(conn, "destinations", ids, dest_id, offset)

    @staticmethod
    def _reorder(conn, table: str, ids: list[int], record_id: int, offset: int) -> None:
        index = ids.index(record_id)
        target = index + offset
        if 0 <= target < len(ids):
            ids[index], ids[target] = ids[target], ids[index]
        conn.executemany(f"UPDATE {table} SET position = ? WHERE id = ?", [(pos, rid) for pos, rid in enumerate(ids)])

    # ------------------------------------------------------------------ legs

    @staticmethod
    def _row_to_leg(row) -> TransportLeg:
        return TransportLeg(
            id=row["id"], trip_id=row["trip_id"], option_id=row["option_id"], position=row["position"],
            origin=row["origin"], destination=row["destination"], mode=row["mode"],
            depart_local=row["depart_local"], arrive_local=row["arrive_local"],
            duration_hours=Decimal(row["duration_hours"]), base_fare=Decimal(row["base_fare"]),
            extra_costs=Decimal(row["extra_costs"]), currency=row["currency"], basis=row["basis"],
            status=row["status"], notes=row["notes"], source_url=row["source_url"],
        )

    def list_legs(self, trip_id: int, option_id: int | None = None) -> list[TransportLeg]:
        """Active itinerary legs when ``option_id`` is None, otherwise that option's legs."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM legs WHERE trip_id = ? AND option_id IS ? ORDER BY position, id", (trip_id, option_id)
            ).fetchall()
        return [self._row_to_leg(r) for r in rows]

    def add_leg(self, leg: TransportLeg) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 AS next FROM legs WHERE trip_id = ? AND option_id IS ?",
                (leg.trip_id, leg.option_id),
            ).fetchone()
            leg.position = row["next"]
            return self._insert(conn, "legs", LEG_FIELDS, leg)

    def update_leg(self, leg: TransportLeg) -> None:
        with self._connect() as conn:
            self._update(conn, "legs", LEG_FIELDS, leg)

    def delete_leg(self, leg_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM legs WHERE id = ?", (leg_id,))
            conn.execute("UPDATE route_options SET headline_leg_id = NULL WHERE headline_leg_id = ?", (leg_id,))

    def move_leg(self, leg_id: int, offset: int) -> None:
        with self._connect() as conn:
            row = conn.execute("SELECT trip_id, option_id FROM legs WHERE id = ?", (leg_id,)).fetchone()
            if row is None:
                return
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM legs WHERE trip_id = ? AND option_id IS ? ORDER BY position, id",
                (row["trip_id"], row["option_id"]))]
            self._reorder(conn, "legs", ids, leg_id, offset)

    # ------------------------------------------------------------------ expenses

    @staticmethod
    def _row_to_expense(row) -> Expense:
        return Expense(
            id=row["id"], trip_id=row["trip_id"], option_id=row["option_id"], destination_id=row["destination_id"],
            description=row["description"], category=row["category"], unit_price=Decimal(row["unit_price"]),
            quantity=Decimal(row["quantity"]), currency=row["currency"], basis=row["basis"], status=row["status"],
            nights_from_stop=bool(row["nights_from_stop"]), notes=row["notes"],
        )

    def list_expenses(self, trip_id: int, option_id: int | None = None) -> list[Expense]:
        """Active itinerary expenses when ``option_id`` is None, otherwise that option's expenses."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM expenses WHERE trip_id = ? AND option_id IS ? ORDER BY category, id",
                (trip_id, option_id),
            ).fetchall()
        return [self._row_to_expense(r) for r in rows]

    def add_expense(self, expense: Expense) -> int:
        with self._connect() as conn:
            return self._insert(conn, "expenses", EXPENSE_FIELDS, expense)

    def update_expense(self, expense: Expense) -> None:
        with self._connect() as conn:
            self._update(conn, "expenses", EXPENSE_FIELDS, expense)

    def delete_expense(self, expense_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))

    # ------------------------------------------------------------------ route options

    @staticmethod
    def _row_to_option(row) -> RouteOption:
        return RouteOption(
            id=row["id"], trip_id=row["trip_id"], name=row["name"], origin=row["origin"],
            destination=row["destination"], notes=row["notes"], headline_leg_id=row["headline_leg_id"],
            applied_at=row["applied_at"],
        )

    def list_options(self, trip_id: int) -> list[RouteOption]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM route_options WHERE trip_id = ? ORDER BY id", (trip_id,)).fetchall()
        return [self._row_to_option(r) for r in rows]

    def add_option(self, option: RouteOption) -> int:
        with self._connect() as conn:
            return self._insert_option(conn, option)

    def _insert_option(self, conn, option: RouteOption) -> int:
        cur = conn.execute(
            "INSERT INTO route_options (trip_id, name, origin, destination, notes, headline_leg_id, applied_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (option.trip_id, option.name, option.origin, option.destination, option.notes,
             option.headline_leg_id, option.applied_at),
        )
        return cur.lastrowid

    def update_option(self, option: RouteOption) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE route_options SET name = ?, origin = ?, destination = ?, notes = ?, headline_leg_id = ?"
                " WHERE id = ?",
                (option.name, option.origin, option.destination, option.notes, option.headline_leg_id, option.id),
            )

    def delete_option(self, option_id: int) -> None:
        """Deletes the option and its legs/expenses (never touches the active itinerary)."""
        with self._connect() as conn:
            conn.execute("DELETE FROM route_options WHERE id = ?", (option_id,))

    def apply_option(self, option_id: int, replace_leg_ids: list[int], replace_expense_ids: list[int]) -> int:
        """Make an option part of the active itinerary, in one transaction.

        * The option's legs/expenses are *copied* into the active itinerary
          (the option itself is kept so you can still compare against it).
        * The active legs/expenses it replaces are *moved* into a new option
          called "Previous plan …", so they stop counting towards totals but
          are not lost.
        * Copied legs take the place of the first replaced leg in the order.

        Returns the id of the "Previous plan" option (or 0 if nothing was replaced).
        """
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM route_options WHERE id = ?", (option_id,)).fetchone()
            if row is None:
                raise ValueError("Route option not found")
            option = self._row_to_option(row)
            trip_id = option.trip_id

            active_leg_ids = [r["id"] for r in conn.execute(
                "SELECT id FROM legs WHERE trip_id = ? AND option_id IS NULL ORDER BY position, id", (trip_id,))]
            active_exp_ids = {r["id"] for r in conn.execute(
                "SELECT id FROM expenses WHERE trip_id = ? AND option_id IS NULL", (trip_id,))}
            bad = [i for i in replace_leg_ids if i not in active_leg_ids]
            bad += [i for i in replace_expense_ids if i not in active_exp_ids]
            if bad:
                raise ValueError("Only active itinerary items can be replaced")

            previous_id = 0
            if replace_leg_ids or replace_expense_ids:
                previous_id = self._insert_option(conn, RouteOption(
                    trip_id=trip_id, name=f"Previous plan (replaced by {option.name})",
                    origin=option.origin, destination=option.destination,
                    notes=f"Moved out of the active itinerary on {datetime.now():%Y-%m-%d %H:%M}.",
                ))
                for leg_id in replace_leg_ids:
                    conn.execute("UPDATE legs SET option_id = ? WHERE id = ?", (previous_id, leg_id))
                for exp_id in replace_expense_ids:
                    conn.execute("UPDATE expenses SET option_id = ? WHERE id = ?", (previous_id, exp_id))

            insert_at = min((active_leg_ids.index(i) for i in replace_leg_ids), default=len(active_leg_ids))
            remaining = [i for i in active_leg_ids if i not in replace_leg_ids]
            insert_at = min(insert_at, len(remaining))

            new_leg_ids = []
            for leg_row in conn.execute(
                "SELECT * FROM legs WHERE option_id = ? ORDER BY position, id", (option_id,)
            ).fetchall():
                leg = self._row_to_leg(leg_row)
                leg.option_id = None
                new_leg_ids.append(self._insert(conn, "legs", LEG_FIELDS, leg))
            for exp_row in conn.execute("SELECT * FROM expenses WHERE option_id = ?", (option_id,)).fetchall():
                exp = self._row_to_expense(exp_row)
                exp.option_id = None
                self._insert(conn, "expenses", EXPENSE_FIELDS, exp)

            ordered = remaining[:insert_at] + new_leg_ids + remaining[insert_at:]
            conn.executemany("UPDATE legs SET position = ? WHERE id = ?", list(enumerate(ordered)))
            conn.execute(
                "UPDATE route_options SET applied_at = ? WHERE id = ?", (f"{datetime.now():%Y-%m-%d %H:%M}", option_id)
            )
            return previous_id

    # ------------------------------------------------------------------ bundles (import/export/seed)

    def load_bundle(self, trip_id: int) -> TripBundle:
        trip = self.get_trip(trip_id)
        if trip is None:
            raise ValueError("Trip not found")
        with self._connect() as conn:
            legs = [self._row_to_leg(r) for r in conn.execute(
                "SELECT * FROM legs WHERE trip_id = ? ORDER BY option_id IS NOT NULL, option_id, position, id",
                (trip_id,))]
            expenses = [self._row_to_expense(r) for r in conn.execute(
                "SELECT * FROM expenses WHERE trip_id = ? ORDER BY id", (trip_id,))]
        return TripBundle(
            trip=trip,
            destinations=self.list_destinations(trip_id),
            legs=legs,
            expenses=expenses,
            options=self.list_options(trip_id),
        )

    @staticmethod
    def _mapped(mapping: dict[int, int], local_id: int | None, kind: str) -> int | None:
        if local_id is None:
            return None
        if local_id not in mapping:
            raise ValueError(f"Reference to unknown {kind} id {local_id}")
        return mapping[local_id]

    def save_bundle(self, bundle: TripBundle) -> int:
        """Insert a whole trip as a new trip in one transaction; local ids are remapped."""
        with self._connect() as conn:
            trip_id = self._insert_trip(conn, bundle.trip)
            dest_map: dict[int, int] = {}
            for pos, dest in enumerate(bundle.destinations):
                new = Destination(**{**dest.__dict__, "id": None, "trip_id": trip_id, "position": pos})
                new_id = self._insert(conn, "destinations", DEST_FIELDS, new)
                if dest.id is not None:
                    dest_map[dest.id] = new_id
            option_map: dict[int, int] = {}
            for opt in bundle.options:
                new_opt = RouteOption(**{**opt.__dict__, "id": None, "trip_id": trip_id, "headline_leg_id": None})
                new_id = self._insert_option(conn, new_opt)
                if opt.id is not None:
                    option_map[opt.id] = new_id
            leg_map: dict[int, int] = {}
            positions: dict[int | None, int] = {}
            for leg in bundle.legs:
                option_id = self._mapped(option_map, leg.option_id, "option")
                pos = positions.get(option_id, 0)
                positions[option_id] = pos + 1
                new = TransportLeg(**{**leg.__dict__, "id": None, "trip_id": trip_id,
                                      "option_id": option_id, "position": pos})
                new_id = self._insert(conn, "legs", LEG_FIELDS, new)
                if leg.id is not None:
                    leg_map[leg.id] = new_id
            for exp in bundle.expenses:
                new = Expense(**{
                    **exp.__dict__, "id": None, "trip_id": trip_id,
                    "option_id": self._mapped(option_map, exp.option_id, "option"),
                    "destination_id": self._mapped(dest_map, exp.destination_id, "destination"),
                })
                self._insert(conn, "expenses", EXPENSE_FIELDS, new)
            for opt in bundle.options:
                if opt.id is not None and opt.headline_leg_id in leg_map:
                    conn.execute("UPDATE route_options SET headline_leg_id = ? WHERE id = ?",
                                 (leg_map[opt.headline_leg_id], option_map[opt.id]))
            return trip_id
