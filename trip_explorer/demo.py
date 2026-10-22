"""Fictional demo trip. Every date, price, duration and rate here is ILLUSTRATIVE.

Nothing in this file is current or verified travel information. It exists to
show how the app works and can be edited or deleted freely.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

from .models import Destination, Expense, RouteOption, TransportLeg, Trip, TripBundle
from .storage import Repository

DEMO_NAME = "DEMO (fictional): Indonesia to Singapore"
DEMO_NOTE = (
    "Fictional planning example. All dates, prices, durations and exchange rates are illustrative "
    "placeholders, not current or verified travel information. Edit freely."
)
FIRST_SETUP_KEY = "first_setup_done"


def build_demo_bundle() -> TripBundle:
    trip = Trip(
        name=DEMO_NAME,
        start_date=date(2027, 6, 1),
        end_date=date(2027, 6, 23),
        travellers=2,
        budget_nzd=D("6000"),
        # NZD per 1 unit of foreign currency (illustrative).
        rates={"IDR": D("0.000105"), "SGD": D("1.27")},
        # Illustrative personal settings for the "is it worth it?" verdict.
        time_value_nzd_per_hour=D("15"),
        disruption_penalty_hours=D("2"),
        notes=DEMO_NOTE,
        is_demo=True,
    )

    stops = [
        # (name, country, arrival, departure, lat, lon, notes)
        ("Bali (Ubud)", "Indonesia", date(2027, 6, 1), date(2027, 6, 5), -8.5069, 115.2625, "Arrive DPS."),
        ("Banyuwangi / Ijen", "Indonesia", date(2027, 6, 5), date(2027, 6, 7), -8.2192, 114.3691, "Ijen night hike."),
        ("Bromo (Cemoro Lawang)", "Indonesia", date(2027, 6, 7), date(2027, 6, 8), -7.9425, 112.9530, "Sunrise jeep."),
        ("Malang", "Indonesia", date(2027, 6, 8), date(2027, 6, 10), -7.9666, 112.6326, ""),
        ("Yogyakarta", "Indonesia", date(2027, 6, 10), date(2027, 6, 13), -7.7956, 110.3695, "Borobudur, Prambanan."),
        ("Jakarta", "Indonesia", date(2027, 6, 13), date(2027, 6, 14), -6.2088, 106.8456,
         "Repositioning stop for the cheaper flight to Medan — see Compare routes."),
        ("Medan", "Indonesia", date(2027, 6, 14), date(2027, 6, 15), 3.5952, 98.6722, ""),
        ("Bukit Lawang", "Indonesia", date(2027, 6, 15), date(2027, 6, 18), 3.5500, 98.1167, "Jungle trek."),
        ("Lake Toba (Tuk Tuk)", "Indonesia", date(2027, 6, 18), date(2027, 6, 21), 2.6630, 98.8590, ""),
        ("Singapore", "Singapore", date(2027, 6, 21), date(2027, 6, 23), 1.3521, 103.8198, "Fly home."),
    ]
    destinations = [
        Destination(trip_id=None, id=i, name=n, country=c, arrival=a, departure=d, latitude=lat, longitude=lon, notes=no)
        for i, (n, c, a, d, lat, lon, no) in enumerate(stops, start=1)
    ]
    dest_id = {d.name: d.id for d in destinations}

    def leg(lid, origin, dest, mode, hours, fare, extras, basis="per_person", currency="IDR", notes="", option=None,
            depart="", arrive="", disruptive=False):
        return TransportLeg(
            trip_id=None, id=lid, option_id=option, origin=origin, destination=dest, mode=mode,
            duration_hours=D(hours), base_fare=D(fare), extra_costs=D(extras), currency=currency, basis=basis,
            status="estimated", notes=notes, depart_local=depart, arrive_local=arrive, disruptive=disruptive,
        )

    legs = [
        leg(1, "Bali (Ubud)", "Banyuwangi / Ijen", "bus", "8", "250000", "60000",
            notes="Bus incl. Gilimanuk–Ketapang ferry; extras = ferry ticket and pickup."),
        leg(2, "Banyuwangi / Ijen", "Bromo (Cemoro Lawang)", "taxi/private transfer", "7", "1500000", "0",
            basis="group", notes="Private car for the group."),
        leg(3, "Bromo (Cemoro Lawang)", "Malang", "taxi/private transfer", "3", "600000", "0", basis="group"),
        leg(4, "Malang", "Yogyakarta", "train", "8.5", "350000", "40000", notes="Extras = station transfers."),
        # Active plan for Yogyakarta → Medan currently goes via Jakarta (matches Option B below).
        leg(5, "Yogyakarta", "Jakarta", "train", "7.5", "450000", "100000",
            notes="Extras = station transfers both ends.", depart="2027-06-13 08:00", arrive="2027-06-13 14:30"),
        leg(6, "Jakarta", "Medan", "flight", "5.5", "1100000", "400000",
            notes="Extras = checked bag + airport transfers. 2.5 h flight, 5.5 h door to door. "
                  "Early flight (3:30 am wake-up), so marked disruptive.",
            depart="2027-06-14 06:00", arrive="2027-06-14 08:30", disruptive=True),
        leg(7, "Medan", "Bukit Lawang", "taxi/private transfer", "4", "700000", "0", basis="group"),
        leg(8, "Bukit Lawang", "Lake Toba (Tuk Tuk)", "taxi/private transfer", "7", "1200000", "60000",
            basis="group", notes="Car to Parapat + ferry to Tuk Tuk."),
        leg(9, "Lake Toba (Tuk Tuk)", "Singapore", "flight", "9", "1600000", "450000",
            notes="Ferry + car to KNO airport, then flight. Extras = bag + transfers."),
    ]

    def expense(eid, desc, category, price, qty="1", basis="group", currency="IDR", stop=None, nights=False,
                option=None, notes=""):
        return Expense(
            trip_id=None, id=eid, option_id=option, destination_id=dest_id.get(stop) if stop else None,
            description=desc, category=category, unit_price=D(price), quantity=D(qty), currency=currency,
            basis=basis, status="estimated", nights_from_stop=nights, notes=notes,
        )

    hotel_prices = {
        "Bali (Ubud)": "900000", "Banyuwangi / Ijen": "500000", "Bromo (Cemoro Lawang)": "600000",
        "Malang": "450000", "Yogyakarta": "600000", "Jakarta": "700000", "Medan": "500000",
        "Bukit Lawang": "350000", "Lake Toba (Tuk Tuk)": "400000",
    }
    expenses = []
    eid = 1
    for stop, price in hotel_prices.items():
        expenses.append(expense(eid, f"Hotel – {stop}", "accommodation", price, stop=stop, nights=True,
                                notes="Nightly price for one shared room (whole group)."))
        eid += 1
    expenses += [
        expense(eid, "Hotel – Singapore", "accommodation", "220", currency="SGD", stop="Singapore", nights=True,
                notes="Nightly price for one shared room (whole group)."),
        expense(eid + 1, "Food – Indonesia daily allowance", "food", "250000", qty="20", basis="per_person",
                notes="Per person per day × 20 days."),
        expense(eid + 2, "Food – Singapore daily allowance", "food", "60", qty="2", basis="per_person",
                currency="SGD", stop="Singapore"),
        expense(eid + 3, "Ijen crater tour", "activities", "450000", basis="per_person", stop="Banyuwangi / Ijen"),
        expense(eid + 4, "Bromo sunrise jeep", "activities", "1600000", stop="Bromo (Cemoro Lawang)"),
        expense(eid + 5, "Borobudur & Prambanan tickets", "activities", "700000", basis="per_person",
                stop="Yogyakarta"),
        expense(eid + 6, "2-day jungle trek", "activities", "1500000", basis="per_person", stop="Bukit Lawang"),
        expense(eid + 7, "Travel insurance", "misc", "180", basis="per_person", currency="NZD"),
        expense(eid + 8, "SIM card / data", "misc", "150000", basis="per_person"),
    ]
    next_eid = eid + 9

    options = [
        RouteOption(trip_id=None, id=1, name="Option A: Fly Yogyakarta → Medan", origin="Yogyakarta",
                    destination="Medan", headline_leg_id=101,
                    notes="Direct flight. Illustrative figures only."),
        RouteOption(trip_id=None, id=2, name="Option B: Train to Jakarta, then fly to Medan", origin="Yogyakarta",
                    destination="Medan", headline_leg_id=103,
                    notes="Cheaper flight from Jakarta, but needs a train, an extra hotel night and meals."),
    ]
    legs += [
        leg(101, "Yogyakarta", "Medan", "flight", "6.5", "2300000", "500000", option=1,
            notes="Extras = checked bag + YIA airport train + KNO transfer. 3 h flight, 6.5 h door to door."),
        leg(102, "Yogyakarta", "Jakarta", "train", "7.5", "450000", "100000", option=2,
            notes="Extras = station transfers both ends."),
        leg(103, "Jakarta", "Medan", "flight", "5.5", "1100000", "400000", option=2,
            notes="Extras = checked bag + airport transfers. Early flight (3:30 am wake-up).",
            depart="2027-06-14 06:00", arrive="2027-06-14 08:30", disruptive=True),
    ]
    expenses += [
        expense(next_eid, "Extra hotel night – Jakarta", "accommodation", "700000", qty="1", option=2,
                notes="Whole-group nightly price × 1 night."),
        expense(next_eid + 1, "Extra day of meals – Jakarta", "food", "200000", basis="per_person", option=2),
    ]

    # Illustrative deadlines (see Overview → Deadlines).
    for item in legs:
        if (item.origin, item.destination) == ("Jakarta", "Medan") and item.option_id is None:
            item.book_by = date(2027, 2, 1)
        if item.destination == "Singapore":
            item.cancel_by = date(2027, 5, 1)
    for item in expenses:
        if item.description == "2-day jungle trek":
            item.book_by = date(2027, 5, 1)
        elif item.description == "Hotel – Singapore":
            item.book_by = date(2027, 4, 1)
        elif item.description == "Hotel – Bali (Ubud)":
            item.cancel_by = date(2027, 5, 25)

    return TripBundle(trip=trip, destinations=destinations, legs=legs, expenses=expenses, options=options)


def _save_demo(repo: Repository) -> int:
    """Save the demo, then record two illustrative bookings so cost history has something to show."""
    trip_id = repo.save_bundle(build_demo_bundle())
    for exp in repo.list_expenses(trip_id):
        if exp.description == "Hotel – Bali (Ubud)":
            exp.unit_price, exp.status = D("980000"), "booked"
            exp.notes += " Illustrative booking: came in above the first estimate."
            repo.update_expense(exp)
    for leg in repo.list_legs(trip_id):
        if leg.destination == "Singapore":
            leg.base_fare, leg.status = D("1750000"), "booked"
            leg.notes += " Illustrative booking: fare rose before booking."
            repo.update_leg(leg)
    return trip_id


def demo_trip_ids(repo: Repository) -> list[int]:
    return [t.id for t in repo.list_trips() if t.is_demo]


def load_demo_trip(repo: Repository, replace_existing: bool = True) -> int:
    """Explicitly (re)load the demo trip. Existing demo trips are replaced, never duplicated."""
    existing = demo_trip_ids(repo)
    if existing and not replace_existing:
        return existing[0]
    for trip_id in existing:
        repo.delete_trip(trip_id)
    return _save_demo(repo)


def ensure_first_setup(repo: Repository) -> bool:
    """Seed the demo once, on the very first run against an empty database.

    Returns True if the demo was seeded. After the first run this is a no-op,
    even if you later delete the demo trip.
    """
    if repo.get_meta(FIRST_SETUP_KEY):
        return False
    seeded = False
    if not repo.list_trips():
        _save_demo(repo)
        seeded = True
    repo.set_meta(FIRST_SETUP_KEY, "1")
    return seeded
