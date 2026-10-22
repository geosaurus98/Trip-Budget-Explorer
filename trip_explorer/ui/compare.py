"""Compare route options (all at once, then two in detail) and optionally apply one."""

from __future__ import annotations

from decimal import Decimal

import altair as alt
import pandas as pd
import streamlit as st

from ..comparison import (
    Comparison,
    OptionSummary,
    compare_options,
    generalised_cost_nzd,
    summarize_option,
)
from ..models import RouteOption, TransportLeg, Trip
from ..money import format_nzd
from ..scenarios import saving_margin, with_price_change, with_rate_change
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
        "Build alternatives between the same start and end points — each with its own transport legs and "
        "associated expenses (extra hotel nights, meals…). See every option side by side, then pick two to compare "
        "in detail. Options are **kept separate from the active itinerary totals** until you explicitly apply one. "
        "All money is **whole-group NZD** unless marked per person."
    )

    options = repo.list_options(trip.id)
    with st.expander("➕ Create a route option", expanded=not options):
        _option_form(repo, trip, None)

    if len(options) < 2:
        st.info("Create at least two route options to compare them.")
        return

    by_id = {o.id: o for o in options}
    ids = list(by_id)
    destinations = repo.list_destinations(trip.id)
    summaries = {
        oid: summarize_option(trip, by_id[oid], repo.list_legs(trip.id, oid), repo.list_expenses(trip.id, oid),
                              destinations)
        for oid in ids
    }
    _all_options(list(summaries.values()), trip)

    st.subheader("Compare two options in detail")
    c1, c2 = st.columns(2)
    a_id = c1.selectbox("Option A", ids, index=0, format_func=lambda i: by_id[i].name, key=f"cmp_a_{trip.id}")
    b_id = c2.selectbox("Option B", ids, index=1, format_func=lambda i: by_id[i].name, key=f"cmp_b_{trip.id}")
    if a_id == b_id:
        st.warning("Choose two different options.")
        return

    comparison = compare_options(summaries[a_id], summaries[b_id], trip.travellers, trip.time_value_nzd_per_hour)
    _results(comparison, trip)
    _what_if(repo, trip, by_id[a_id], by_id[b_id], destinations, comparison)

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


def _all_options(summaries: list[OptionSummary], trip: Trip) -> None:
    st.subheader("All options")
    value = trip.time_value_nzd_per_hour
    rows = []
    for s in summaries:
        row = {
            "Option": s.option.name,
            "Route": f"{s.option.origin} → {s.option.destination}",
            "Group NZD": float(s.total_nzd),
            "Per person NZD": float(s.per_person_nzd or 0),
            "Hours": float(s.travel_hours),
            "Effective hours": float(s.effective_hours),
            "Transfers": s.transfers,
        }
        if value > 0:
            row["Cost incl. time NZD"] = float(generalised_cost_nzd(s, value, trip.travellers))
        rows.append(row)
    df = pd.DataFrame(rows).sort_values("Cost incl. time NZD" if value > 0 else "Group NZD")
    money = st.column_config.NumberColumn(format="%.2f")
    st.dataframe(df, hide_index=True, width="stretch",
                 column_config={c: money for c in ("Group NZD", "Per person NZD", "Cost incl. time NZD")})
    notes = ["Click a column header to sort.",
             "Effective hours = door-to-door hours + disruption penalty for legs marked disruptive."]
    if value > 0:
        notes.append(f"Cost incl. time = group cost + NZD {value:,.2f} × {trip.travellers} traveller(s) × "
                     "effective hours.")
    else:
        notes.append("Set a value for your time in Settings to rank options by cost including time.")
    if df["Route"].nunique() > 1:
        notes.append("⚠ Options cover different routes, so not every pair is like-for-like.")
    st.caption(" ".join(notes))

    points = alt.Chart(df).mark_circle(size=140).encode(
        x=alt.X("Hours:Q", title="Door-to-door hours", scale=alt.Scale(zero=False, padding=60)),
        y=alt.Y("Group NZD:Q", title="Total cost (group NZD)", scale=alt.Scale(zero=False, padding=40)),
        color=alt.Color("Route:N"),
        tooltip=["Option", "Route", alt.Tooltip("Group NZD:Q", format=",.2f"), "Hours", "Transfers"],
    )
    labels = points.mark_text(align="center", dy=-14, fontSize=11).encode(text="Option:N")
    st.altair_chart((points + labels).properties(height=320), width="stretch")
    st.caption("Lower-left is cheaper and faster. An option that is above and to the right of another is worse on "
               "both counts.")


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
    _time_value(c)


def _time_value(c: Comparison) -> None:
    tv = c.time_value
    st.markdown("**Is it worth it for you?**")
    not_worth_it = tv.break_even_value_per_person_hour is not None and tv.better not in (None, c.cheaper)
    (st.warning if not_worth_it else st.info)(tv.verdict)
    if tv.value_per_person_hour > 0:
        st.dataframe(pd.DataFrame([
            ("Effective hours (incl. disruption)", fmt_hours(c.a.effective_hours), fmt_hours(c.b.effective_hours)),
            ("Disruptive legs", str(c.a.disruptive_legs), str(c.b.disruptive_legs)),
            ("Total cost (group)", format_nzd(c.a.total_nzd), format_nzd(c.b.total_nzd)),
            ("Value of time spent (group)", format_nzd(tv.time_cost_a_nzd), format_nzd(tv.time_cost_b_nzd)),
            ("Cost incl. time (group)", format_nzd(tv.generalised_a_nzd), format_nzd(tv.generalised_b_nzd)),
        ], columns=["", "Option A", "Option B"]), hide_index=True, width="stretch")
    st.caption("Break-even time value = cost saving ÷ (extra effective hours × travellers), in NZD per "
               "person-hour. Set your own time value and disruption penalty in Settings; mark overnight or very "
               "early legs as disruptive in the leg form.")


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


# --------------------------------------------------------------------------- what if


def _what_if(repo: Repository, trip: Trip, a: RouteOption, b: RouteOption, destinations, base: Comparison) -> None:
    st.markdown("**What if prices change?**")
    margin = saving_margin(base)
    if margin is not None:
        other = "B" if margin.cheaper == "A" else "A"
        rise = ("any amount" if margin.cheaper_rise_pct is None else f"more than **{margin.cheaper_rise_pct}%**")
        st.write(f"Option {margin.cheaper} stays cheaper unless its costs rise by {rise}, or option {other}'s costs "
                 f"fall by more than **{margin.other_fall_pct}%**.")
        if margin.cheaper_rise_pct is not None and margin.cheaper_rise_pct < 10:
            st.caption("⚠ That's a thin margin for prices that are still estimates.")

    with st.expander("Try a scenario (nothing is saved)"):
        c1, c2, c3 = st.columns(3)
        fx = c1.slider("Foreign exchange rates", -30, 30, 0, 5, format="%d%%", key=f"wi_fx_{trip.id}",
                       help="+10% = every foreign currency costs 10% more NZD. NZD-priced items don't change.")
        pa = c2.slider("Option A prices", -50, 50, 0, 5, format="%d%%", key=f"wi_a_{trip.id}",
                       help="Moves every fare, additional cost and expense price in option A.")
        pb = c3.slider("Option B prices", -50, 50, 0, 5, format="%d%%", key=f"wi_b_{trip.id}")

        scenario_trip = with_rate_change(trip, Decimal(fx))
        summaries = []
        for option, pct in ((a, pa), (b, pb)):
            legs, expenses = with_price_change(repo.list_legs(trip.id, option.id),
                                               repo.list_expenses(trip.id, option.id), Decimal(pct))
            summaries.append(summarize_option(scenario_trip, option, legs, expenses, destinations))
        scenario = compare_options(*summaries, trip.travellers, trip.time_value_nzd_per_hour)

        def remaining(c: Comparison) -> str:
            be = c.break_even
            if be.cheap_fare_label is None:
                return "—"
            if be.saving_survives:
                return f"{format_nzd(be.remaining_allowance_nzd)} left (option {be.cheap_fare_label})"
            return f"{format_nzd(be.excess_nzd)} over (option {be.cheap_fare_label})"

        st.dataframe(pd.DataFrame([
            ("Option A total (group)", format_nzd(base.a.total_nzd), format_nzd(scenario.a.total_nzd)),
            ("Option B total (group)", format_nzd(base.b.total_nzd), format_nzd(scenario.b.total_nzd)),
            ("Difference (B − A)", format_nzd(base.cost_difference_nzd, True),
             format_nzd(scenario.cost_difference_nzd, True)),
            ("Cheaper option", base.cheaper or "equal", scenario.cheaper or "equal"),
            ("Break-even allowance", remaining(base), remaining(scenario)),
            ("Better incl. your time", base.time_value.better or "—", scenario.time_value.better or "—"),
        ], columns=["", "Now", "Scenario"]), hide_index=True, width="stretch")
        st.info(scenario.verdict)
        if trip.time_value_nzd_per_hour > 0:
            st.caption(scenario.time_value.verdict)


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
