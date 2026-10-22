"""Budget screen: non-transport expenses and the full cost breakdown."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ..calculations import summarize
from ..models import Expense, Trip
from ..money import format_nzd
from ..storage import Repository
from ..transfer import budget_csv
from .common import BASIS_LABELS, confirm_delete, expense_form, expenses_table, pick_record, safe_filename, saved


def render(repo: Repository, trip: Trip) -> None:
    destinations = repo.list_destinations(trip.id)
    legs = repo.list_legs(trip.id)
    expenses = repo.list_expenses(trip.id)
    summary = summarize(trip, legs, expenses, destinations)

    st.header("Budget")
    c1, c2, c3 = st.columns(3)
    c1.metric("Total (whole group)", format_nzd(summary.total_nzd))
    c2.metric(f"Per traveller (÷ {trip.travellers})", format_nzd(summary.per_traveller_nzd))
    c3.metric("Remaining (group)" if summary.remaining_nzd >= 0 else "Overspend (group)",
              format_nzd(abs(summary.remaining_nzd)))

    expenses_tab, breakdown_tab = st.tabs(["Expenses", "Full breakdown & CSV"])
    with expenses_tab:
        st.caption("Accommodation, food, activities and miscellaneous costs. Transport costs live on the "
                   "**Itinerary → Transport legs** tab.")
        if expenses:
            st.dataframe(expenses_table(expenses, trip, destinations), hide_index=True, width="stretch")
        else:
            st.info("No expenses yet — add accommodation, food or activities below.")

        st.subheader("Add or edit an expense")
        expense = pick_record("Expense", expenses, _describe, key=f"exp_pick_{trip.id}",
                              new_label="➕ Add a new expense")
        expense_form(repo, trip, expense, option_id=None, destinations=destinations, key="active_exp")
        if expense is not None and confirm_delete(f"the expense '{expense.description}'", key=f"del_exp_{expense.id}"):
            repo.delete_expense(expense.id)
            saved(f"Deleted expense '{expense.description}'.")

    with breakdown_tab:
        st.caption("Every active cost line converted to NZD for the whole group. Each line is rounded to cents; "
                   "the total is the sum of the rounded lines.")
        if summary.lines:
            df = pd.DataFrame([{
                "Category": l.category, "Description": l.description, "Stop": l.destination,
                "Unit amount": f"{l.currency} {l.unit_amount:,.2f}", "Qty": f"{l.quantity.normalize():f}",
                "Basis": BASIS_LABELS[l.basis], "Status": l.status,
                "Group NZD": None if l.total_nzd is None else float(l.total_nzd), "Problem": l.problem,
            } for l in summary.lines])
            st.dataframe(df, hide_index=True, width="stretch",
                         column_config={"Group NZD": st.column_config.NumberColumn(format="%.2f")})
        else:
            st.info("Nothing to break down yet.")
        st.download_button(
            "⬇ Download budget breakdown (CSV)", budget_csv(summary), file_name=safe_filename(trip, "budget.csv"),
            mime="text/csv",
        )


def _describe(e: Expense) -> str:
    return f"[{e.category}] {e.description}"
