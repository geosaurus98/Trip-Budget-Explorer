"""Budget screen: non-transport expenses and the full cost breakdown."""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from ..calculations import BudgetSummary, summarize
from ..daily import daily_breakdown
from ..history import accuracy_report
from ..sharing import settle_up
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

    expenses_tab, daily_tab, accuracy_tab, sharing_tab, breakdown_tab = st.tabs(
        ["Expenses", "By day", "Estimates vs current", "Who paid", "Full breakdown & CSV"])
    with sharing_tab:
        _who_paid(trip, legs, expenses, destinations)
    with daily_tab:
        _by_day(trip, legs, expenses, destinations, summary)
    with accuracy_tab:
        _accuracy(repo, trip, legs, expenses, destinations)
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


CATEGORY_ORDER = ["accommodation", "transport", "food", "activities", "misc"]


def _by_day(trip: Trip, legs, expenses, destinations, summary: BudgetSummary) -> None:
    st.caption(
        "Whole-group NZD per day. Accommodation and stop-linked food are spread over the stop's nights; food not "
        "linked to a stop is spread over every night of the trip; transport sits on its departure date (or the day "
        "you leave its origin stop); stop-linked activities and misc sit on the arrival day."
    )
    result = daily_breakdown(trip, legs, expenses, destinations)
    if not result.days:
        st.info("Add trip dates (Settings) or stop dates (Itinerary) to see spending by day.")
        return

    budget = trip.budget_nzd
    rows, running = [], result.undated_total  # undated costs count from day one
    for row in result.days:
        running += row.total
        record = {"Date": row.day, "Night at": row.stop}
        record.update({c: float(row.by_category.get(c, 0)) for c in CATEGORY_ORDER})
        record.update({"Day total": float(row.total), "Cumulative": float(running)})
        if budget > 0:
            record["Budget left"] = float(budget - running)
        rows.append(record)
    df = pd.DataFrame(rows)

    long = df.melt(id_vars=["Date"], value_vars=CATEGORY_ORDER, var_name="Category", value_name="NZD")
    bars = alt.Chart(long[long["NZD"] > 0]).mark_bar().encode(
        x=alt.X("Date:T", title=None),
        y=alt.Y("NZD:Q", title="Group NZD per day"),
        color=alt.Color("Category:N", sort=CATEGORY_ORDER),
        tooltip=["Date:T", "Category", alt.Tooltip("NZD:Q", format=",.2f")],
    )
    st.altair_chart(bars.properties(height=280), width="stretch")

    money = st.column_config.NumberColumn(format="%.2f")
    st.dataframe(df, hide_index=True, width="stretch",
                 column_config={c: money for c in [*CATEGORY_ORDER, "Day total", "Cumulative", "Budget left"]})
    if result.undated_items:
        st.caption(f"Not tied to a date (counted in the cumulative total from day one): "
                   f"{format_nzd(result.undated_total)} — " + ", ".join(result.undated_items))
    if budget > 0 and running > budget:
        over = next(r for r in rows if r["Cumulative"] > budget)
        st.warning(f"On these figures the group budget runs out on {over['Date']:%d %b %Y}.")


def _accuracy(repo: Repository, trip: Trip, legs, expenses, destinations) -> None:
    st.caption(
        "Each item's **first recorded** cost versus its cost now. History is recorded automatically whenever a "
        "price, quantity, currency, basis or booking status changes. Both figures use today's exchange rates, so "
        "this shows price changes, not currency movements."
    )
    report = accuracy_report(trip, legs, expenses, destinations, repo.first_snapshots(trip.id))
    if not report.rows:
        st.info("No priced items yet.")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Booked items: first estimate", format_nzd(report.booked_first_nzd))
    pct = report.booked_change_pct
    c2.metric("Booked items: now", format_nzd(report.booked_current_nzd),
              delta=None if pct is None else f"{pct:+}%", delta_color="inverse")
    if report.projected_extra_nzd is None:
        c3.metric("Not yet booked", format_nzd(report.not_booked_current_nzd))
        st.caption("Once you mark items as booked, the app compares them with your first estimates and projects "
                   "the effect on everything still unbooked.")
    else:
        c3.metric("Not yet booked (projected)", format_nzd(report.not_booked_current_nzd + report.projected_extra_nzd),
                  delta=format_nzd(report.projected_extra_nzd, signed=True), delta_color="inverse",
                  help=f"If the unbooked {format_nzd(report.not_booked_current_nzd)} drifts by the same {pct}% as "
                       "your bookings did.")

    rows = sorted(report.rows, key=lambda r: -abs(r.change_nzd))  # biggest changes first
    df = pd.DataFrame([{
        "Item": r.description, "Category": r.category, "First status": r.first_status, "Now": r.current_status,
        "First NZD": float(r.first_nzd), "Current NZD": float(r.current_nzd), "Change NZD": float(r.change_nzd),
        "Change %": None if r.change_pct is None else float(r.change_pct),
        "First recorded": _when(r.first_recorded_at),
    } for r in rows])
    money = st.column_config.NumberColumn(format="%.2f")
    st.dataframe(df, hide_index=True, width="stretch",
                 column_config={"First NZD": money, "Current NZD": money, "Change NZD": money,
                                "Change %": st.column_config.NumberColumn(format="%+.1f%%")})

    names = {(r.record_type, r.record_id): r.description for r in rows}
    key = st.selectbox("Show the full history of", list(names), format_func=lambda k: names[k],
                       key=f"history_pick_{trip.id}")
    history = repo.cost_history(*key)
    if history:
        st.dataframe(pd.DataFrame([{
            "Recorded": _when(h.recorded_at), "Status": h.status, "Amount": f"{h.currency} {h.amount:,.2f}",
            "Quantity": f"{h.quantity.normalize():f}", "Basis": BASIS_LABELS[h.basis],
        } for h in history]), hide_index=True, width="stretch")
    else:
        st.caption("No history recorded for this item yet (it was created before history tracking existed).")


def _who_paid(trip: Trip, legs, expenses, destinations) -> None:
    st.caption(
        "Record who paid for each leg or expense (the **Paid by** field in its form). Everything paid is shared "
        "**equally** between the named travellers; amounts use the trip's current exchange rates."
    )
    if not trip.traveller_names:
        st.info("Add traveller names under **Settings → Trip settings** to start tracking who paid.")
        return
    result = settle_up(trip, legs, expenses, destinations)
    if result.paid_total == 0:
        st.info("Nothing is marked as paid yet. Choose **Paid by** when editing a leg or expense.")
        return

    st.dataframe(pd.DataFrame([{
        "Traveller": b.name, "Paid NZD": float(b.paid), "Fair share NZD": float(b.share),
        "Balance NZD": float(b.net),
        "": "is owed" if b.net > 0 else ("owes" if b.net < 0 else "settled"),
    } for b in result.balances]), hide_index=True, width="stretch",
        column_config={c: st.column_config.NumberColumn(format="%.2f")
                       for c in ("Paid NZD", "Fair share NZD", "Balance NZD")})

    st.markdown("**To settle up**")
    if result.transfers:
        for t in result.transfers:
            st.write(f"• **{t.payer}** pays **{t.payee}** {format_nzd(t.amount)}")
    else:
        st.write("Everyone is square.")
    st.caption(f"Paid so far and shared equally: {format_nzd(result.paid_total)}.")
    if result.unknown_payer:
        st.warning("Paid by someone not in the traveller list (not included): "
                   + "; ".join(f"{line.description} — {name}" for line, name in result.unknown_payer))
    if result.not_paid:
        with st.expander(f"{len(result.not_paid)} item(s) not marked as paid"):
            for line in result.not_paid:
                st.write(f"• {line.description}: {format_nzd(line.total_nzd)}")


def _when(iso_timestamp: str) -> str:
    return iso_timestamp.replace("T", " ")[:16]


def _describe(e: Expense) -> str:
    return f"[{e.category}] {e.description}"
