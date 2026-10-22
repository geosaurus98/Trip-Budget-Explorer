import sqlite3
from decimal import Decimal as D

import pytest

from trip_explorer.calculations import legs_in_stop_order, missing_connections, unmatched_leg_places
from trip_explorer.comparison import compare_options, generalised_cost_nzd, summarize_option
from trip_explorer.models import Destination, RouteOption, TransportLeg, Trip
from trip_explorer.storage import Repository
from trip_explorer.transfer import parse_bundle


def stops(*names):
    return [Destination(trip_id=1, id=i, name=n) for i, n in enumerate(names, start=1)]


def leg(origin, dest, lid=None, hours="1", fare="0", disruptive=False):
    return TransportLeg(trip_id=1, id=lid, origin=origin, destination=dest, duration_hours=D(hours),
                        base_fare=D(fare), currency="NZD", disruptive=disruptive)


# --------------------------------------------------------------------------- stop connections


def test_missing_connections_finds_unjoined_stops():
    dests = stops("Bali", "Bromo", "Malang")
    missing = missing_connections(dests, [leg("Bali", "Bromo")])
    assert [(a.name, b.name) for a, b in missing] == [("Bromo", "Malang")]


def test_chain_through_non_stop_places_counts_and_names_ignore_case():
    dests = stops("Lake Toba", "Singapore")
    legs = [leg("lake toba ", "Kualanamu airport"), leg("Kualanamu airport", "SINGAPORE")]
    assert missing_connections(dests, legs) == []
    assert unmatched_leg_places(dests, legs) == ["Kualanamu airport"]


def test_chain_through_another_stop_does_not_count():
    dests = stops("A", "B", "C")
    # A→C directly skips B, so A→B and B→C are both unjoined.
    assert len(missing_connections(dests, [leg("A", "C")])) == 2


def test_legs_in_stop_order_keeps_chains_together():
    dests = stops("A", "B", "C")
    legs = [leg("B", "C", 1), leg("A", "Airport", 2), leg("Airport", "B", 3)]
    assert [l.id for l in legs_in_stop_order(dests, legs)] == [2, 3, 1]


def test_set_leg_order_requires_every_leg(tmp_path):
    repo = Repository(tmp_path / "t.db")
    tid = repo.create_trip(Trip(name="T"))
    ids = [repo.add_leg(TransportLeg(trip_id=tid, origin=o, destination=d)) for o, d in (("B", "C"), ("A", "B"))]
    with pytest.raises(ValueError):
        repo.set_leg_order(tid, ids[:1])
    repo.set_leg_order(tid, list(reversed(ids)))
    assert [l.origin for l in repo.list_legs(tid)] == ["A", "B"]


# --------------------------------------------------------------------------- migration / import compatibility


def test_old_database_is_migrated_in_place(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE trips (id INTEGER PRIMARY KEY, name TEXT NOT NULL, start_date TEXT, end_date TEXT,
            travellers INTEGER NOT NULL DEFAULT 1, budget_nzd TEXT NOT NULL DEFAULT '0',
            notes TEXT NOT NULL DEFAULT '', is_demo INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE legs (id INTEGER PRIMARY KEY, trip_id INTEGER NOT NULL, option_id INTEGER,
            position INTEGER NOT NULL DEFAULT 0, origin TEXT NOT NULL DEFAULT '', destination TEXT NOT NULL DEFAULT '',
            mode TEXT NOT NULL DEFAULT 'other', depart_local TEXT NOT NULL DEFAULT '',
            arrive_local TEXT NOT NULL DEFAULT '', duration_hours TEXT NOT NULL DEFAULT '0',
            base_fare TEXT NOT NULL DEFAULT '0', extra_costs TEXT NOT NULL DEFAULT '0',
            currency TEXT NOT NULL DEFAULT 'NZD', basis TEXT NOT NULL DEFAULT 'per_person',
            status TEXT NOT NULL DEFAULT 'estimated', notes TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '');
        INSERT INTO trips (name) VALUES ('Old trip');
        INSERT INTO legs (trip_id, origin, destination) VALUES (1, 'A', 'B');
    """)
    conn.commit()
    conn.close()

    repo = Repository(path)
    trip = repo.list_trips()[0]
    assert trip.name == "Old trip" and trip.time_value_nzd_per_hour == 0 and trip.disruption_penalty_hours == 0
    assert repo.list_legs(trip.id)[0].disruptive is False
    trip.time_value_nzd_per_hour = D("20")
    repo.update_trip(trip)
    assert Repository(path).get_trip(trip.id).time_value_nzd_per_hour == D("20")


def test_import_of_file_without_new_fields_uses_defaults():
    bundle = parse_bundle({
        "format": "trip-budget-explorer", "version": 1,
        "trip": {"name": "Old export", "travellers": 2},
        "legs": [{"origin": "A", "destination": "B", "currency": "NZD"}],
    })
    assert bundle.trip.time_value_nzd_per_hour == 0
    assert bundle.legs[0].disruptive is False


# --------------------------------------------------------------------------- time value


def option_summary(oid, legs, penalty="0"):
    trip = Trip(name="T", travellers=2, disruption_penalty_hours=D(penalty))
    opt = RouteOption(trip_id=1, id=oid, name=f"o{oid}", origin="Y", destination="M")
    return summarize_option(trip, opt, legs, [], [])


def slow_cheap_vs_fast_dear(penalty="0"):
    # A: NZD 400 group, 6 h.  B: NZD 340 group, 10 h + one disruptive leg.
    a = option_summary(1, [leg("Y", "M", hours="6", fare="200")], penalty)
    b = option_summary(2, [leg("Y", "J", hours="4", fare="70"), leg("J", "M", hours="6", fare="100", disruptive=True)],
                       penalty)
    return a, b


def test_effective_hours_add_disruption_penalty():
    _, b = slow_cheap_vs_fast_dear(penalty="2")
    assert b.travel_hours == D("10") and b.effective_hours == D("12") and b.disruptive_legs == 1


def test_break_even_time_value_and_verdicts():
    a, b = slow_cheap_vs_fast_dear()
    # Saving 60 over 4 extra hours × 2 travellers = NZD 7.50 per person-hour.
    unset = compare_options(a, b, 2).time_value
    assert unset.break_even_value_per_person_hour == D("7.50") and unset.better is None
    assert "Set your time value" in unset.verdict

    cheap_time = compare_options(a, b, 2, D("5")).time_value
    assert cheap_time.better == "B" and cheap_time.verdict.startswith("Worth it")
    assert cheap_time.generalised_b_nzd == D("440.00")  # 340 + 5 × 2 × 10

    dear_time = compare_options(a, b, 2, D("10")).time_value
    assert dear_time.better == "A" and dear_time.verdict.startswith("Not worth it")

    exact = compare_options(a, b, 2, D("7.50")).time_value
    assert exact.better is None and exact.verdict.startswith("Break-even")


def test_disruption_penalty_lowers_break_even():
    a, b = slow_cheap_vs_fast_dear(penalty="2")
    # Saving 60 over (12 − 6) effective hours × 2 travellers = NZD 5.00.
    assert compare_options(a, b, 2).time_value.break_even_value_per_person_hour == D("5.00")


def test_no_trade_off_when_cheaper_is_also_faster():
    a = option_summary(1, [leg("Y", "M", hours="6", fare="200")])
    b = option_summary(2, [leg("Y", "M", hours="5", fare="100")])
    tv = compare_options(a, b, 2, D("20")).time_value
    assert tv.break_even_value_per_person_hour is None and tv.better == "B"
    assert tv.verdict.startswith("No trade-off")


def test_equal_cost_has_no_break_even():
    a = option_summary(1, [leg("Y", "M", hours="6", fare="100")])
    b = option_summary(2, [leg("Y", "M", hours="9", fare="100")])
    tv = compare_options(a, b, 2, D("20")).time_value
    assert tv.break_even_value_per_person_hour is None and tv.better == "A"


def test_generalised_cost():
    a, _ = slow_cheap_vs_fast_dear()
    assert generalised_cost_nzd(a, D("12.5"), 2) == D("550.00")  # 400 + 12.5 × 2 × 6
