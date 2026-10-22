"""Shared Streamlit helpers and the reusable leg/expense forms."""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
import streamlit as st

from ..calculations import expense_quantity, expense_total_nzd, leg_components_nzd, stop_nights
from ..models import (
    BOOKING_STATUSES,
    EXPENSE_CATEGORIES,
    TRANSPORT_MODES,
    Destination,
    Expense,
    TransportLeg,
    Trip,
)
from ..money import MissingRateError, format_nzd, round_money, to_decimal
from ..storage import Repository

BASIS_LABELS = {"per_person": "Per person", "group": "Whole group"}
RATE_HELP = "Exchange rates are NZD per 1 unit of foreign currency (set them in Settings)."


# --------------------------------------------------------------------------- messages


def flash(message: str, kind: str = "success") -> None:
    """Queue a message that survives the rerun after a save."""
    st.session_state.setdefault("_flash", []).append((kind, message))


def show_flash() -> None:
    for kind, message in st.session_state.pop("_flash", []):
        getattr(st, kind)(message)


def saved(message: str) -> None:
    flash(message)
    st.rerun()


def select_trip(trip_id: int | None) -> None:
    """Switch the sidebar's selected trip on the next run (widgets can't be changed after they render)."""
    st.session_state["_next_trip_id"] = trip_id


# --------------------------------------------------------------------------- inputs


def money_input(container, label: str, value: Decimal, key: str, help: str | None = None) -> Decimal:
    number = container.number_input(label, min_value=0.0, value=float(value), step=1.0, format="%.2f", key=key, help=help)
    return round_money(to_decimal(number))


def currency_choices(trip: Trip, current: str | None = None) -> list[str]:
    codes = ["NZD"] + sorted(c for c in trip.rates if c != "NZD")
    if current and current not in codes:
        codes.append(current)  # keep an orphaned currency selectable
    return codes


def pick_record(label: str, records: list, describe, key: str, new_label: str = "➕ Add new"):
    """Selectbox of records by id, with an 'add new' entry. Returns the record or None."""
    by_id = {r.id: r for r in records}
    if st.session_state.get(key) not in [0, *by_id]:
        st.session_state.pop(key, None)  # the selected record was deleted
    choice = st.selectbox(label, [0, *by_id], format_func=lambda i: new_label if i == 0 else describe(by_id[i]), key=key)
    return by_id.get(choice)


def confirm_delete(what: str, key: str) -> bool:
    """Two-step delete: tick to confirm, then click."""
    cols = st.columns([3, 1])
    confirmed = cols[0].checkbox(f"I want to delete {what}", key=f"{key}_confirm")
    return cols[1].button("Delete", key=f"{key}_delete", disabled=not confirmed, width="stretch")


def parse_coordinate(text: str, limit: float, name: str) -> float | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"{name} must be a number (e.g. -8.5069)") from None
    if not -limit <= value <= limit:
        raise ValueError(f"{name} must be between −{limit} and {limit}")
    return value


def safe_filename(trip: Trip, suffix: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in trip.name).strip("_")[:40] or "trip"
    return f"{safe}_{suffix}"


def fmt_hours(hours: Decimal) -> str:
    return f"{hours.normalize():f} h"


# --------------------------------------------------------------------------- tables


def legs_table(legs: list[TransportLeg], trip: Trip) -> pd.DataFrame:
    rows = []
    for i, leg in enumerate(legs, start=1):
        try:
            base, extras = leg_components_nzd(leg, trip)
            total = format_nzd(base + extras)
        except MissingRateError as exc:
            total = f"⚠ {exc}"
        rows.append({
            "#": i, "From": leg.origin, "To": leg.destination, "Mode": leg.mode,
            "Door-to-door h": float(leg.duration_hours),
            "Fare + extras": f"{leg.currency} {leg.base_fare:,.2f} + {leg.extra_costs:,.2f}",
            "Basis": BASIS_LABELS[leg.basis], "Group total (NZD)": total, "Status": leg.status,
            "Disruptive": "yes" if leg.disruptive else "",
            "Depart (local)": leg.depart_local, "Arrive (local)": leg.arrive_local,
        })
    return pd.DataFrame(rows)


def expenses_table(expenses: list[Expense], trip: Trip, destinations: list[Destination]) -> pd.DataFrame:
    by_id = {d.id: d for d in destinations}
    rows = []
    for exp in expenses:
        try:
            total = format_nzd(expense_total_nzd(exp, trip, by_id))
        except MissingRateError as exc:
            total = f"⚠ {exc}"
        qty = expense_quantity(exp, by_id)
        rows.append({
            "Description": exp.description, "Category": exp.category,
            "Stop": by_id[exp.destination_id].name if exp.destination_id in by_id else "",
            "Unit price": f"{exp.currency} {exp.unit_price:,.2f}",
            "Qty": f"{qty.normalize():f}" + (" (stop nights)" if exp.nights_from_stop and exp.destination_id in by_id else ""),
            "Basis": BASIS_LABELS[exp.basis], "Group total (NZD)": total, "Status": exp.status,
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- forms


def deadline_inputs(book_by, cancel_by, key: str):
    """Two optional date inputs, used inside the leg and expense forms."""
    c1, c2 = st.columns(2)
    return (
        c1.date_input("Book by (optional)", value=book_by, key=f"{key}_bookby", format="YYYY-MM-DD",
                      help="Shown under Overview → Deadlines until the item is marked booked."),
        c2.date_input("Free cancellation until (optional)", value=cancel_by, key=f"{key}_cancelby",
                      format="YYYY-MM-DD",
                      help="For booked items: a reminder of the last day you can cancel without paying."),
    )


NOT_PAID = ""


def paid_by_input(trip: Trip, current: str, key: str) -> str:
    """Who paid, from the trip's traveller names. Hidden until names are set in Settings."""
    choices = [NOT_PAID, *trip.traveller_names]
    if current and current not in choices:
        choices.append(current)  # keep a payer whose name was since changed
    if len(choices) == 1:
        return current
    return st.selectbox("Paid by (for sharing costs)", choices, index=choices.index(current), key=f"{key}_paid",
                        format_func=lambda n: "— not paid yet / not recorded —" if n == NOT_PAID else n,
                        help="Budget → Who paid shows who owes whom. Costs are shared equally.")


def leg_form(repo: Repository, trip: Trip, leg: TransportLeg | None, option_id: int | None,
             key: str, default_origin: str = "", default_destination: str = "") -> None:
    is_new = leg is None
    leg = leg or TransportLeg(trip_id=trip.id, origin=default_origin, destination=default_destination,
                              option_id=option_id, currency="NZD")
    k = f"{key}_{leg.id or 'new'}"
    with st.form(k, clear_on_submit=is_new):
        c1, c2, c3 = st.columns(3)
        origin = c1.text_input("From *", leg.origin, key=f"{k}_o")
        destination = c2.text_input("To *", leg.destination, key=f"{k}_d")
        mode = c3.selectbox("Mode", TRANSPORT_MODES, index=TRANSPORT_MODES.index(leg.mode), key=f"{k}_m")

        c1, c2, c3 = st.columns(3)
        duration = c1.number_input(
            "Door-to-door duration (hours)", min_value=0.0, value=float(leg.duration_hours), step=0.25, key=f"{k}_h",
            help="Include getting to the station/airport, check-in, waiting and onward transfer. Used for comparisons.",
        )
        depart = c2.text_input("Departure (local, optional)", leg.depart_local, placeholder="2027-06-13 08:30",
                               key=f"{k}_dep", help="Reference only — no timezone calculations are done.")
        arrive = c3.text_input("Arrival (local, optional)", leg.arrive_local, placeholder="2027-06-13 14:30",
                               key=f"{k}_arr", help="Reference only — no timezone calculations are done.")

        c1, c2, c3 = st.columns(3)
        base = money_input(c1, "Base fare", leg.base_fare, f"{k}_fare")
        extras = money_input(c2, "Additional costs", leg.extra_costs, f"{k}_x",
                             help="Baggage, seat fees, airport/station transfers, etc.")
        currencies = currency_choices(trip, leg.currency)
        currency = c3.selectbox("Currency", currencies, index=currencies.index(leg.currency), key=f"{k}_c",
                                help=RATE_HELP)

        c1, c2 = st.columns(2)
        basis = c1.radio("Costs entered are…", list(BASIS_LABELS), format_func=BASIS_LABELS.get,
                         index=list(BASIS_LABELS).index(leg.basis), horizontal=True, key=f"{k}_b",
                         help="Per person: multiplied by the number of travellers. Whole group: used as-is.")
        status = c2.radio("Booking status", BOOKING_STATUSES, index=BOOKING_STATUSES.index(leg.status),
                          horizontal=True, key=f"{k}_s")
        disruptive = st.checkbox(
            "Disruptive (overnight, very early start, etc.)", value=leg.disruptive, key=f"{k}_dis",
            help="Adds the trip's disruption penalty hours when weighing time against money on the Compare screen. "
                 "Raw travel hours are not changed.",
        )
        book_by, cancel_by = deadline_inputs(leg.book_by, leg.cancel_by, k)
        paid_by = paid_by_input(trip, leg.paid_by, k)
        notes = st.text_area("Notes", leg.notes, height=68, key=f"{k}_n")
        url = st.text_input("Source URL (optional)", leg.source_url, key=f"{k}_u")
        submitted = st.form_submit_button("Add leg" if is_new else "Save leg", type="primary")

    if submitted:
        if not origin.strip() or not destination.strip():
            st.error("Please enter both 'From' and 'To'.")
            return
        updated = TransportLeg(
            id=leg.id, trip_id=trip.id, option_id=leg.option_id, position=leg.position,
            origin=origin.strip(), destination=destination.strip(), mode=mode, depart_local=depart.strip(),
            arrive_local=arrive.strip(), duration_hours=to_decimal(duration), base_fare=base, extra_costs=extras,
            currency=currency, basis=basis, status=status, notes=notes, source_url=url.strip(),
            disruptive=disruptive, book_by=book_by, cancel_by=cancel_by, paid_by=paid_by,
        )
        if is_new:
            repo.add_leg(updated)
            saved(f"Added leg {updated.origin} → {updated.destination}.")
        else:
            repo.update_leg(updated)
            saved(f"Saved leg {updated.origin} → {updated.destination}.")


def expense_form(repo: Repository, trip: Trip, expense: Expense | None, option_id: int | None,
                 destinations: list[Destination], key: str, default_category: str = "accommodation") -> None:
    is_new = expense is None
    expense = expense or Expense(trip_id=trip.id, description="", category=default_category, option_id=option_id,
                                 currency="NZD", basis="group")
    k = f"{key}_{expense.id or 'new'}"
    dest_by_id = {d.id: d for d in destinations}
    dest_ids = [0, *dest_by_id]

    st.caption(
        "**Calculation:** group total = unit price × quantity × (travellers if *per person*). "
        "**Accommodation:** unit price = price *per night*; quantity = nights. Choose **Whole group** if the nightly "
        "price covers the room(s) for everyone (for two rooms, enter the combined nightly price), or **Per person** "
        "if it is charged per guest (e.g. dorm beds)."
    )
    with st.form(k, clear_on_submit=is_new):
        c1, c2, c3 = st.columns([2, 1, 1])
        description = c1.text_input("Description *", expense.description, key=f"{k}_desc")
        category = c2.selectbox("Category", EXPENSE_CATEGORIES, index=EXPENSE_CATEGORIES.index(expense.category),
                                key=f"{k}_cat")
        dest_choice = c3.selectbox(
            "Stop (optional)", dest_ids,
            index=dest_ids.index(expense.destination_id) if expense.destination_id in dest_by_id else 0,
            format_func=lambda i: "— none —" if i == 0 else dest_by_id[i].name, key=f"{k}_dest",
        )

        c1, c2, c3 = st.columns(3)
        price = money_input(c1, "Unit price (nightly price for accommodation)", expense.unit_price, f"{k}_price")
        quantity = c2.number_input("Quantity (nights for accommodation)", min_value=0.0,
                                   value=float(expense.quantity), step=1.0, key=f"{k}_qty")
        currencies = currency_choices(trip, expense.currency)
        currency = c3.selectbox("Currency", currencies, index=currencies.index(expense.currency), key=f"{k}_cur",
                                help=RATE_HELP)

        nights_from_stop = st.checkbox(
            "Accommodation: use the linked stop's nights as the quantity (updates automatically when dates change)",
            value=expense.nights_from_stop, key=f"{k}_nights",
        )
        c1, c2 = st.columns(2)
        basis = c1.radio("Price is…", list(BASIS_LABELS), format_func=BASIS_LABELS.get,
                         index=list(BASIS_LABELS).index(expense.basis), horizontal=True, key=f"{k}_basis")
        status = c2.radio("Booking status", BOOKING_STATUSES, index=BOOKING_STATUSES.index(expense.status),
                          horizontal=True, key=f"{k}_status")
        book_by, cancel_by = deadline_inputs(expense.book_by, expense.cancel_by, k)
        paid_by = paid_by_input(trip, expense.paid_by, k)
        notes = st.text_area("Notes", expense.notes, height=68, key=f"{k}_notes")
        submitted = st.form_submit_button("Add expense" if is_new else "Save expense", type="primary")

    if submitted:
        if not description.strip():
            st.error("Please enter a description.")
            return
        destination_id = dest_choice or None
        qty = to_decimal(quantity)
        if nights_from_stop:
            if category != "accommodation":
                st.error("'Use the stop's nights' only applies to accommodation.")
                return
            if destination_id is None:
                st.error("Choose a stop to use its nights.")
                return
            nights = stop_nights(dest_by_id[destination_id])
            if nights is not None:
                qty = Decimal(nights)  # stored as a fallback if the stop is later removed
        updated = Expense(
            id=expense.id, trip_id=trip.id, option_id=expense.option_id, destination_id=destination_id,
            description=description.strip(), category=category, unit_price=price, quantity=qty,
            currency=currency, basis=basis, status=status, nights_from_stop=nights_from_stop, notes=notes,
            book_by=book_by, cancel_by=cancel_by, paid_by=paid_by,
        )
        if is_new:
            repo.add_expense(updated)
            saved(f"Added expense '{updated.description}'.")
        else:
            repo.update_expense(updated)
            saved(f"Saved expense '{updated.description}'.")
