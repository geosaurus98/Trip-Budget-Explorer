import csv
import io
import json
from datetime import date
from decimal import Decimal as D

import pytest

from trip_explorer.calculations import summarize
from trip_explorer.demo import DEMO_NAME, demo_trip_ids, ensure_first_setup, load_demo_trip
from trip_explorer.models import Destination, Expense, TransportLeg, Trip
from trip_explorer.storage import Repository
from trip_explorer.transfer import ImportValidationError, bundle_to_dict, budget_csv, export_json, parse_json


@pytest.fixture
def repo(tmp_path):
    return Repository(tmp_path / "test.db")


def active_total(repo, trip_id):
    trip = repo.get_trip(trip_id)
    return summarize(trip, repo.list_legs(trip_id), repo.list_expenses(trip_id), repo.list_destinations(trip_id))


def test_data_persists_across_repository_instances(tmp_path):
    path = tmp_path / "p.db"
    r1 = Repository(path)
    tid = r1.create_trip(Trip(name="Trip", travellers=2, budget_nzd=D("1234.56"), rates={"SGD": D("1.27")}))
    r1.add_destination(Destination(trip_id=tid, name="Singapore", latitude=1.35, longitude=103.8))
    r2 = Repository(path)  # simulates an app restart
    trip = r2.get_trip(tid)
    assert trip.budget_nzd == D("1234.56") and trip.rates == {"SGD": D("1.27")}
    assert [d.name for d in r2.list_destinations(tid)] == ["Singapore"]


def test_reorder_destinations(repo):
    tid = repo.create_trip(Trip(name="T"))
    ids = [repo.add_destination(Destination(trip_id=tid, name=n)) for n in "ABC"]
    repo.move_destination(ids[2], -1)
    assert [d.name for d in repo.list_destinations(tid)] == ["A", "C", "B"]
    repo.move_destination(ids[0], -1)  # already first: no-op
    assert [d.name for d in repo.list_destinations(tid)] == ["A", "C", "B"]


def test_edit_and_delete_update_totals(repo):
    tid = repo.create_trip(Trip(name="T", travellers=2))
    lid = repo.add_leg(TransportLeg(trip_id=tid, origin="A", destination="B", base_fare=D("100"), currency="NZD"))
    eid = repo.add_expense(Expense(trip_id=tid, description="Food", unit_price=D("50"), currency="NZD"))
    assert active_total(repo, tid).total_nzd == D("250.00")

    leg = repo.list_legs(tid)[0]
    leg.basis = "group"
    repo.update_leg(leg)
    assert active_total(repo, tid).total_nzd == D("150.00")

    repo.delete_expense(eid)
    repo.delete_leg(lid)
    assert active_total(repo, tid).total_nzd == D("0")


def test_deleting_stop_unlinks_expense_and_falls_back_to_quantity(repo):
    tid = repo.create_trip(Trip(name="T"))
    did = repo.add_destination(Destination(trip_id=tid, name="X", arrival=date(2027, 1, 1), departure=date(2027, 1, 4)))
    repo.add_expense(Expense(trip_id=tid, description="Hotel", category="accommodation", destination_id=did,
                             unit_price=D("100"), quantity=D("3"), currency="NZD", nights_from_stop=True))
    assert active_total(repo, tid).total_nzd == D("300.00")
    dest = repo.list_destinations(tid)[0]
    dest.departure = date(2027, 1, 6)
    repo.update_destination(dest)
    assert active_total(repo, tid).total_nzd == D("500.00")
    repo.delete_destination(did)
    assert repo.list_expenses(tid)[0].destination_id is None
    assert active_total(repo, tid).total_nzd == D("300.00")


def test_options_are_excluded_from_active_totals(repo):
    tid = load_demo_trip(repo)
    before = active_total(repo, tid).total_nzd
    opt = repo.list_options(tid)[0]
    repo.add_leg(TransportLeg(trip_id=tid, option_id=opt.id, origin="X", destination="Y", base_fare=D("999"),
                              currency="NZD"))
    assert active_total(repo, tid).total_nzd == before


def test_apply_option_replaces_without_duplicating(repo):
    tid = load_demo_trip(repo)
    a, b = repo.list_options(tid)
    active_legs = repo.list_legs(tid)
    via_jakarta = [l.id for l in active_legs if "Jakarta" in (l.origin, l.destination)]
    jakarta_hotel = [e.id for e in repo.list_expenses(tid) if e.description == "Hotel – Jakarta"]
    before = active_total(repo, tid)

    repo.apply_option(a.id, via_jakarta, jakarta_hotel)

    after_legs = repo.list_legs(tid)
    routes = [(l.origin, l.destination) for l in after_legs]
    assert ("Yogyakarta", "Medan") in routes
    assert not any("Jakarta" in r for r in routes)
    assert len(after_legs) == len(active_legs) - 1
    # Copied leg sits where the replaced legs were.
    assert routes.index(("Yogyakarta", "Medan")) == routes.index(("Medan", "Bukit Lawang")) - 1

    # Replaced items are moved into a "Previous plan" option, not lost.
    options = repo.list_options(tid)
    previous = [o for o in options if o.name.startswith("Previous plan")]
    assert len(previous) == 1
    assert len(repo.list_legs(tid, previous[0].id)) == 2
    assert len(repo.list_expenses(tid, previous[0].id)) == 1

    # Total changes by exactly (option A legs) − (replaced legs + replaced hotel).
    after = active_total(repo, tid)
    hotel = next(l for l in before.lines if l.description == "Hotel – Jakarta").total_nzd
    replaced = sum(l.total_nzd for l in before.lines if l.kind == "transport" and "Jakarta" in l.description)
    added = sum(l.total_nzd for l in after.lines if l.kind == "transport" and l.description.startswith("Yogyakarta → Medan"))
    assert after.total_nzd == before.total_nzd - replaced - hotel + added

    # Option A itself is still intact for future comparisons and marked applied.
    a_after = next(o for o in options if o.id == a.id)
    assert a_after.applied_at and len(repo.list_legs(tid, a.id)) == 1


def test_apply_rejects_non_active_items(repo):
    tid = load_demo_trip(repo)
    a, b = repo.list_options(tid)
    b_leg = repo.list_legs(tid, b.id)[0]
    with pytest.raises(ValueError):
        repo.apply_option(a.id, [b_leg.id], [])
    assert len(repo.list_options(tid)) == 2  # transaction rolled back


def test_delete_trip_cascades(repo):
    tid = load_demo_trip(repo)
    repo.delete_trip(tid)
    assert repo.list_trips() == []
    assert repo.list_legs(tid) == [] and repo.list_expenses(tid) == []


# --------------------------------------------------------------------------- demo seeding


def test_first_setup_seeds_once_and_never_duplicates(repo):
    assert ensure_first_setup(repo) is True
    assert ensure_first_setup(repo) is False
    assert len(repo.list_trips()) == 1
    repo.delete_trip(repo.list_trips()[0].id)
    assert ensure_first_setup(repo) is False  # deleted demo stays deleted
    assert repo.list_trips() == []


def test_first_setup_skips_when_trips_exist(repo):
    repo.create_trip(Trip(name="Mine"))
    assert ensure_first_setup(repo) is False
    assert [t.name for t in repo.list_trips()] == ["Mine"]


def test_explicit_demo_reload_replaces_existing(repo):
    load_demo_trip(repo)
    load_demo_trip(repo)
    assert len(demo_trip_ids(repo)) == 1
    trip = repo.get_trip(demo_trip_ids(repo)[0])
    assert trip.name == DEMO_NAME and trip.is_demo


def test_demo_comparison_options_cover_yogyakarta_to_medan(repo):
    tid = load_demo_trip(repo)
    options = repo.list_options(tid)
    assert len(options) == 2
    for opt in options:
        legs = repo.list_legs(tid, opt.id)
        assert opt.origin == legs[0].origin == "Yogyakarta"
        assert opt.destination == legs[-1].destination == "Medan"


# --------------------------------------------------------------------------- JSON / CSV


def test_json_round_trip_preserves_everything(repo):
    tid = load_demo_trip(repo)
    original = repo.load_bundle(tid)
    text = export_json(original)
    new_id = repo.save_bundle(parse_json(text))
    copy = repo.load_bundle(new_id)

    def normalised(bundle):
        data = bundle_to_dict(bundle)
        # Ids are remapped on import; compare by structure instead.
        dest_names = {d.id: d.name for d in bundle.destinations}
        opt_names = {o.id: o.name for o in bundle.options}
        leg_names = {l.id: (l.origin, l.destination, l.option_id and opt_names[l.option_id]) for l in bundle.legs}
        for d in data["destinations"]:
            d.pop("id")
        for l in data["legs"]:
            l.pop("id")
            l["option_id"] = opt_names.get(l["option_id"])
        for e in data["expenses"]:
            e.pop("id")
            e["option_id"] = opt_names.get(e["option_id"])
            e["destination_id"] = dest_names.get(e["destination_id"])
        for o in data["route_options"]:
            o.pop("id")
            o["headline_leg_id"] = leg_names.get(o["headline_leg_id"])
        return data

    assert normalised(copy) == normalised(original)
    assert active_total(repo, new_id).total_nzd == active_total(repo, tid).total_nzd


def test_invalid_import_changes_nothing(repo):
    tid = load_demo_trip(repo)
    data = json.loads(export_json(repo.load_bundle(tid)))
    data["legs"][0]["base_fare"] = "lots"
    data["legs"][1]["mode"] = "teleport"
    data["expenses"][0]["destination_id"] = 9999
    with pytest.raises(ImportValidationError) as exc:
        parse_json(json.dumps(data))
    assert len(exc.value.errors) == 3
    assert len(repo.list_trips()) == 1


@pytest.mark.parametrize("text", ["not json", "[]", '{"format": "other"}',
                                  '{"format": "trip-budget-explorer", "version": 99}'])
def test_import_rejects_wrong_files(text):
    with pytest.raises(ImportValidationError):
        parse_json(text)


def test_budget_csv_contains_lines_and_totals(repo):
    tid = load_demo_trip(repo)
    summary = active_total(repo, tid)
    rows = list(csv.reader(io.StringIO(budget_csv(summary))))
    assert rows[0][0] == "category"
    lines = [r for r in rows[1:] if r and r[0] in ("transport", "accommodation", "food", "activities", "misc")]
    assert len(lines) == len(summary.lines)
    assert sum(D(r[8]) for r in lines) == summary.total_nzd
    totals = {r[0]: r[8] for r in rows if r and r[0].startswith(("TOTAL", "Budget"))}
    assert totals == {"TOTAL (group NZD)": str(summary.total_nzd), "Budget (group NZD)": "6000.00"}
