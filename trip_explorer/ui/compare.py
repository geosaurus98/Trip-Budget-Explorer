"""Compare two route options and optionally apply one to the active itinerary."""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
import streamlit as st

from ..comparison import Comparison, OptionSummary, compare_options, summarize_option
from ..models import RouteOption, TransportLeg, Trip
from ..money import format_nzd
from ..storage import Repository
from .common import (
    confirm_delete,
    expense_form,
    expenses_table,
    fmt_hours,
    leg_form,
    legs_table,
    pick_record,
    saved,
)


def render(repo: Repository, trip: Trip) -> None:
    st.header("Compare routes")
    st.caption(
        "Build two alternatives between the same start and end points — each with its own transport legs and "
        "associated expenses (extra hotel nights, meals…). Options are **kept separate from the active itinerary "
        "totals** until you explicitly apply one. All money is **whole-group NZD** unless marked per person."
    )

    options = repo.list_options(trip.id)
    with st.expander("➕ Create a route option", expanded=not options):
        _option_form(repo, trip, None)

    if len(options) < 2:
        st.info("Create at least two route options to compare them.")
        return

    by_id = {o.id: o for o in options}
    ids = list(by_id)
    c1, c2 = st.columns(2)
    a_id = c1.selectbox("Option A", ids, index=0, format_func=lambda i: by_id[i].name, key=f"cmp_a_{trip.id}")
    b_id = c2.selectbox("Option B", ids, index=1, format_func=lambda i: by_id[i].name, key=f"cmp_b_{trip.id}")
    if a_id == b_id:
        st.warning("Choose two different options.")
        return

    destinations = repo.list_destinations(trip.id)
    summaries = {
        oid: summarize_option(trip, by_id[oid], repo.list_legs(trip.id, oid), repo.list_expenses(trip.id, oid),
                              destinations)
        for oid in (a_id, b_id)
    }
    comparison = compare_options(summaries[a_id], summaries[b_id], trip.travellers)
    _results(comparison, trip)

    st.divider()
    st.subheader("Edit options")
    tab_a, tab_b = st.tabs([f"A: {by_id[a_id].name}", f"B: {by_id[b_id].name}"])
    with tab_a:
        _edit_option(repo, trip, summaries[a_id], "A")
    with tab_b:
        _edit_option(repo, trip, summaries[b_id], "B")

    st.divider()
    _apply_section(repo, trip, [by_id[a_id], by_id[b_id]])


# --------------------------------------------------------------------------- results


def _results(c: Comparison, trip: Trip) -> None:
    for w in c.warnings:
        st.warning(w)
    for label, s in (("A", c.a), ("B", c.b)):
        for w in s.warnings:
            st.warning(f"Option {label}: {w}")

    st.subheader("Result")
    if c.cheaper and c.faster and c.cheaper != c.faster:
        st.info(f"**{c.verdict}**")
    else:
        st.success(f"**{c.verdict}**")

    def money(v: Decimal | None) -> str:
        return format_nzd(v)

    def pp(v: Decimal) -> str:
        return format_nzd(c.per_person(v))

    rows = [
        ("Total cost (group)", money(c.a.total_nzd), money(c.b.total_nzd), format_nzd(c.cost_difference_nzd, True)),
        ("Total cost (per person)", money(c.a.per_person_nzd), money(c.b.per_person_nzd),
         format_nzd(c.per_person(c.cost_difference_nzd), True)),
        ("Transport legs (group)", money(c.a.transport_nzd), money(c.b.transport_nzd),
         format_nzd(c.b.transport_nzd - c.a.transport_nzd, True)),
        ("Extra accommodation (group)", money(c.a.accommodation_nzd), money(c.b.accommodation_nzd),
         format_nzd(c.b.accommodation_nzd - c.a.accommodation_nzd, True)),
        ("Other associated expenses (group)", money(c.a.other_expenses_nzd), money(c.b.other_expenses_nzd),
         format_nzd(c.b.other_expenses_nzd - c.a.other_expenses_nzd, True)),
        ("Door-to-door travel time", fmt_hours(c.a.travel_hours), fmt_hours(c.b.travel_hours),
         ("+" if c.time_difference_hours > 0 else "") + fmt_hours(c.time_difference_hours)),
        ("Transport legs / transfers", f"{len(c.a.legs)} / {c.a.transfers}", f"{len(c.b.legs)} / {c.b.transfers}",
         f"{c.b.transfers - c.a.transfers:+d} transfers"),
        ("Headline fare (group)", money(c.a.headline_fare_nzd), money(c.b.headline_fare_nzd),
         format_nzd(c.b.headline_fare_nzd - c.a.headline_fare_nzd, True)),
    ]
    st.dataframe(pd.DataFrame(rows, columns=["", "Option A", "Option B", "Difference (B − A)"]),
                 hide_index=True, width="stretch")
    st.caption("Transfers = changes between transport legs (number of legs − 1). Travel time is the sum of the "
               "entered door-to-door hours.")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Savings per additional travel hour**")
        if c.savings_per_extra_hour_nzd is not None:
            st.metric(f"Option {c.cheaper} saves, per extra hour (group)", format_nzd(c.savings_per_extra_hour_nzd))
            st.caption(f"= {format_nzd(abs(c.cost_difference_nzd))} saved ÷ {fmt_hours(abs(c.time_difference_hours))} "
                       f"extra. Per person: {pp(c.savings_per_extra_hour_nzd)} per hour.")
        elif c.cheaper is None and c.faster is None:
            st.write("Not applicable — same cost and same time.")
        elif c.faster is None:
            st.write("Not applicable — both take the same time, so there is no extra time to trade for the saving.")
        elif c.cheaper is None:
            st.write("Not applicable — same cost, so there is no saving to trade for time.")
        else:
            st.write(f"Not applicable — option {c.cheaper} is both cheaper and faster.")
    with c2:
        _break_even(c)


def _break_even(c: Comparison) -> None:
    be = c.break_even
    st.markdown("**Break-even: maximum extra repositioning spend**")
    if c.a.headline_leg is None or c.b.headline_leg is None:
        st.write("Add priced transport legs to both options to calculate the break-even allowance.")
        return
    if be.cheap_fare_label is None:
        st.write("Both headline fares are equal, so there is no fare saving to spend. "
                 f"The options differ by {format_nzd(abs(c.cost_difference_nzd))} in other costs.")
        return
    cheap = c.a if be.cheap_fare_label == "A" else c.b
    other = c.b if be.cheap_fare_label == "A" else c.a
    other_label = "B" if be.cheap_fare_label == "A" else "A"
    st.write(
        f"Option {be.cheap_fare_label} has the cheaper headline fare "
        f"(*{cheap.headline_leg.origin} → {cheap.headline_leg.destination}*) vs option {other_label} "
        f"(*{other.headline_leg.origin} → {other.headline_leg.destination}*)."
    )
    df = pd.DataFrame([
        ("Initial fare-saving allowance", format_nzd(be.initial_allowance_nzd),
         format_nzd(c.per_person(be.initial_allowance_nzd))),
        (f"Extra costs entered for option {be.cheap_fare_label}", format_nzd(be.extra_costs_nzd),
         format_nzd(c.per_person(be.extra_costs_nzd))),
        ("Remaining allowance" if be.saving_survives else "Excess over the fare saving",
         format_nzd(be.remaining_allowance_nzd if be.saving_survives else be.excess_nzd),
         format_nzd(c.per_person(be.remaining_allowance_nzd if be.saving_survives else be.excess_nzd))),
    ], columns=["", "Group", "Per person"])
    st.dataframe(df, hide_index=True, width="stretch")
    if be.saving_survives:
        st.success(f"You could spend up to {format_nzd(be.remaining_allowance_nzd)} more (group) on getting to/from "
                   f"option {be.cheap_fare_label}'s cheaper fare before it stops being cheaper.")
    elif be.remaining_allowance_nzd == 0:
        st.warning("Extra costs exactly cancel the fare saving — the options cost the same.")
    else:
        st.error(f"The extra costs already exceed the fare saving by {format_nzd(be.excess_nzd)} (group): "
                 f"option {be.cheap_fare_label}'s cheaper fare does not save money overall.")
    with st.expander("How is this calculated?"):
        st.markdown(
            "- **Headline fare** = base fare of each option's main leg (choose it under *Edit options*; "
            "default is the leg with the largest base fare). Group NZD.\n"
            "- **Initial allowance** = other option's headline fare − cheaper headline fare.\n"
            "- **Extra costs** = everything else in the cheaper-fare option (other legs, baggage, transfers, "
            "extra hotel nights, meals) − the same for the other option. Negative means it is cheaper on those too.\n"
            "- **Remaining allowance** = initial allowance − extra costs = total of other option − total of "
            "cheaper-fare option. If negative, the excess is shown instead."
        )


# --------------------------------------------------------------------------- editing


def _option_form(repo: Repository, trip: Trip, option: RouteOption | None, legs: list[TransportLeg] = ()) -> None:
    is_new = option is None
    option = option or RouteOption(trip_id=trip.id, name="", origin="", destination="")
    k = f"opt_{option.id or 'new'}"
    with st.form(k, clear_on_submit=is_new):
        name = st.text_input("Option name *", option.name, key=f"{k}_name")
        c1, c2 = st.columns(2)
        origin = c1.text_input("Starts at *", option.origin, key=f"{k}_o")
        destination = c2.text_input("Ends at *", option.destination, key=f"{k}_d")
        headline = None
        if not is_new:
            leg_ids = [0] + [l.id for l in legs]
            names = {l.id: f"{l.origin} → {l.destination} ({l.mode})" for l in legs}
            headline = st.selectbox(
                "Headline fare leg (for break-even)", leg_ids,
                index=leg_ids.index(option.headline_leg_id) if option.headline_leg_id in leg_ids else 0,
                format_func=lambda i: "Auto: leg with the largest base fare" if i == 0 else names[i], key=f"{k}_hl",
            )
        notes = st.text_area("Notes", option.notes, height=68, key=f"{k}_notes")
        submitted = st.form_submit_button("Create option" if is_new else "Save option", type="primary")
    if submitted:
        if not (name.strip() and origin.strip() and destination.strip()):
            st.error("Name, start and end are required.")
            return
        updated = RouteOption(id=option.id, trip_id=trip.id, name=name.strip(), origin=origin.strip(),
                              destination=destination.strip(), notes=notes, headline_leg_id=headline or None,
                              applied_at=option.applied_at)
        if is_new:
            repo.add_option(updated)
            saved(f"Created option '{updated.name}'.")
        else:
            repo.update_option(updated)
            saved(f"Saved option '{updated.name}'.")


def _edit_option(repo: Repository, trip: Trip, summary: OptionSummary, label: str) -> None:
    option = summary.option
    destinations = repo.list_destinations(trip.id)
    if option.applied_at:
        st.caption(f"Applied to the itinerary on {option.applied_at}.")
    with st.expander("Option details", expanded=False):
        _option_form(repo, trip, option, summary.legs)
        if confirm_delete(f"option '{option.name}' and its legs/expenses", key=f"del_opt_{option.id}"):
            repo.delete_option(option.id)
            saved(f"Deleted option '{option.name}'.")

    st.markdown("**Transport legs in this option**")
    if summary.legs:
        st.dataframe(legs_table(summary.legs, trip), hide_index=True, width="stretch")
    else:
        st.info("No legs yet.")
    leg = pick_record("Leg", summary.legs, lambda l: f"{l.origin} → {l.destination} ({l.mode})",
                      key=f"optleg_pick_{option.id}", new_label="➕ Add a leg to this option")
    default_origin = summary.legs[-1].destination if summary.legs else option.origin
    leg_form(repo, trip, leg, option_id=option.id, key=f"optleg_{option.id}",
             default_origin=default_origin, default_destination=option.destination)
    if leg is not None:
        c1, c2, _ = st.columns([1, 1, 4])
        if c1.button("↑ Move up", key=f"optlegup_{leg.id}"):
            repo.move_leg(leg.id, -1)
            st.rerun()
        if c2.button("↓ Move down", key=f"optlegdown_{leg.id}"):
            repo.move_leg(leg.id, +1)
            st.rerun()
        if confirm_delete(f"the leg {leg.origin} → {leg.destination}", key=f"del_optleg_{leg.id}"):
            repo.delete_leg(leg.id)
            saved("Deleted leg.")

    st.markdown("**Associated expenses in this option** (extra hotel nights, meals, etc.)")
    if summary.expenses:
        st.dataframe(expenses_table(summary.expenses, trip, destinations), hide_index=True, width="stretch")
    else:
        st.info("No associated expenses.")
    expense = pick_record("Expense", summary.expenses, lambda e: f"[{e.category}] {e.description}",
                          key=f"optexp_pick_{option.id}", new_label="➕ Add an expense to this option")
    expense_form(repo, trip, expense, option_id=option.id, destinations=destinations, key=f"optexp_{option.id}")
    if expense is not None and confirm_delete(f"the expense '{expense.description}'", key=f"del_optexp_{expense.id}"):
        repo.delete_expense(expense.id)
        saved("Deleted expense.")


# --------------------------------------------------------------------------- apply


def _suggest_replacements(option: RouteOption, active_legs: list[TransportLeg]) -> list[int]:
    """Active legs forming the chain from the option's start to its end, if one exists."""
    norm = lambda s: s.strip().lower()  # noqa: E731
    for start, leg in enumerate(active_legs):
        if norm(leg.origin) == norm(option.origin):
            for end in range(start, len(active_legs)):
                if norm(active_legs[end].destination) == norm(option.destination):
                    return [l.id for l in active_legs[start:end + 1]]
    return []


def _apply_section(repo: Repository, trip: Trip, options: list[RouteOption]) -> None:
    st.subheader("Apply an option to the active itinerary")
    st.caption(
        "Applying **copies** the option's legs and expenses into the active itinerary and **moves** the items you "
        "select below into a new 'Previous plan' option, so nothing is counted twice and nothing is lost. "
        "Stops (destinations) are not changed — delete or edit them on the Itinerary screen if needed."
    )
    by_id = {o.id: o for o in options}
    option_id = st.radio("Option to apply", list(by_id), format_func=lambda i: by_id[i].name, horizontal=True,
                         key=f"apply_pick_{trip.id}")
    option = by_id[option_id]
    active_legs = repo.list_legs(trip.id)
    active_expenses = repo.list_expenses(trip.id)
    destinations = repo.list_destinations(trip.id)

    suggested_legs = _suggest_replacements(option, active_legs)
    via = {l.destination.strip().lower() for l in active_legs if l.id in suggested_legs[:-1]}
    suggested_exp = [e.id for e in active_expenses
                     if any(d.id == e.destination_id and d.name.strip().lower() in via for d in destinations)]

    leg_names = {l.id: f"{l.origin} → {l.destination} ({l.mode})" for l in active_legs}
    exp_names = {e.id: f"[{e.category}] {e.description}" for e in active_expenses}
    # Keyed on the current active plan so suggestions refresh after it changes.
    plan_signature = "_".join(str(i) for i in [*leg_names, *exp_names])
    k = f"apply_{option.id}_{abs(hash(plan_signature))}"
    replace_legs = st.multiselect("Active legs this option replaces", list(leg_names), default=suggested_legs,
                                  format_func=leg_names.get, key=f"{k}_legs")
    replace_exps = st.multiselect("Active expenses this option replaces", list(exp_names), default=suggested_exp,
                                  format_func=exp_names.get, key=f"{k}_exps",
                                  help="E.g. the hotel at a stop that the new route skips.")
    if option.applied_at:
        st.warning(f"This option was already applied on {option.applied_at}. Applying it again adds another copy "
                   "unless you select the previously applied legs/expenses to replace.")
    if not replace_legs and not replace_exps:
        st.warning("Nothing is selected to replace — the option will be **added** on top of the current plan.")
    confirmed = st.checkbox("I understand this changes the active itinerary totals", key=f"{k}_confirm")
    if st.button(f"Apply '{option.name}'", type="primary", disabled=not confirmed, key=f"{k}_btn"):
        repo.apply_option(option.id, replace_legs, replace_exps)
        saved(f"Applied '{option.name}'. Replaced items were moved to a 'Previous plan' option.")
