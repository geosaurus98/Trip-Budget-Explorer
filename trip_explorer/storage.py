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

from .models import CostSnapshot, Destination, Expense, RouteOption, TodoItem, TransportLeg, Trip, TripBundle

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
    is_demo INTEGER NOT NULL DEFAULT 0,
    time_value_nzd_per_hour TEXT NOT NULL DEFAULT '0',
    disruption_penalty_hours TEXT NOT NULL DEFAULT '0',
    traveller_names TEXT NOT NULL DEFAULT ''
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
    source_url TEXT NOT NULL DEFAULT '',
    disruptive INTEGER NOT NULL DEFAULT 0,
    book_by TEXT,
    cancel_by TEXT,
    paid_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS cost_history (
    id INTEGER PRIMARY KEY,
    trip_id INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    record_type TEXT NOT NULL,
    record_id INTEGER NOT NULL,
    recorded_at TEXT NOT NULL,
    status TEXT NOT NULL,
    amount TEXT NOT NULL,
    quantity TEXT NOT NULL,
    currency TEXT NOT NULL,
    basis TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS cost_history_record ON cost_history (record_type, record_id);
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
    notes TEXT NOT NULL DEFAULT '',
    book_by TEXT,
    cancel_by TEXT,
    paid_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS todos (
    id INTEGER PRIMARY KEY,
    trip_id INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    due TEXT,
    done INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT ''
);
"""

LEG_FIELDS = (
    "trip_id", "option_id", "position", "origin", "destination", "mode", "depart_local", "arrive_local",
    "duration_hours", "base_fare", "extra_costs", "currency", "basis", "status", "notes", "source_url",
    "disruptive", "book_by", "cancel_by", "paid_by",
)
EXPENSE_FIELDS = (
    "trip_id", "option_id", "destination_id", "description", "category", "unit_price", "quantity",
    "currency", "basis", "status", "nights_from_stop", "notes", "book_by", "cancel_by",
    "paid_by",
)
DEST_FIELDS = ("trip_id", "position", "name", "country", "arrival", "departure", "latitude", "longitude", "notes")


def _date_out(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _date_in(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _names_out(names: list[str]) -> str:
    return "\n".join(n.strip() for n in names if n.strip())


def _names_in(value: str) -> list[str]:
    return [n for n in (value or "").split("\n") if n]


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
            self._migrate(conn)

    # Columns added after the first release: (table, column, SQL definition).
    # New databases get them from SCHEMA; older ones are upgraded in place.
    MIGRATIONS = (
        ("trips", "time_value_nzd_per_hour", "TEXT NOT NULL DEFAULT '0'"),
        ("trips", "disruption_penalty_hours", "TEXT NOT NULL DEFAULT '0'"),
        ("legs", "disruptive", "INTEGER NOT NULL DEFAULT 0"),
        ("legs", "book_by", "TEXT"),
        ("legs", "cancel_by", "TEXT"),
        ("expenses", "book_by", "TEXT"),
        ("expenses", "cancel_by", "TEXT"),
        ("trips", "traveller_names", "TEXT NOT NULL DEFAULT ''"),
        ("legs", "paid_by", "TEXT NOT NULL DEFAULT ''"),
        ("expenses", "paid_by", "TEXT NOT NULL DEFAULT ''"),
    )

    @classmethod
    def _migrate(cls, conn) -> None:
        for table, column, definition in cls.MIGRATIONS:
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

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

    def _insert(self, conn, table: str, fields: tuple[str, ...], obj) -> int:
        values = [_db_value(getattr(obj, f)) for f in fields]
        placeholders = ", ".join("?" for _ in fields)
        cur = conn.execute(f"INSERT INTO {table} ({', '.join(fields)}) VALUES ({placeholders})", values)
        self._snapshot(conn, table, cur.lastrowid)
        return cur.lastrowid

    def _update(self, conn, table: str, fields: tuple[str, ...], obj) -> None:
        if obj.id is None:
            raise ValueError("Cannot update a record without an id")
        assignments = ", ".join(f"{f} = ?" for f in fields)
        values = [_db_value(getattr(obj, f)) for f in fields] + [obj.id]
        conn.execute(f"UPDATE {table} SET {assignments} WHERE id = ?", values)
        self._snapshot(conn, table, obj.id)

    # ------------------------------------------------------------------ cost history

    _HISTORY_TYPES = {"legs": "leg", "expenses": "expense"}

    def _snapshot(self, conn, table: str, record_id: int) -> None:
        """Record a leg's/expense's cost fields if they differ from its latest snapshot.

        Called on every insert/update, so all write paths (forms, apply, import,
        demo) build history; edits to notes, names or dates add nothing.
        """
        record_type = self._HISTORY_TYPES.get(table)
        if record_type is None:
            return
        row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone()
        if record_type == "leg":
            amount, quantity = str(Decimal(row["base_fare"]) + Decimal(row["extra_costs"])), "1"
        else:
            amount, quantity = row["unit_price"], row["quantity"]
            if row["nights_from_stop"] and row["destination_id"] is not None:
                stop = conn.execute("SELECT arrival, departure FROM destinations WHERE id = ?",
                                    (row["destination_id"],)).fetchone()
                if stop and stop["arrival"] and stop["departure"]:
                    nights = (_date_in(stop["departure"]) - _date_in(stop["arrival"])).days
                    if nights >= 0:
                        quantity = str(nights)  # the live quantity the cost calculation uses
        current = (row["status"], amount, quantity, row["currency"], row["basis"])
        latest = conn.execute(
            "SELECT status, amount, quantity, currency, basis FROM cost_history"
            " WHERE record_type = ? AND record_id = ? ORDER BY id DESC LIMIT 1",
            (record_type, record_id),
        ).fetchone()
        if latest is not None and tuple(latest) == current:
            return
        conn.execute(
            "INSERT INTO cost_history (trip_id, record_type, record_id, recorded_at, status, amount, quantity,"
            " currency, basis) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row["trip_id"], record_type, record_id, datetime.now().isoformat(timespec="seconds"), *current),
        )

    @staticmethod
    def _row_to_snapshot(row) -> CostSnapshot:
        return CostSnapshot(
            record_type=row["record_type"], record_id=row["record_id"], recorded_at=row["recorded_at"],
            status=row["status"], amount=Decimal(row["amount"]), quantity=Decimal(row["quantity"]),
            currency=row["currency"], basis=row["basis"],
        )

    def cost_history(self, record_type: str, record_id: int) -> list[CostSnapshot]:
        """Every recorded cost state of one leg or expense, oldest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM cost_history WHERE record_type = ? AND record_id = ? ORDER BY id",
                (record_type, record_id),
            ).fetchall()
        return [self._row_to_snapshot(r) for r in rows]

    def first_snapshots(self, trip_id: int) -> dict[tuple[str, int], CostSnapshot]:
        """The earliest recorded cost state of each leg/expense in a trip."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT h.* FROM cost_history h JOIN ("
                "  SELECT MIN(id) AS id FROM cost_history WHERE trip_id = ? GROUP BY record_type, record_id"
                ") first ON first.id = h.id",
                (trip_id,),
            ).fetchall()
        return {(r["record_type"], r["record_id"]): self._row_to_snapshot(r) for r in rows}

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
            time_value_nzd_per_hour=Decimal(row["time_value_nzd_per_hour"]),
            disruption_penalty_hours=Decimal(row["disruption_penalty_hours"]),
            traveller_names=_names_in(row["traveller_names"]),
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
            "INSERT INTO trips (name, start_date, end_date, travellers, budget_nzd, notes, is_demo,"
            " time_value_nzd_per_hour, disruption_penalty_hours, traveller_names)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (trip.name, _date_out(trip.start_date), _date_out(trip.end_date), trip.travellers,
             str(trip.budget_nzd), trip.notes, int(trip.is_demo), str(trip.time_value_nzd_per_hour),
             str(trip.disruption_penalty_hours), _names_out(trip.traveller_names)),
        )
        self._write_rates(conn, cur.lastrowid, trip.rates)
        return cur.lastrowid

    def create_trip(self, trip: Trip) -> int:
        with self._connect() as conn:
            return self._insert_trip(conn, trip)

    def update_trip(self, trip: Trip) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE trips SET name = ?, start_date = ?, end_date = ?, travellers = ?, budget_nzd = ?, notes = ?,"
                " time_value_nzd_per_hour = ?, disruption_penalty_hours = ?, traveller_names = ? WHERE id = ?",
                (trip.name, _date_out(trip.start_date), _date_out(trip.end_date), trip.travellers,
                 str(trip.budget_nzd), trip.notes, str(trip.time_value_nzd_per_hour),
                 str(trip.disruption_penalty_hours), _names_out(trip.traveller_names), trip.id),
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
            # Accommodation priced by this stop's nights changes cost when dates change.
            for row in conn.execute(
                "SELECT id FROM expenses WHERE destination_id = ? AND nights_from_stop = 1", (dest.id,)
            ).fetchall():
                self._snapshot(conn, "expenses", row["id"])

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
            disruptive=bool(row["disruptive"]), book_by=_date_in(row["book_by"]),
            cancel_by=_date_in(row["cancel_by"]), paid_by=row["paid_by"],
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
            conn.execute("DELETE FROM cost_history WHERE record_type = 'leg' AND record_id = ?", (leg_id,))
            conn.execute("UPDATE route_options SET headline_leg_id = NULL WHERE headline_leg_id = ?", (leg_id,))

    def set_leg_order(self, trip_id: int, ordered_ids: list[int], option_id: int | None = None) -> None:
        """Rewrite positions for the given legs (active legs when option_id is None)."""
        with self._connect() as conn:
            current = {r["id"] for r in conn.execute(
                "SELECT id FROM legs WHERE trip_id = ? AND option_id IS ?", (trip_id, option_id))}
            if set(ordered_ids) != current:
                raise ValueError("Leg order must list every leg exactly once")
            conn.executemany("UPDATE legs SET position = ? WHERE id = ?", list(enumerate(ordered_ids)))

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
            book_by=_date_in(row["book_by"]), cancel_by=_date_in(row["cancel_by"]), paid_by=row["paid_by"],
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
            conn.execute("DELETE FROM cost_history WHERE record_type = 'expense' AND record_id = ?", (expense_id,))

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
            self._prune_history(conn)

    @staticmethod
    def _prune_history(conn) -> None:
        """Remove history of legs/expenses deleted by a cascade."""
        conn.execute("DELETE FROM cost_history WHERE record_type = 'leg'"
                     " AND record_id NOT IN (SELECT id FROM legs)")
        conn.execute("DELETE FROM cost_history WHERE record_type = 'expense'"
                     " AND record_id NOT IN (SELECT id FROM expenses)")

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

    # ------------------------------------------------------------------ checklist

    @staticmethod
    def _row_to_todo(row) -> TodoItem:
        return TodoItem(id=row["id"], trip_id=row["trip_id"], title=row["title"], due=_date_in(row["due"]),
                        done=bool(row["done"]), notes=row["notes"])

    def list_todos(self, trip_id: int) -> list[TodoItem]:
        """Open items first, then by due date (undated last)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM todos WHERE trip_id = ? ORDER BY done, due IS NULL, due, id", (trip_id,)
            ).fetchall()
        return [self._row_to_todo(r) for r in rows]

    def _insert_todo(self, conn, todo: TodoItem) -> int:
        cur = conn.execute("INSERT INTO todos (trip_id, title, due, done, notes) VALUES (?, ?, ?, ?, ?)",
                           (todo.trip_id, todo.title, _date_out(todo.due), int(todo.done), todo.notes))
        return cur.lastrowid

    def save_todos(self, trip_id: int, todos: list[TodoItem]) -> None:
        """Make the trip's checklist exactly ``todos``, in one transaction.

        Items with an id are updated, items without one are added, and existing
        items missing from the list are deleted.
        """
        with self._connect() as conn:
            existing = {r["id"] for r in conn.execute("SELECT id FROM todos WHERE trip_id = ?", (trip_id,))}
            keep = {t.id for t in todos if t.id is not None}
            if keep - existing:
                raise ValueError("Checklist contains items from another trip")
            for todo_id in existing - keep:
                conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
            for todo in todos:
                if todo.id is None:
                    self._insert_todo(conn, TodoItem(**{**todo.__dict__, "trip_id": trip_id}))
                else:
                    conn.execute("UPDATE todos SET title = ?, due = ?, done = ?, notes = ? WHERE id = ?",
                                 (todo.title, _date_out(todo.due), int(todo.done), todo.notes, todo.id))

    def add_missing_todos(self, trip_id: int, titles: list[str]) -> int:
        """Add checklist items whose titles aren't already present (ignoring case). Returns how many."""
        present = {t.title.strip().lower() for t in self.list_todos(trip_id)}
        new = [t for t in titles if t.strip().lower() not in present]
        with self._connect() as conn:
            for title in new:
                self._insert_todo(conn, TodoItem(trip_id=trip_id, title=title))
        return len(new)

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
            todos=self.list_todos(trip_id),
        )

    def duplicate_trip(self, trip_id: int, new_name: str) -> int:
        """Copy a whole trip (stops, legs, expenses, options) as a new, non-demo trip.

        The copy starts with fresh cost history.
        """
        bundle = self.load_bundle(trip_id)
        bundle.trip.name, bundle.trip.is_demo = new_name, False
        return self.save_bundle(bundle)

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
            for todo in bundle.todos:
                self._insert_todo(conn, TodoItem(**{**todo.__dict__, "id": None, "trip_id": trip_id}))
            for opt in bundle.options:
                if opt.id is not None and opt.headline_leg_id in leg_map:
                    conn.execute("UPDATE route_options SET headline_leg_id = ? WHERE id = ?",
                                 (leg_map[opt.headline_leg_id], option_map[opt.id]))
            return trip_id
