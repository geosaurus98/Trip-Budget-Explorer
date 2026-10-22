"""Itinerary screen: stops (destinations) and active transport legs."""

from __future__ import annotations

import streamlit as st

from ..calculations import (
    itinerary_issues,
    legs_in_stop_order,
    missing_connections,
    stop_nights,
    unmatched_leg_places,
)
from ..geo import parse_lat_lon
from ..models import Destination, TransportLeg, Trip
from ..storage import Repository
from .common import confirm_delete, flash, leg_form, legs_table, parse_coordinate, pick_record, saved
from .overview import itinerary_dataframe


def render(repo: Repository, trip: Trip) -> None:
    st.header("Itinerary")
    stops_tab, legs_tab = st.tabs(["Stops", "Transport legs"])
    with stops_tab:
        _stops(repo, trip)
    with legs_tab:
        _legs(repo, trip)


def _describe_stop(number: int, d: Destination) -> str:
    nights = stop_nights(d)
    return f"{number}. {d.name}" + (f" ({nights} nights)" if nights is not None else "")


def _stops(repo: Repository, trip: Trip) -> None:
    destinations = repo.list_destinations(trip.id)
    order = {d.id: i for i, d in enumerate(destinations)}

    issues = [i for i in itinerary_issues(trip, destinations) if "coordinates" not in i]
    for issue in issues:
        st.warning(issue)

    if destinations:
        st.dataframe(itinerary_dataframe(destinations), hide_index=True, width="stretch")
    else:
        st.info("No stops yet — add your first destination below.")

    st.subheader("Add or edit a stop")
    dest = pick_record("Stop", destinations, lambda d: _describe_stop(order[d.id] + 1, d), key=f"stop_pick_{trip.id}", new_label="➕ Add a new stop")

    if dest is not None:
        c1, c2, _ = st.columns([1, 1, 4])
        if c1.button("↑ Move up", disabled=order[dest.id] == 0, key=f"up_{dest.id}"):
            repo.move_destination(dest.id, -1)
            st.rerun()
        if c2.button("↓ Move down", disabled=order[dest.id] == len(destinations) - 1, key=f"down_{dest.id}"):
            repo.move_destination(dest.id, +1)
            st.rerun()

    is_new = dest is None
    dest = dest or Destination(trip_id=trip.id, name="")
    k = f"stop_{dest.id or 'new'}"
    with st.form(k, clear_on_submit=is_new):
        c1, c2 = st.columns(2)
        name = c1.text_input("Place name *", dest.name, key=f"{k}_name")
        country = c2.text_input("Country", dest.country, key=f"{k}_country")
        c1, c2 = st.columns(2)
        arrival = c1.date_input("Arrival date", value=dest.arrival, key=f"{k}_arr", format="YYYY-MM-DD")
        departure = c2.date_input("Departure date", value=dest.departure, key=f"{k}_dep", format="YYYY-MM-DD")
        c1, c2 = st.columns(2)
        lat = c1.text_input("Latitude (optional)", "" if dest.latitude is None else str(dest.latitude),
                            placeholder="-8.5069", key=f"{k}_lat", help="Decimal degrees; south is negative.")
        lon = c2.text_input("Longitude (optional)", "" if dest.longitude is None else str(dest.longitude),
                            placeholder="115.2625", key=f"{k}_lon", help="Decimal degrees; west is negative.")
        pasted = st.text_input(
            "…or paste coordinates / a map link (fills in latitude and longitude)", key=f"{k}_paste",
            placeholder="-8.5069, 115.2625   or   8.5069° S, 115.2625° E   or   https://…/@-8.5069,115.2625,14z",
            help="In most map apps, long-press or right-click a place to copy its coordinates. Nothing is looked "
                 "up online.",
        )
        notes = st.text_area("Notes", dest.notes, height=68, key=f"{k}_notes")
        submitted = st.form_submit_button("Add stop" if is_new else "Save stop", type="primary")

    if submitted:
        if not name.strip():
            st.error("Please enter a place name.")
        else:
            try:
                if pasted.strip():
                    latitude, longitude = parse_lat_lon(pasted)
                else:
                    latitude = parse_coordinate(lat, 90, "Latitude")
                    longitude = parse_coordinate(lon, 180, "Longitude")
            except ValueError as exc:
                st.error(str(exc))
            else:
                updated = Destination(
                    id=dest.id, trip_id=trip.id, position=dest.position, name=name.strip(), country=country.strip(),
                    arrival=arrival, departure=departure, latitude=latitude, longitude=longitude, notes=notes,
                )
                if arrival and departure and departure < arrival:
                    # Saved anyway (drafts are allowed) but flagged.
                    flash(f"{updated.name}: departure is before arrival — saved as a draft.", "warning")
                if is_new:
                    repo.add_destination(updated)
                    saved(f"Added stop {updated.name}.")
                else:
                    repo.update_destination(updated)
                    saved(f"Saved stop {updated.name}.")

    if not is_new:
        st.caption("Deleting a stop keeps any expenses linked to it, but unlinks them.")
        if confirm_delete(f"the stop '{dest.name}'", key=f"del_stop_{dest.id}"):
            repo.delete_destination(dest.id)
            saved(f"Deleted stop {dest.name}.")


def _connection_checks(repo: Repository, trip: Trip, destinations: list[Destination],
                       legs: list[TransportLeg]) -> None:
    """Flag stops that no leg joins, and offer to create placeholder legs for them."""
    missing = missing_connections(destinations, legs)
    if missing:
        st.warning(
            f"{len(missing)} pair(s) of consecutive stops have no transport leg between them, so the trip total "
            "may be missing travel costs: " + "; ".join(f"{a.name} → {b.name}" for a, b in missing)
        )
        if st.button(f"Create {len(missing)} placeholder leg(s)", key=f"placeholders_{trip.id}",
                     help="Adds zero-cost 'estimated' legs in the right order for you to fill in."):
            for a, b in missing:
                repo.add_leg(TransportLeg(
                    trip_id=trip.id, origin=a.name, destination=b.name, depart_local=_date_text(a.departure),
                    notes="Placeholder created from stops — enter mode, duration and costs.",
                ))
            ordered = legs_in_stop_order(destinations, repo.list_legs(trip.id))
            repo.set_leg_order(trip.id, [leg.id for leg in ordered])
            saved(f"Created {len(missing)} placeholder leg(s).")
    elif destinations and legs:
        st.caption("✓ Every pair of consecutive stops is joined by at least one leg.")

    unmatched = unmatched_leg_places(destinations, legs)
    if unmatched:
        st.caption("Leg places that don't match any stop name (fine for airports or stations, otherwise check for "
                   "typos): " + ", ".join(unmatched))
    ordered = legs_in_stop_order(destinations, legs)
    if [leg.id for leg in ordered] != [leg.id for leg in legs]:
        st.info("The legs are not in the same order as your stops.")
        if st.button("Sort legs by stop order", key=f"sort_legs_{trip.id}"):
            repo.set_leg_order(trip.id, [leg.id for leg in ordered])
            saved("Sorted legs into stop order.")


def _date_text(value) -> str:
    return value.isoformat() if value else ""


def _legs(repo: Repository, trip: Trip) -> None:
    legs = repo.list_legs(trip.id)
    destinations = repo.list_destinations(trip.id)
    st.caption(
        "Active transport legs count towards the trip totals. Departure/arrival times are **local reference "
        "information only**; comparisons use the entered **door-to-door duration**."
    )
    if legs:
        st.dataframe(legs_table(legs, trip), hide_index=True, width="stretch")
    else:
        st.info("No transport legs yet — add one below.")
    _connection_checks(repo, trip, destinations, legs)

    st.subheader("Add or edit a leg")
    positions = {leg.id: i for i, leg in enumerate(legs)}
    leg = pick_record("Leg", legs, lambda l: f"{positions[l.id] + 1}. {l.origin} → {l.destination} ({l.mode})",
                      key=f"leg_pick_{trip.id}", new_label="➕ Add a new leg")
    if leg is not None:
        c1, c2, _ = st.columns([1, 1, 4])
        if c1.button("↑ Move up", disabled=positions[leg.id] == 0, key=f"legup_{leg.id}"):
            repo.move_leg(leg.id, -1)
            st.rerun()
        if c2.button("↓ Move down", disabled=positions[leg.id] == len(legs) - 1, key=f"legdown_{leg.id}"):
            repo.move_leg(leg.id, +1)
            st.rerun()

    # Sensible defaults for a new leg: continue from the last leg, towards the next stop.
    default_origin = legs[-1].destination if legs else (destinations[0].name if destinations else "")
    names = [d.name for d in destinations]
    default_destination = ""
    if default_origin in names and names.index(default_origin) + 1 < len(names):
        default_destination = names[names.index(default_origin) + 1]
    if destinations:
        st.caption("Stops: " + " → ".join(names))
    leg_form(repo, trip, leg, option_id=None, key="active_leg",
             default_origin=default_origin, default_destination=default_destination)

    if leg is not None and confirm_delete(f"the leg {leg.origin} → {leg.destination}", key=f"del_leg_{leg.id}"):
        repo.delete_leg(leg.id)
        saved(f"Deleted leg {leg.origin} → {leg.destination}.")
