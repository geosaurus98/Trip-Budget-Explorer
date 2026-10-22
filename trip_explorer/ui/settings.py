"""Trip settings, exchange rates, import/export and demo data."""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
import streamlit as st

from ..calculations import summarize
from ..demo import demo_trip_ids, load_demo_trip
from ..models import Trip
from ..money import normalise_currency, to_decimal
from ..storage import Repository
from ..transfer import ImportValidationError, budget_csv, export_json, parse_json
from .common import confirm_delete, flash, money_input, safe_filename, saved, select_trip


def render(repo: Repository, trip: Trip | None) -> None:
    st.header("Settings and import/export")
    if trip is not None:
        _trip_settings(repo, trip)
        _rates(repo, trip)
        _export(repo, trip)
    _import(repo)
    _demo(repo)
    if trip is not None:
        _delete_trip(repo, trip)
    st.caption(f"Local database: `{repo.db_path}`")


def _trip_settings(repo: Repository, trip: Trip) -> None:
    st.subheader("Trip settings")
    k = f"trip_{trip.id}"
    with st.form(k):
        name = st.text_input("Trip name *", trip.name, key=f"{k}_name")
        c1, c2, c3 = st.columns(3)
        start = c1.date_input("Start date", value=trip.start_date, key=f"{k}_start", format="YYYY-MM-DD")
        end = c2.date_input("End date", value=trip.end_date, key=f"{k}_end", format="YYYY-MM-DD")
        travellers = c3.number_input("Number of travellers", min_value=1, value=trip.travellers, step=1,
                                     key=f"{k}_trav")
        budget = money_input(st, "Budget for the whole group (NZD)", trip.budget_nzd, f"{k}_budget")
        notes = st.text_area("Notes", trip.notes, key=f"{k}_notes")
        submitted = st.form_submit_button("Save trip settings", type="primary")
    if submitted:
        if not name.strip():
            st.error("Trip name is required.")
            return
        trip.name, trip.start_date, trip.end_date = name.strip(), start, end
        trip.travellers, trip.budget_nzd, trip.notes = int(travellers), budget, notes
        repo.update_trip(trip)
        if start and end and end < start:
            flash("End date is before start date — saved anyway.", "warning")
        saved("Saved trip settings.")


def _rates(repo: Repository, trip: Trip) -> None:
    st.subheader("Exchange rates")
    st.markdown("Enter each rate as **NZD per 1 unit of foreign currency**. Example: if 1 SGD costs 1.27 NZD, "
                "enter `1.27`; if 1 IDR costs 0.000105 NZD, enter `0.000105`. NZD is always 1.")
    df = pd.DataFrame(
        [{"Currency": code, "NZD per 1 unit": str(rate)} for code, rate in trip.rates.items()],
        columns=["Currency", "NZD per 1 unit"],
    )
    edited = st.data_editor(
        df, num_rows="dynamic", hide_index=True, width="stretch", key=f"rates_{trip.id}",
        column_config={
            "Currency": st.column_config.TextColumn("Currency (e.g. IDR)", max_chars=3, required=True),
            "NZD per 1 unit": st.column_config.TextColumn("NZD per 1 unit", required=True),
        },
    )
    if st.button("Save exchange rates", type="primary", key=f"rates_save_{trip.id}"):
        rates: dict[str, Decimal] = {}
        errors = []
        for _, row in edited.iterrows():
            code = normalise_currency(str(row["Currency"] or ""))
            raw = row["NZD per 1 unit"]
            if not code and (raw is None or str(raw).strip() in ("", "None", "nan")):
                continue
            if len(code) != 3 or not code.isalpha():
                errors.append(f"'{code}' is not a 3-letter currency code.")
                continue
            if code == "NZD":
                continue
            try:
                rate = to_decimal(str(raw))
                if rate <= 0:
                    raise ValueError
            except ValueError:
                errors.append(f"Rate for {code} must be a positive number.")
                continue
            rates[code] = rate
        if errors:
            for e in errors:
                st.error(e)
        else:
            trip.rates = rates
            repo.update_trip(trip)
            saved("Saved exchange rates. All totals now use the new rates.")


def _export(repo: Repository, trip: Trip) -> None:
    st.subheader("Export")
    c1, c2 = st.columns(2)
    c1.download_button("⬇ Export whole trip (JSON)", export_json(repo.load_bundle(trip.id)),
                       file_name=safe_filename(trip, "trip.json"), mime="application/json")
    summary = summarize(trip, repo.list_legs(trip.id), repo.list_expenses(trip.id), repo.list_destinations(trip.id))
    c2.download_button("⬇ Budget breakdown (CSV)", budget_csv(summary), file_name=safe_filename(trip, "budget.csv"),
                       mime="text/csv")


def _import(repo: Repository) -> None:
    st.subheader("Import a trip (JSON)")
    st.caption("The file is fully validated first. A valid file is added as a **new** trip; existing trips are "
               "never modified.")
    upload = st.file_uploader("Trip JSON file", type=["json"], key="import_file")
    if upload is None:
        return
    try:
        bundle = parse_json(upload.getvalue())
    except ImportValidationError as exc:
        st.error("This file can't be imported:")
        for err in exc.errors[:30]:
            st.write("• " + err)
        return
    st.success(f"Valid file: '{bundle.trip.name}' — {len(bundle.destinations)} stops, "
               f"{len([l for l in bundle.legs if l.option_id is None])} legs, "
               f"{len([e for e in bundle.expenses if e.option_id is None])} expenses, "
               f"{len(bundle.options)} route options.")
    if st.button("Import as new trip", type="primary", key="import_btn"):
        new_id = repo.save_bundle(bundle)
        select_trip(new_id)
        saved(f"Imported '{bundle.trip.name}'.")


def _demo(repo: Repository) -> None:
    st.subheader("Demo data")
    st.caption("A fictional Indonesia → Singapore example. All figures are illustrative, not real prices.")
    existing = demo_trip_ids(repo)
    if existing:
        st.write("A demo trip is already loaded. Reloading **replaces** it (any edits to the demo are lost).")
        if confirm_delete("the current demo trip and reload a fresh copy", key="reload_demo"):
            select_trip(load_demo_trip(repo, replace_existing=True))
            saved("Reloaded the demo trip.")
    elif st.button("Load demo trip", key="load_demo"):
        select_trip(load_demo_trip(repo))
        saved("Loaded the demo trip.")


def _delete_trip(repo: Repository, trip: Trip) -> None:
    st.subheader("Danger zone")
    if confirm_delete(f"the whole trip '{trip.name}' and everything in it", key=f"del_trip_{trip.id}"):
        repo.delete_trip(trip.id)
        select_trip(None)
        saved(f"Deleted trip '{trip.name}'.")
