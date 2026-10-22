"""Trip Route & Budget Explorer — Streamlit entry point.

Run with:  streamlit run app.py

This file only wires up navigation. Screens live in trip_explorer/ui/,
calculations in trip_explorer/calculations.py and comparison.py, and
database access in trip_explorer/storage.py.
"""

from __future__ import annotations

import streamlit as st

from trip_explorer.demo import ensure_first_setup
from trip_explorer.models import Trip
from trip_explorer.storage import Repository, default_db_path
from trip_explorer.ui import budget, compare, itinerary, overview, settings, trips
from trip_explorer.ui.common import saved, select_trip, show_flash

st.set_page_config(page_title="Trip Route & Budget Explorer", page_icon="🧭", layout="wide")

PAGES = {
    "Overview": overview.render,
    "Itinerary": itinerary.render,
    "Budget": budget.render,
    "Compare routes": compare.render,
    "Compare trips": trips.render,
    "Settings & import/export": settings.render,
}


@st.cache_resource
def get_repository() -> Repository:
    repo = Repository(default_db_path())
    ensure_first_setup(repo)  # seeds the demo only on the very first run
    return repo


def sidebar(repo: Repository) -> tuple[Trip | None, str]:
    st.sidebar.title("🧭 Trip Explorer")
    st.sidebar.caption("Is the cheaper route worth the extra time?")
    trips = repo.list_trips()
    trip = None
    if trips:
        by_id = {t.id: t for t in trips}
        if "_next_trip_id" in st.session_state:
            st.session_state["trip_id"] = st.session_state.pop("_next_trip_id")
        if st.session_state.get("trip_id") not in by_id:
            st.session_state["trip_id"] = trips[0].id
        trip_id = st.sidebar.selectbox("Trip", list(by_id), format_func=lambda i: by_id[i].name, key="trip_id")
        trip = by_id[trip_id]

    with st.sidebar.expander("➕ New trip", expanded=not trips):
        with st.form("new_trip", clear_on_submit=True):
            name = st.text_input("Trip name")
            travellers = st.number_input("Travellers", min_value=1, value=1, step=1)
            if st.form_submit_button("Create trip", type="primary"):
                if name.strip():
                    select_trip(repo.create_trip(Trip(name=name.strip(), travellers=int(travellers))))
                    saved(f"Created trip '{name.strip()}'. Set dates, budget and exchange rates in Settings.")
                else:
                    st.error("Enter a trip name.")

    page = st.sidebar.radio("Section", list(PAGES), key="page")
    if trip is not None and trip.is_demo:
        st.sidebar.info("Demo trip: fictional, illustrative figures only.")
    return trip, page


def main() -> None:
    repo = get_repository()
    trip, page = sidebar(repo)
    show_flash()
    if trip is not None and trip.is_demo:
        st.caption("🧪 **Fictional demo trip.** Dates, prices, durations and exchange rates are illustrative "
                   "placeholders — not current or verified travel information.")
    if trip is None and page not in ("Settings & import/export", "Compare trips"):
        st.title("Trip Route & Budget Explorer")
        st.info("No trips yet. Create one in the sidebar, or load the demo trip under **Settings & import/export**.")
        return
    PAGES[page](repo, trip)


main()
