"""Trip checklist: pre-trip tasks with optional due dates (shown under Overview → Deadlines)."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from ..models import TodoItem, Trip
from ..storage import Repository
from .common import saved

# Generic reminders; check the actual requirements for your destinations and passport.
STANDARD_ITEMS = [
    "Check passport validity requirements for each country",
    "Check visa / entry requirements for each country",
    "Buy travel insurance",
    "Ask a travel doctor about recommended vaccinations",
    "Sort out cards and cash for travel (fees, notify bank if needed)",
    "Arrange phone data (SIM / eSIM / roaming)",
    "Save copies of passport, insurance and bookings",
    "Register travel plans on SafeTravel (NZ)",
]


def render(repo: Repository, trip: Trip) -> None:
    st.header("Checklist")
    st.caption("Things to sort out before you go. Tick items off, edit them in the table, add rows at the bottom, "
               "or select rows and press Delete — then **Save checklist**. Items with a due date appear under "
               "**Overview → Deadlines** until ticked.")
    todos = repo.list_todos(trip.id)

    df = pd.DataFrame(
        [{"id": t.id, "Done": t.done, "Task": t.title, "Due": t.due, "Notes": t.notes} for t in todos],
        columns=["id", "Done", "Task", "Due", "Notes"],
    )
    df["Due"] = pd.to_datetime(df["Due"]).dt.date  # keeps the date editor working on an empty table
    df["Done"] = df["Done"].astype(bool)
    edited = st.data_editor(
        df, num_rows="dynamic", hide_index=True, width="stretch", key=f"todos_{trip.id}",
        column_order=["Done", "Task", "Due", "Notes"],
        column_config={
            "Done": st.column_config.CheckboxColumn(default=False, width="small"),
            "Task": st.column_config.TextColumn(required=True, width="large"),
            "Due": st.column_config.DateColumn(format="YYYY-MM-DD"),
        },
    )

    c1, c2, _ = st.columns([1, 2, 3])
    if c1.button("Save checklist", type="primary", key=f"todos_save_{trip.id}"):
        items, skipped = rows_to_todos(edited, trip.id)
        repo.save_todos(trip.id, items)
        saved("Saved checklist." + (f" Skipped {skipped} row(s) without a task." if skipped else ""))
    if c2.button("Add common pre-trip items", key=f"todos_std_{trip.id}",
                 help="Adds generic reminders that aren't already on the list. Check the actual requirements "
                      "for your destinations and passport."):
        added = repo.add_missing_todos(trip.id, STANDARD_ITEMS)
        saved(f"Added {added} item(s)." if added else "All common items are already on the list.")

    if todos:
        done = sum(t.done for t in todos)
        st.progress(done / len(todos), text=f"{done} of {len(todos)} done")
    else:
        st.info("No checklist items yet — add your own rows above or start with the common pre-trip items.")


def rows_to_todos(df: pd.DataFrame, trip_id: int) -> tuple[list[TodoItem], int]:
    """Turn edited table rows into checklist items. Returns (items, rows skipped for having no task)."""
    items, skipped = [], 0
    for _, row in df.iterrows():
        title = "" if pd.isna(row["Task"]) else str(row["Task"]).strip()
        if not title:
            skipped += 1
            continue
        items.append(TodoItem(
            id=None if pd.isna(row["id"]) else int(row["id"]), trip_id=trip_id, title=title,
            due=_to_date(row["Due"]), done=False if pd.isna(row["Done"]) else bool(row["Done"]),
            notes="" if pd.isna(row["Notes"]) else str(row["Notes"]),
        ))
    return items, skipped


def _to_date(value) -> date | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])
