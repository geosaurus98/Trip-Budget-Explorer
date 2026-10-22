from datetime import date
from decimal import Decimal as D

import pytest

from trip_explorer.calculations import trip_stats
from trip_explorer.deadlines import upcoming_deadlines
from trip_explorer.demo import load_demo_trip
from trip_explorer.models import Destination, Expense, TransportLeg, Trip, TripBundle
from trip_explorer.report import trip_summary_html
from trip_explorer.storage import Repository
from trip_explorer.transfer import export_json, parse_json

TODAY = date(2027, 3, 1)


@pytest.fixture
def repo(tmp_path):
    return Repository(tmp_path / "t.db")


# --------------------------------------------------------------------------- deadlines


def test_book_by_shown_until_booked_and_flags_overdue():
    legs = [TransportLeg(trip_id=1, origin="A", destination="B", book_by=date(2027, 2, 20)),
            TransportLeg(trip_id=1, origin="B", destination="C", book_by=date(2027, 2, 20), status="booked")]
    expenses = [Expense(trip_id=1, description="Hotel", book_by=date(2027, 3, 5)),
                Expense(trip_id=1, description="Tour", book_by=date(2027, 3, 20)),
                Expense(trip_id=1, description="Later", book_by=date(2027, 6, 1))]
    found = upcoming_deadlines(legs, expenses, TODAY)
    assert [(d.description, d.urgency) for d in found] == [
        ("A → B (other)", "overdue"), ("Hotel", "this week"), ("Tour", "within 30 days"), ("Later", "later")]
    assert found[0].days_left == -9


def test_cancellation_deadline_only_for_booked_and_not_lapsed():
    expenses = [Expense(trip_id=1, description="Booked", status="booked", cancel_by=date(2027, 3, 10)),
                Expense(trip_id=1, description="Lapsed", status="booked", cancel_by=date(2027, 2, 1)),
                Expense(trip_id=1, description="Not booked", cancel_by=date(2027, 3, 10))]
    found = upcoming_deadlines([], expenses, TODAY)
    assert [(d.description, d.kind, d.label) for d in found] == [("Booked", "cancel", "Free cancellation until")]


def test_deadlines_persist_and_round_trip_json(repo):
    tid = load_demo_trip(repo)
    found = upcoming_deadlines(repo.list_legs(tid), repo.list_expenses(tid), TODAY)
    assert len(found) == 5
    copy_id = repo.save_bundle(parse_json(export_json(repo.load_bundle(tid))))
    copied = upcoming_deadlines(repo.list_legs(copy_id), repo.list_expenses(copy_id), TODAY)
    assert [(d.due, d.description) for d in copied] == [(d.due, d.description) for d in found]


# --------------------------------------------------------------------------- whole trips


def test_trip_stats_per_person_per_night():
    trip = Trip(name="T", travellers=2, start_date=date(2027, 6, 1), end_date=date(2027, 6, 11))
    expenses = [Expense(trip_id=1, description="x", unit_price=D("1000"), currency="NZD")]
    stats = trip_stats(trip, [], expenses, [])
    assert stats.nights == 10 and stats.per_person_per_night_nzd == D("50.00")


def test_trip_stats_falls_back_to_stop_nights_and_handles_unknown():
    stops = [Destination(trip_id=1, name="A", arrival=date(2027, 1, 1), departure=date(2027, 1, 4)),
             Destination(trip_id=1, name="B", arrival=date(2027, 1, 4), departure=date(2027, 1, 6))]
    assert trip_stats(Trip(name="T"), [], [], stops).nights == 5
    empty = trip_stats(Trip(name="T"), [], [], [])
    assert empty.nights is None and empty.per_person_per_night_nzd is None


def test_duplicate_trip_is_independent(repo):
    tid = load_demo_trip(repo)
    copy_id = repo.duplicate_trip(tid, "My version")
    copy = repo.get_trip(copy_id)
    assert copy.name == "My version" and not copy.is_demo
    assert trip_stats(copy, repo.list_legs(copy_id), repo.list_expenses(copy_id),
                      repo.list_destinations(copy_id)).summary.total_nzd == \
        trip_stats(repo.get_trip(tid), repo.list_legs(tid), repo.list_expenses(tid),
                   repo.list_destinations(tid)).summary.total_nzd
    assert len(repo.list_options(copy_id)) == 2
    repo.delete_expense(repo.list_expenses(copy_id)[0].id)
    assert len(repo.list_expenses(tid)) == len(repo.list_expenses(copy_id)) + 1


# --------------------------------------------------------------------------- printable summary


def test_summary_html_contains_key_sections(repo):
    tid = load_demo_trip(repo)
    html = trip_summary_html(repo.load_bundle(tid), TODAY)
    for text in ("Fictional demo trip", "NZD 5,720.75", "Itinerary", "Transport", "Deadlines",
                 "Route comparisons", "Not worth it", "1 IDR = NZD 0.000105"):
        assert text in html, text
    assert "<script" not in html and "http" not in html  # self-contained, nothing external


def test_summary_html_escapes_user_text():
    bundle = TripBundle(trip=Trip(name="<script>alert(1)</script>"),
                        destinations=[Destination(trip_id=None, id=1, name="A & B <i>")])
    html = trip_summary_html(bundle, TODAY)
    assert "<script>alert" not in html and "&lt;script&gt;" in html and "A &amp; B &lt;i&gt;" in html


def test_summary_html_for_empty_trip():
    html = trip_summary_html(TripBundle(trip=Trip(name="Empty")), TODAY)
    assert "No stops yet" in html and "No transport legs yet" in html and "Route comparisons" not in html
