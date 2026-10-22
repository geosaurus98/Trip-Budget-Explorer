from datetime import date
from decimal import Decimal as D

import pytest

from trip_explorer.calculations import summarize
from trip_explorer.comparison import compare_options, summarize_option
from trip_explorer.daily import daily_breakdown, split_evenly
from trip_explorer.demo import build_demo_bundle, load_demo_trip
from trip_explorer.history import accuracy_report
from trip_explorer.models import Destination, Expense, RouteOption, TransportLeg, Trip
from trip_explorer.scenarios import saving_margin, with_price_change, with_rate_change
from trip_explorer.storage import Repository


@pytest.fixture
def repo(tmp_path):
    return Repository(tmp_path / "t.db")


def active(repo, tid):
    return (repo.get_trip(tid), repo.list_legs(tid), repo.list_expenses(tid), repo.list_destinations(tid))


# --------------------------------------------------------------------------- cost history


def test_history_records_cost_changes_only(repo):
    tid = repo.create_trip(Trip(name="T"))
    eid = repo.add_expense(Expense(trip_id=tid, description="Hotel", unit_price=D("100"), currency="NZD"))
    exp = repo.list_expenses(tid)[0]

    exp.notes = "nicer room?"  # not a cost field
    repo.update_expense(exp)
    assert len(repo.cost_history("expense", eid)) == 1

    exp.unit_price, exp.status = D("120"), "researched"
    repo.update_expense(exp)
    exp.status = "booked"
    repo.update_expense(exp)
    history = repo.cost_history("expense", eid)
    assert [(h.status, h.amount) for h in history] == [
        ("estimated", D("100")), ("researched", D("120")), ("booked", D("120"))]


def test_leg_history_tracks_fare_plus_extras(repo):
    tid = repo.create_trip(Trip(name="T"))
    lid = repo.add_leg(TransportLeg(trip_id=tid, origin="A", destination="B", base_fare=D("50"),
                                    extra_costs=D("10"), currency="NZD"))
    leg = repo.list_legs(tid)[0]
    leg.extra_costs = D("25")
    repo.update_leg(leg)
    assert [h.amount for h in repo.cost_history("leg", lid)] == [D("60"), D("75")]


def test_history_is_removed_with_its_record(repo):
    tid = load_demo_trip(repo)
    leg = repo.list_legs(tid)[0]
    exp = repo.list_expenses(tid)[0]
    option = repo.list_options(tid)[0]
    option_leg = repo.list_legs(tid, option.id)[0]
    repo.delete_leg(leg.id)
    repo.delete_expense(exp.id)
    repo.delete_option(option.id)
    assert repo.cost_history("leg", leg.id) == []
    assert repo.cost_history("expense", exp.id) == []
    assert repo.cost_history("leg", option_leg.id) == []


def test_accuracy_report_and_projection(repo):
    tid = repo.create_trip(Trip(name="T", travellers=2, rates={"SGD": D("1.25")}))
    hotel = repo.add_expense(Expense(trip_id=tid, description="Hotel", unit_price=D("100"), currency="NZD"))
    repo.add_expense(Expense(trip_id=tid, description="Tour", unit_price=D("80"), currency="SGD"))  # NZD 100
    exp = next(e for e in repo.list_expenses(tid) if e.id == hotel)
    exp.unit_price, exp.status = D("110"), "booked"
    repo.update_expense(exp)

    report = accuracy_report(*active(repo, tid), repo.first_snapshots(tid))
    row = next(r for r in report.rows if r.record_id == hotel)
    assert (row.first_nzd, row.current_nzd, row.change_pct) == (D("100.00"), D("110.00"), D("10.00"))
    assert row.first_status == "estimated" and row.current_status == "booked"
    assert report.booked_change_pct == D("10.00")
    assert report.not_booked_current_nzd == D("100.00")
    assert report.projected_extra_nzd == D("10.00")


def test_accuracy_report_without_bookings_has_no_projection(repo):
    tid = repo.save_bundle(build_demo_bundle())  # demo data without the illustrative bookings
    report = accuracy_report(*active(repo, tid), repo.first_snapshots(tid))
    assert report.booked_change_pct is None and report.projected_extra_nzd is None
    assert all(r.change_nzd == 0 for r in report.rows)


def test_demo_bookings_show_up_in_history(repo):
    tid = load_demo_trip(repo)
    report = accuracy_report(*active(repo, tid), repo.first_snapshots(tid))
    booked = [r for r in report.rows if r.current_status == "booked"]
    assert len(booked) == 2 and all(r.first_status == "estimated" and r.change_nzd > 0 for r in booked)
    assert report.booked_change_pct > 0 and report.projected_extra_nzd > 0


# --------------------------------------------------------------------------- daily view


def test_split_evenly_is_exact():
    parts = split_evenly(D("100.00"), 3)
    assert parts == [D("33.34"), D("33.33"), D("33.33")] and sum(parts) == D("100.00")
    assert sum(split_evenly(D("-0.05"), 2)) == D("-0.05")
    with pytest.raises(ValueError):
        split_evenly(D("1"), 0)


def test_daily_breakdown_rules():
    trip = Trip(name="T", travellers=1, start_date=date(2027, 6, 1), end_date=date(2027, 6, 4))
    stops = [Destination(trip_id=1, id=1, name="A", arrival=date(2027, 6, 1), departure=date(2027, 6, 3)),
             Destination(trip_id=1, id=2, name="B", arrival=date(2027, 6, 3), departure=date(2027, 6, 4))]
    legs = [TransportLeg(trip_id=1, id=1, origin="A", destination="B", base_fare=D("30"), currency="NZD")]
    expenses = [
        Expense(trip_id=1, id=1, description="Hotel A", category="accommodation", destination_id=1,
                unit_price=D("50"), nights_from_stop=True, currency="NZD"),
        Expense(trip_id=1, id=2, description="Food", category="food", unit_price=D("10"), quantity=D("3"),
                currency="NZD"),
        Expense(trip_id=1, id=3, description="Tour B", category="activities", destination_id=2,
                unit_price=D("40"), currency="NZD"),
        Expense(trip_id=1, id=4, description="Insurance", category="misc", unit_price=D("20"), currency="NZD"),
    ]
    result = daily_breakdown(trip, legs, expenses, stops)
    days = {r.day: r for r in result.days}
    assert list(days) == [date(2027, 6, 1), date(2027, 6, 2), date(2027, 6, 3)]
    assert days[date(2027, 6, 1)].by_category == {"accommodation": D("50"), "food": D("10")}
    assert days[date(2027, 6, 3)].by_category["transport"] == D("30")  # leaves A on 3 June
    assert days[date(2027, 6, 3)].by_category["activities"] == D("40")
    assert days[date(2027, 6, 3)].stop == "B"
    assert result.undated == {"misc": D("20.00")} and result.undated_items == ["Insurance"]
    total = sum(r.total for r in result.days) + result.undated_total
    assert total == summarize(trip, legs, expenses, stops).total_nzd


def test_leg_with_explicit_departure_date():
    trip = Trip(name="T")
    leg = TransportLeg(trip_id=1, id=1, origin="X", destination="Y", base_fare=D("5"), currency="NZD",
                       depart_local="2027-07-09 06:00")
    assert daily_breakdown(trip, [leg], [], []).days[0].day == date(2027, 7, 9)


def test_demo_daily_breakdown_adds_up(repo):
    tid = load_demo_trip(repo)
    trip, legs, expenses, dests = active(repo, tid)
    result = daily_breakdown(trip, legs, expenses, dests)
    assert sum(r.total for r in result.days) + result.undated_total == summarize(trip, legs, expenses, dests).total_nzd


# --------------------------------------------------------------------------- scenarios


def make_comparison(trip, a_legs, b_legs):
    a = summarize_option(trip, RouteOption(trip_id=1, id=1, name="a", origin="Y", destination="M"), a_legs, [], [])
    b = summarize_option(trip, RouteOption(trip_id=1, id=2, name="b", origin="Y", destination="M"), b_legs, [], [])
    return compare_options(a, b, trip.travellers)


def leg(fare, currency="NZD", hours="1"):
    return TransportLeg(trip_id=1, origin="Y", destination="M", base_fare=D(fare), currency=currency,
                        duration_hours=D(hours))


def test_rate_change_scales_foreign_prices_only():
    trip = Trip(name="T", rates={"IDR": D("0.0001")})
    shifted = with_rate_change(trip, D("10"))
    assert shifted.rates["IDR"] == D("0.00011") and trip.rates["IDR"] == D("0.0001")
    c = make_comparison(shifted, [leg("100")], [leg("1000000", "IDR")])
    assert c.b.total_nzd == D("110.00") and c.a.total_nzd == D("100.00")


def test_price_change_copies_without_mutating():
    legs = [leg("100")]
    expenses = [Expense(trip_id=1, description="x", unit_price=D("50"), currency="NZD")]
    new_legs, new_exps = with_price_change(legs, expenses, D("-20"))
    assert new_legs[0].base_fare == D("80") and new_exps[0].unit_price == D("40")
    assert legs[0].base_fare == D("100") and expenses[0].unit_price == D("50")
    with pytest.raises(ValueError):
        with_price_change(legs, expenses, D("-100"))


def test_saving_margin():
    trip = Trip(name="T", travellers=1)
    c = make_comparison(trip, [leg("120")], [leg("100")])
    m = saving_margin(c)
    assert (m.cheaper, m.cheaper_rise_pct, m.other_fall_pct) == ("B", D("20.00"), D("16.67"))
    # Check: B at +20% equals A.
    raised, _ = with_price_change([leg("100")], [], m.cheaper_rise_pct)
    assert make_comparison(trip, [leg("120")], raised).cheaper is None
    assert saving_margin(make_comparison(trip, [leg("100")], [leg("100")])) is None
    assert saving_margin(make_comparison(trip, [leg("100")], [leg("0")])).cheaper_rise_pct is None


def test_linked_accommodation_history_uses_live_nights(repo):
    tid = repo.create_trip(Trip(name="T"))
    did = repo.add_destination(Destination(trip_id=tid, name="X", arrival=date(2027, 1, 1), departure=date(2027, 1, 3)))
    eid = repo.add_expense(Expense(trip_id=tid, description="Hotel", category="accommodation", destination_id=did,
                                   unit_price=D("100"), quantity=D("1"), currency="NZD", nights_from_stop=True))
    dest = repo.list_destinations(tid)[0]
    dest.departure = date(2027, 1, 5)
    repo.update_destination(dest)
    assert [h.quantity for h in repo.cost_history("expense", eid)] == [D("2"), D("4")]
