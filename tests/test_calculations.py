from datetime import date
from decimal import Decimal as D

import pytest

from trip_explorer.calculations import (
    expense_total_nzd,
    itinerary_issues,
    leg_total_nzd,
    line_total_nzd,
    nights_between,
    summarize,
)
from trip_explorer.models import Destination, Expense, TransportLeg, Trip
from trip_explorer.money import MissingRateError, convert_to_nzd, round_money, to_decimal


def make_trip(**kw):
    defaults = dict(name="T", travellers=2, budget_nzd=D("1000"), rates={"IDR": D("0.0001"), "SGD": D("1.25")})
    defaults.update(kw)
    return Trip(**defaults)


# --------------------------------------------------------------------------- currency


def test_conversion_uses_nzd_per_unit():
    rates = {"SGD": D("1.25"), "IDR": D("0.000105")}
    assert convert_to_nzd(D("100"), "SGD", rates) == D("125.00")
    assert round_money(convert_to_nzd(D("1000000"), "IDR", rates)) == D("105.00")


def test_nzd_needs_no_rate_and_codes_are_case_insensitive():
    assert convert_to_nzd(D("10"), "nzd", {}) == D("10")
    assert convert_to_nzd(D("10"), "sgd", {"SGD": D("1.5")}) == D("15.0")


def test_missing_rate_raises():
    with pytest.raises(MissingRateError):
        convert_to_nzd(D("1"), "EUR", {})


def test_round_half_up_to_cents():
    assert round_money(D("0.005")) == D("0.01")
    assert round_money(D("2.344999")) == D("2.34")


def test_to_decimal_avoids_float_noise():
    assert to_decimal(0.1) == D("0.1")
    assert to_decimal("1,234.50") == D("1234.50")
    with pytest.raises(ValueError):
        to_decimal("abc")


# --------------------------------------------------------------------------- basis


def test_per_person_is_multiplied_by_travellers_group_is_not():
    assert line_total_nzd(D("100"), D("1"), "NZD", "per_person", 3, {}) == D("300.00")
    assert line_total_nzd(D("100"), D("1"), "NZD", "group", 3, {}) == D("100.00")


def test_leg_total_combines_fare_and_extras_with_basis():
    trip = make_trip(travellers=2)
    pp = TransportLeg(trip_id=1, origin="A", destination="B", base_fare=D("100"), extra_costs=D("20"),
                      currency="SGD", basis="per_person")
    group = TransportLeg(trip_id=1, origin="A", destination="B", base_fare=D("100"), extra_costs=D("20"),
                         currency="SGD", basis="group")
    assert leg_total_nzd(pp, trip) == D("300.00")  # (100+20) × 2 × 1.25
    assert leg_total_nzd(group, trip) == D("150.00")


# --------------------------------------------------------------------------- accommodation


def test_nights_from_dates():
    assert nights_between(date(2027, 6, 1), date(2027, 6, 5)) == 4
    assert nights_between(date(2027, 6, 1), date(2027, 6, 1)) == 0
    assert nights_between(date(2027, 6, 5), date(2027, 6, 1)) is None
    assert nights_between(None, date(2027, 6, 1)) is None


def test_accommodation_group_nightly_price_uses_stop_nights_once():
    trip = make_trip(travellers=2)
    stop = Destination(trip_id=1, id=7, name="Bali", arrival=date(2027, 6, 1), departure=date(2027, 6, 5))
    hotel = Expense(trip_id=1, description="Hotel", category="accommodation", destination_id=7,
                    unit_price=D("100"), quantity=D("1"), currency="NZD", basis="group", nights_from_stop=True)
    # 100 per night for the room × 4 nights — NOT also × 2 travellers.
    assert expense_total_nzd(hotel, trip, {7: stop}) == D("400.00")


def test_accommodation_per_person_nightly_price():
    trip = make_trip(travellers=3)
    stop = Destination(trip_id=1, id=7, name="Hostel", arrival=date(2027, 6, 1), departure=date(2027, 6, 3))
    dorm = Expense(trip_id=1, description="Dorm", category="accommodation", destination_id=7,
                   unit_price=D("30"), currency="NZD", basis="per_person", nights_from_stop=True)
    assert expense_total_nzd(dorm, trip, {7: stop}) == D("180.00")  # 30 × 2 nights × 3 people


def test_linked_nights_follow_date_edits_and_fall_back_when_unlinked():
    trip = make_trip()
    stop = Destination(trip_id=1, id=7, name="X", arrival=date(2027, 6, 1), departure=date(2027, 6, 3))
    hotel = Expense(trip_id=1, description="H", category="accommodation", destination_id=7, unit_price=D("50"),
                    quantity=D("2"), currency="NZD", basis="group", nights_from_stop=True)
    assert expense_total_nzd(hotel, trip, {7: stop}) == D("100.00")
    stop.departure = date(2027, 6, 6)
    assert expense_total_nzd(hotel, trip, {7: stop}) == D("250.00")
    assert expense_total_nzd(hotel, trip, {}) == D("100.00")  # stop deleted → entered quantity


# --------------------------------------------------------------------------- totals


def test_budget_summary_totals_and_per_traveller():
    trip = make_trip(travellers=2, budget_nzd=D("500"))
    legs = [TransportLeg(trip_id=1, origin="A", destination="B", base_fare=D("100"), currency="NZD",
                         basis="per_person", duration_hours=D("2.5"))]
    expenses = [
        Expense(trip_id=1, description="Food", category="food", unit_price=D("10"), quantity=D("5"),
                currency="NZD", basis="per_person"),
        Expense(trip_id=1, description="Tour", category="activities", unit_price=D("1000"), currency="SGD",
                basis="group"),
    ]
    s = summarize(trip, legs, expenses, [])
    assert s.by_category == {"transport": D("200.00"), "food": D("100.00"), "activities": D("1250.00")}
    assert s.total_nzd == D("1550.00")
    assert s.per_traveller_nzd == D("775.00")
    assert s.remaining_nzd == D("-1050.00")
    assert s.transport_hours == D("2.5")


def test_unpriced_lines_are_excluded_and_flagged():
    trip = make_trip(rates={})
    expenses = [Expense(trip_id=1, description="Euro thing", unit_price=D("10"), currency="EUR")]
    s = summarize(trip, [], expenses, [])
    assert s.total_nzd == D("0")
    assert len(s.unpriced_lines) == 1 and "EUR" in s.unpriced_lines[0].problem


def test_breakdown_lines_sum_to_total():
    trip = make_trip(travellers=3, rates={"IDR": D("0.000105")})
    expenses = [Expense(trip_id=1, description=f"e{i}", unit_price=D("33333"), currency="IDR", basis="per_person")
                for i in range(7)]
    s = summarize(trip, [], expenses, [])
    assert sum(l.total_nzd for l in s.lines) == s.total_nzd


# --------------------------------------------------------------------------- itinerary checks


def test_itinerary_flags_invalid_ranges_and_overlaps():
    trip = make_trip(start_date=date(2027, 6, 1), end_date=date(2027, 6, 10))
    stops = [
        Destination(trip_id=1, name="A", arrival=date(2027, 6, 1), departure=date(2027, 6, 4), latitude=0, longitude=0),
        Destination(trip_id=1, name="B", arrival=date(2027, 6, 3), departure=date(2027, 6, 2), latitude=0, longitude=0),
        Destination(trip_id=1, name="C", arrival=date(2027, 6, 5), departure=date(2027, 6, 12)),
    ]
    issues = " | ".join(itinerary_issues(trip, stops))
    assert "B): departure is before arrival" in issues
    assert "overlap" in issues
    assert "after the trip end date" in issues
    assert "no coordinates" in issues


def test_same_day_transfer_is_not_an_overlap():
    trip = make_trip()
    stops = [
        Destination(trip_id=1, name="A", arrival=date(2027, 6, 1), departure=date(2027, 6, 3), latitude=0, longitude=0),
        Destination(trip_id=1, name="B", arrival=date(2027, 6, 3), departure=date(2027, 6, 4), latitude=0, longitude=0),
    ]
    assert itinerary_issues(trip, stops) == []
