"""Overview dashboard: totals, breakdown, itinerary table and route map."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from ..calculations import itinerary_issues, missing_connections, stop_nights, summarize
from ..deadlines import upcoming_deadlines
from ..mapping import build_route_map, mapped_stops
from ..models import Destination, Trip
from ..money import format_nzd
from ..storage import Repository
from .common import fmt_hours


def itinerary_dataframe(destinations: list[Destination]) -> pd.DataFrame:
    rows = []
    for i, d in enumerate(destinations, start=1):
        nights = stop_nights(d)
        rows.append({
            "#": i, "Place": d.name, "Country": d.country,
            "Arrive": d.arrival.isoformat() if d.arrival else "",
            "Depart": d.departure.isoformat() if d.departure else "",
            "Nights": nights,
            "Lat": d.latitude,
            "Lon": d.longitude,
            "Notes": d.notes,
        })
    return pd.DataFrame(rows)


URGENCY_ICON = {"overdue": "🔴", "this week": "🟠", "within 30 days": "🟡", "later": "⚪"}


def _deadlines(legs, expenses, todos) -> None:
    st.subheader("Deadlines")
    deadlines = upcoming_deadlines(legs, expenses, date.today(), todos)
    if not deadlines:
        st.caption("No open deadlines. Add 'Book by' or 'Free cancellation until' dates to legs and expenses, "
                   "or due dates to Checklist items.")
        return
    overdue = [d for d in deadlines if d.urgency == "overdue"]
    if overdue:
        st.error(f"{len(overdue)} item(s) are overdue (not booked or not done by their date).")
    st.dataframe(pd.DataFrame([{
        "": URGENCY_ICON[d.urgency], "Due": d.due, "Deadline": d.label, "Item": d.description,
        "Status": d.status,
        "When": f"{-d.days_left} days ago" if d.days_left < 0 else ("today" if d.days_left == 0
                                                                    else f"in {d.days_left} days"),
    } for d in deadlines]), hide_index=True, width="stretch")


def render(repo: Repository, trip: Trip) -> None:
    destinations = repo.list_destinations(trip.id)
    legs = repo.list_legs(trip.id)
    expenses = repo.list_expenses(trip.id)
    summary = summarize(trip, legs, expenses, destinations)

    st.header(f"Overview — {trip.name}")
    dates = ""
    if trip.start_date and trip.end_date:
        dates = f"{trip.start_date:%d %b %Y} – {trip.end_date:%d %b %Y} · {(trip.end_date - trip.start_date).days} nights · "
    st.caption(f"{dates}{trip.travellers} traveller(s). Totals cover the **active itinerary** only; "
               "route options on the Compare screen are not included until applied.")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total trip cost (whole group)", format_nzd(summary.total_nzd))
    c2.metric("Cost per traveller", format_nzd(summary.per_traveller_nzd))
    if summary.remaining_nzd >= 0:
        c3.metric("Budget remaining (group)", format_nzd(summary.remaining_nzd),
                  help=f"Group budget: {format_nzd(summary.budget_nzd)}")
    else:
        c3.metric("Over budget (group)", format_nzd(-summary.remaining_nzd), delta=format_nzd(summary.remaining_nzd),
                  help=f"Group budget: {format_nzd(summary.budget_nzd)}")
    c4.metric("Entered transport time", fmt_hours(summary.transport_hours),
              help="Sum of door-to-door hours of active transport legs.")

    if summary.budget_nzd > 0:
        used = float(summary.total_nzd / summary.budget_nzd)
        st.progress(min(used, 1.0), text=f"{used:.0%} of group budget {format_nzd(summary.budget_nzd)} used")
    else:
        st.info("No budget set yet — add one in **Settings**.")

    if summary.unpriced_lines:
        st.warning(
            f"{len(summary.unpriced_lines)} cost line(s) are **not included** in totals because an exchange rate "
            "is missing: " + "; ".join(f"{l.description} ({l.problem})" for l in summary.unpriced_lines)
        )
    issues = itinerary_issues(trip, destinations)
    issues += [f"No transport leg from {a.name} to {b.name} — travel costs may be missing."
               for a, b in missing_connections(destinations, legs)]
    if issues:
        with st.expander(f"⚠ {len(issues)} itinerary check(s)", expanded=False):
            for issue in issues:
                st.write("• " + issue)

    left, right = st.columns(2)
    with left:
        st.subheader("Cost by category (group, NZD)")
        if summary.by_category:
            df = pd.DataFrame(
                [{"Category": k, "Group NZD": float(v),
                  "Per traveller NZD": float(v / trip.travellers) if trip.travellers else 0.0}
                 for k, v in sorted(summary.by_category.items(), key=lambda kv: -kv[1])]
            )
            st.bar_chart(df, x="Category", y="Group NZD", horizontal=True, height=220)
            st.dataframe(df, hide_index=True, width="stretch",
                         column_config={"Group NZD": st.column_config.NumberColumn(format="%.2f"),
                                        "Per traveller NZD": st.column_config.NumberColumn(format="%.2f")})
        else:
            st.info("No costs yet. Add transport legs in **Itinerary** and expenses in **Budget**.")
    with right:
        st.subheader("Cost by booking status (group, NZD)")
        if summary.by_status:
            st.dataframe(
                pd.DataFrame([{"Status": k, "Group NZD": float(v)} for k, v in summary.by_status.items()]),
                hide_index=True, width="stretch",
                column_config={"Group NZD": st.column_config.NumberColumn(format="%.2f")},
            )
        st.caption("Shows how much of the total is still an estimate versus researched or booked.")

    _deadlines(legs, expenses, repo.list_todos(trip.id))

    st.subheader("Itinerary")
    if destinations:
        st.dataframe(itinerary_dataframe(destinations), hide_index=True, width="stretch")
    else:
        st.info("No stops yet. Add your first destination in **Itinerary**.")

    st.subheader("Route map")
    st.caption("Dashed straight lines join stops in itinerary order. They do **not** show actual roads, "
               "flight paths or transport routes.")
    fmap = build_route_map(destinations)
    missing = len(destinations) - len(mapped_stops(destinations))
    if fmap is None:
        st.info("No stops have coordinates yet. Add latitude/longitude to a stop in **Itinerary** to see the map.")
    else:
        st_folium(fmap, height=460, use_container_width=True, returned_objects=[], key=f"map_{trip.id}")
    if missing and fmap is not None:
        st.caption(f"{missing} stop(s) without coordinates are not shown on the map.")
