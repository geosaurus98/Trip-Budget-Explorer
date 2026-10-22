import sqlite3
from datetime import date
from decimal import Decimal as D

import pytest

from trip_explorer.deadlines import upcoming_deadlines
from trip_explorer.demo import load_demo_trip
from trip_explorer.geo import parse_lat_lon
from trip_explorer.models import Expense, TodoItem, TransportLeg, Trip
from trip_explorer.report import trip_summary_html
from trip_explorer.sharing import settle_up
from trip_explorer.storage import Repository
from trip_explorer.transfer import parse_bundle


@pytest.fixture
def repo(tmp_path):
    return Repository(tmp_path / "t.db")


# --------------------------------------------------------------------------- coordinates


@pytest.mark.parametrize("text, expected", [
    ("-8.5069, 115.2625", (-8.5069, 115.2625)),
    ("-8.5069 115.2625", (-8.5069, 115.2625)),
    ("8.5069° S, 115.2625° E", (-8.5069, 115.2625)),
    ("115.2625 E, 8.5069 S", (-8.5069, 115.2625)),
    ("40.7128° N, 74.0060° W", (40.7128, -74.006)),
    ("https://www.google.com/maps/place/Ubud/@-8.5069,115.2625,14z", (-8.5069, 115.2625)),
    ("https://www.openstreetmap.org/?mlat=-8.5069&mlon=115.2625#map=12/-8.5/115.2", (-8.5069, 115.2625)),
    ("https://maps.example/?q=1.3521,103.8198", (1.3521, 103.8198)),
])
def test_parse_lat_lon(text, expected):
    assert parse_lat_lon(text) == pytest.approx(expected)


@pytest.mark.parametrize("text, message", [
    ("", "Paste coordinates"),
    ("Ubud, Bali", "two numbers"),
    ("100, 50", "swapped"),
    ("10, 200", "Longitude"),
])
def test_parse_lat_lon_errors(text, message):
    with pytest.raises(ValueError, match=message):
        parse_lat_lon(text)


# --------------------------------------------------------------------------- sharing costs


def make_trip(names=("Alex", "Sam", "Jo")):
    return Trip(name="T", travellers=len(names), traveller_names=list(names))


def test_settle_up_balances_and_transfers():
    trip = make_trip()
    expenses = [
        Expense(trip_id=1, id=1, description="Hotel", unit_price=D("300"), currency="NZD", paid_by="Alex"),
        Expense(trip_id=1, id=2, description="Dinner", unit_price=D("60"), currency="NZD", paid_by="sam"),
        Expense(trip_id=1, id=3, description="Tour", unit_price=D("90"), currency="NZD"),  # not paid yet
    ]
    result = settle_up(trip, [], expenses, [])
    balances = {b.name: (b.paid, b.share, b.net) for b in result.balances}
    assert balances == {"Alex": (D("300"), D("120"), D("180")), "Sam": (D("60"), D("120"), D("-60")),
                        "Jo": (D("0"), D("120"), D("-120"))}
    assert sum(b.net for b in result.balances) == 0
    assert [(t.payer, t.payee, t.amount) for t in result.transfers] == [("Jo", "Alex", D("120")),
                                                                          ("Sam", "Alex", D("60"))]
    assert [l.description for l in result.not_paid] == ["Tour"]


def test_settle_up_splits_cents_exactly_and_flags_unknown_payers():
    trip = make_trip()
    legs = [TransportLeg(trip_id=1, id=1, origin="A", destination="B", base_fare=D("100"), currency="NZD",
                         basis="group", paid_by="Jo")]
    expenses = [Expense(trip_id=1, id=1, description="Mystery", unit_price=D("5"), currency="NZD",
                        paid_by="Chris")]
    result = settle_up(trip, legs, expenses, [])
    assert sorted(b.share for b in result.balances) == [D("33.33"), D("33.33"), D("33.34")]
    assert sum(t.amount for t in result.transfers) == D("66.67")
    assert [(l.description, name) for l, name in result.unknown_payer] == [("Mystery", "Chris")]


def test_settle_up_requires_names():
    with pytest.raises(ValueError):
        settle_up(Trip(name="T"), [], [], [])


def test_paid_by_and_names_persist_and_import(repo):
    tid = load_demo_trip(repo)
    trip = repo.get_trip(tid)
    assert trip.traveller_names == ["Alex", "Sam"]
    result = settle_up(trip, repo.list_legs(tid), repo.list_expenses(tid), repo.list_destinations(tid))
    assert {b.name: b.paid for b in result.balances} == {"Alex": D("411.60"), "Sam": D("462.00")}
    assert [(t.payer, t.payee, t.amount) for t in result.transfers] == [("Alex", "Sam", D("25.20"))]
    with pytest.raises(Exception):
        parse_bundle({"format": "trip-budget-explorer", "version": 1,
                      "trip": {"name": "x", "traveller_names": "Alex"}})


# --------------------------------------------------------------------------- checklist


def test_save_todos_adds_updates_and_deletes(repo):
    tid = repo.create_trip(Trip(name="T"))
    repo.save_todos(tid, [TodoItem(trip_id=tid, title="Insurance"), TodoItem(trip_id=tid, title="Visa")])
    insurance, visa = sorted(repo.list_todos(tid), key=lambda t: t.title)
    insurance.done = True
    repo.save_todos(tid, [insurance, TodoItem(trip_id=tid, title="Vaccines", due=date(2027, 4, 1))])
    todos = repo.list_todos(tid)
    assert [(t.title, t.done) for t in todos] == [("Vaccines", False), ("Insurance", True)]  # open first


def test_save_todos_rejects_other_trips_items(repo):
    a = repo.create_trip(Trip(name="A"))
    b = repo.create_trip(Trip(name="B"))
    repo.save_todos(a, [TodoItem(trip_id=a, title="x")])
    with pytest.raises(ValueError):
        repo.save_todos(b, repo.list_todos(a))
    assert len(repo.list_todos(a)) == 1


def test_add_missing_todos_ignores_existing(repo):
    tid = repo.create_trip(Trip(name="T"))
    repo.save_todos(tid, [TodoItem(trip_id=tid, title="Buy travel insurance")])
    assert repo.add_missing_todos(tid, ["buy travel INSURANCE", "Visa"]) == 1
    assert repo.add_missing_todos(tid, ["Visa"]) == 0


def test_todos_feed_deadlines_and_summary(repo):
    tid = load_demo_trip(repo)
    todos = repo.list_todos(tid)
    found = upcoming_deadlines([], [], date(2027, 3, 15), todos)
    assert [(d.description, d.urgency) for d in found] == [
        ("Buy travel insurance", "overdue"),
        ("Check visa / entry requirements for each country", "within 30 days"),
        ("Ask a travel doctor about recommended vaccinations", "later")]  # 31 days away
    html = trip_summary_html(repo.load_bundle(tid), date(2027, 3, 15))
    assert "1 of 4 done" in html and "Alex pays Sam NZD 25.20" in html


def test_duplicate_trip_copies_checklist(repo):
    tid = load_demo_trip(repo)
    copy = repo.duplicate_trip(tid, "Copy")
    assert [t.title for t in repo.list_todos(copy)] == [t.title for t in repo.list_todos(tid)]


def test_new_columns_migrate_on_existing_database(tmp_path):
    path = tmp_path / "old.db"
    Repository(path)  # current schema
    conn = sqlite3.connect(path)
    conn.executescript("ALTER TABLE expenses DROP COLUMN paid_by; ALTER TABLE trips DROP COLUMN traveller_names;")
    conn.close()
    repo = Repository(path)  # re-adds the missing columns
    tid = repo.create_trip(Trip(name="T", traveller_names=["A", "B"]))
    repo.add_expense(Expense(trip_id=tid, description="x", paid_by="A"))
    assert repo.get_trip(tid).traveller_names == ["A", "B"] and repo.list_expenses(tid)[0].paid_by == "A"


def test_checklist_rows_to_todos():
    import pandas as pd

    from trip_explorer.ui.checklist import rows_to_todos

    df = pd.DataFrame([
        {"id": 7, "Done": True, "Task": " Insurance ", "Due": date(2027, 3, 1), "Notes": None},
        {"id": None, "Done": None, "Task": "New row", "Due": pd.Timestamp("2027-04-02"), "Notes": "x"},
        {"id": None, "Done": False, "Task": "  ", "Due": None, "Notes": None},
    ])
    items, skipped = rows_to_todos(df, trip_id=3)
    assert skipped == 1
    assert [(t.id, t.title, t.done, t.due, t.notes) for t in items] == [
        (7, "Insurance", True, date(2027, 3, 1), ""), (None, "New row", False, date(2027, 4, 2), "x")]
