from decimal import Decimal as D

from trip_explorer.comparison import compare_options, summarize_option
from trip_explorer.models import Expense, RouteOption, TransportLeg, Trip

TRIP = Trip(name="T", travellers=2, rates={"IDR": D("0.0001")})


def option(oid, headline=None):
    return RouteOption(trip_id=1, id=oid, name=f"opt{oid}", origin="Yogyakarta", destination="Medan",
                       headline_leg_id=headline)


def leg(lid, origin, dest, fare, extras="0", hours="1", basis="per_person"):
    return TransportLeg(trip_id=1, id=lid, origin=origin, destination=dest, base_fare=D(fare), extra_costs=D(extras),
                        duration_hours=D(hours), currency="NZD", basis=basis)


def summary(oid, legs, expenses=(), headline=None):
    return summarize_option(TRIP, option(oid, headline), list(legs), list(expenses), [])


def direct_and_via():
    a = summary(1, [leg(1, "Yogyakarta", "Medan", "200", extras="40", hours="6")])
    b = summary(2, [
        leg(2, "Yogyakarta", "Jakarta", "40", extras="10", hours="7.5"),
        leg(3, "Jakarta", "Medan", "100", extras="30", hours="5.5"),
    ], [Expense(trip_id=1, description="Hotel", category="accommodation", unit_price=D("60"), currency="NZD",
                basis="group")])
    return a, b


def test_option_summary_breakdown():
    a, b = direct_and_via()
    assert a.total_nzd == D("480.00")  # (200+40) × 2
    assert b.transport_nzd == D("360.00")  # (40+10+100+30) × 2
    assert b.accommodation_nzd == D("60.00")
    assert b.total_nzd == D("420.00")
    assert b.per_person_nzd == D("210.00")
    assert (a.transfers, b.transfers) == (0, 1)
    assert b.travel_hours == D("13.0")
    assert b.headline_leg.id == 3 and b.headline_auto_selected  # largest base fare
    assert b.warnings == []


def test_cheaper_but_slower_gives_savings_per_hour():
    a, b = direct_and_via()
    c = compare_options(a, b, 2)
    assert c.cost_difference_nzd == D("-60.00")
    assert c.time_difference_hours == D("7.0")
    assert (c.cheaper, c.faster) == ("B", "A")
    assert c.savings_per_extra_hour_nzd == D("8.57")  # 60 / 7


def test_break_even_allowance_and_remaining():
    a, b = direct_and_via()
    be = compare_options(a, b, 2).break_even
    assert be.cheap_fare_label == "B"
    assert be.initial_allowance_nzd == D("200.00")  # (200 − 100) × 2
    # Non-headline: B = 420 − 200 = 220; A = 480 − 400 = 80 → extra 140.
    assert be.extra_costs_nzd == D("140.00")
    assert be.remaining_allowance_nzd == D("60.00")
    assert be.remaining_allowance_nzd == a.total_nzd - b.total_nzd
    assert be.saving_survives and be.excess_nzd == 0


def test_break_even_reports_excess_when_extras_exceed_saving():
    a = summary(1, [leg(1, "Yogyakarta", "Medan", "200", hours="6")])
    b = summary(2, [leg(2, "Yogyakarta", "Jakarta", "90", hours="7"), leg(3, "Jakarta", "Medan", "150", hours="5")])
    be = compare_options(a, b, 2).break_even
    assert be.initial_allowance_nzd == D("100.00")  # (200 − 150) × 2
    assert be.extra_costs_nzd == D("180.00")
    assert be.remaining_allowance_nzd == D("-80.00")
    assert not be.saving_survives and be.excess_nzd == D("80.00")


def test_explicit_headline_leg_overrides_auto():
    b = summary(2, [leg(2, "Yogyakarta", "Jakarta", "300"), leg(3, "Jakarta", "Medan", "100")], headline=3)
    assert b.headline_leg.id == 3 and not b.headline_auto_selected


def test_equal_durations_no_division_by_zero():
    a = summary(1, [leg(1, "Yogyakarta", "Medan", "200", hours="6")])
    b = summary(2, [leg(2, "Yogyakarta", "Medan", "150", hours="6")])
    c = compare_options(a, b, 2)
    assert c.time_difference_hours == 0 and c.faster is None
    assert c.cheaper == "B" and c.savings_per_extra_hour_nzd is None
    assert "Same door-to-door time" in c.verdict


def test_cheaper_and_faster_dominates():
    a = summary(1, [leg(1, "Yogyakarta", "Medan", "200", hours="6")])
    b = summary(2, [leg(2, "Yogyakarta", "Medan", "150", hours="4")])
    c = compare_options(a, b, 2)
    assert c.cheaper == c.faster == "B" and c.savings_per_extra_hour_nzd is None
    assert "both cheaper" in c.verdict


def test_identical_options():
    a = summary(1, [leg(1, "Yogyakarta", "Medan", "200", hours="6")])
    b = summary(2, [leg(2, "Yogyakarta", "Medan", "200", hours="6")])
    c = compare_options(a, b, 2)
    assert c.cheaper is None and c.faster is None and c.savings_per_extra_hour_nzd is None
    assert c.break_even.cheap_fare_label is None and c.break_even.initial_allowance_nzd == 0


def test_empty_option_does_not_crash():
    a = summary(1, [])
    b = summary(2, [leg(2, "Yogyakarta", "Medan", "200", hours="6")])
    c = compare_options(a, b, 2)
    assert a.headline_leg is None and a.total_nzd == 0
    assert "No transport legs" in a.warnings[0]
    assert c.cheaper == "A"


def test_route_gaps_and_mismatched_endpoints_warn():
    s = summary(1, [leg(1, "Yogyakarta", "Solo", "10"), leg(2, "Surabaya", "Jakarta", "10")])
    text = " ".join(s.warnings)
    assert "Gap between legs" in text and "not the option end" in text
