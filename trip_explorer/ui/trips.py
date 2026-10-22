"""Compare whole trips side by side (active itineraries only)."""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from ..calculations import TRANSPORT_CATEGORY, trip_stats
from ..models import EXPENSE_CATEGORIES, Trip
from ..money import format_nzd
from ..storage import Repository
from .common import fmt_hours

CATEGORIES = [TRANSPORT_CATEGORY, *EXPENSE_CATEGORIES]


def render(repo: Repository, trip: Trip | None) -> None:
    st.header("Compare trips")
    st.caption("Compare whole trips, e.g. two versions of the same holiday. Figures use each trip's own active "
               "itinerary, exchange rates and number of travellers; route options are not included.")
    trips = repo.list_trips()
    if len(trips) < 2:
        st.info("You need at least two trips. Create one in the sidebar, or import a trip in Settings. Tip: export "
                "a trip to JSON and import it again to get a copy you can modify.")
        return

    by_id = {t.id: t for t in trips}
    default = [trip.id] if trip is not None else []
    default += [t.id for t in trips if t.id not in default][: 2 - len(default)]
    chosen = st.multiselect("Trips to compare", list(by_id), default=default,
                            format_func=lambda i: by_id[i].name, key="compare_trips")
    if len(chosen) < 2:
        st.info("Choose at least two trips.")
        return

    stats = {
        tid: trip_stats(by_id[tid], repo.list_legs(tid), repo.list_expenses(tid), repo.list_destinations(tid))
        for tid in chosen
    }
    names = {tid: by_id[tid].name for tid in chosen}

    def row(label, fn):
        return {"": label, **{names[tid]: fn(by_id[tid], stats[tid]) for tid in chosen}}

    rows = [
        row("Travellers", lambda t, s: str(t.travellers)),
        row("Dates", lambda t, s: f"{t.start_date:%d %b %Y} – {t.end_date:%d %b %Y}"
            if t.start_date and t.end_date else "—"),
        row("Nights", lambda t, s: "—" if s.nights is None else str(s.nights)),
        row("Stops", lambda t, s: str(s.stops)),
        row("Total cost (group)", lambda t, s: format_nzd(s.summary.total_nzd)),
        row("Cost per traveller", lambda t, s: format_nzd(s.summary.per_traveller_nzd)),
        row("Cost per person per night", lambda t, s: format_nzd(s.per_person_per_night_nzd)),
        row("Budget (group)", lambda t, s: format_nzd(s.summary.budget_nzd)),
        row("Remaining (− = overspend)", lambda t, s: format_nzd(s.summary.remaining_nzd, signed=True)),
        row("Transport time", lambda t, s: fmt_hours(s.summary.transport_hours)),
        row("Booked so far (group)", lambda t, s: format_nzd(s.summary.by_status.get("booked"))),
    ]
    rows += [row(f"  {c} (group)", lambda t, s, c=c: format_nzd(s.summary.by_category.get(c)))
             for c in CATEGORIES]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    unpriced = [names[tid] for tid in chosen if stats[tid].summary.unpriced_lines]
    if unpriced:
        st.warning("Some costs are missing an exchange rate and are not included for: " + ", ".join(unpriced))
    if len({by_id[tid].travellers for tid in chosen}) > 1:
        st.caption("These trips have different numbers of travellers; compare the per-person figures.")

    per_person = pd.DataFrame([
        {"Trip": names[tid], "Category": c,
         "NZD per traveller": float(stats[tid].summary.by_category.get(c, 0) / by_id[tid].travellers)}
        for tid in chosen for c in CATEGORIES
    ])
    chart = alt.Chart(per_person).mark_bar().encode(
        y=alt.Y("Trip:N", title=None),
        x=alt.X("sum(NZD per traveller):Q", title="NZD per traveller"),
        color=alt.Color("Category:N", sort=CATEGORIES),
        order=alt.Order("Category:N"),
        tooltip=["Trip", "Category", alt.Tooltip("NZD per traveller:Q", format=",.2f")],
    )
    st.altair_chart(chart.properties(height=60 + 40 * len(chosen)), width="stretch")
