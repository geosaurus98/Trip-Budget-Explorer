"""A self-contained, printable HTML summary of one trip.

The file has no external assets, so it can be emailed, opened offline,
printed, or saved as PDF from the browser's print dialog.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from html import escape

from .calculations import itinerary_issues, missing_connections, stop_nights, summarize
from .comparison import compare_options, summarize_option
from .deadlines import upcoming_deadlines
from .models import TripBundle
from .money import format_nzd

BASIS = {"per_person": "per person", "group": "whole group"}

CSS = """
body { font: 14px/1.45 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; color: #1f2328;
       max-width: 960px; margin: 24px auto; padding: 0 16px; }
h1 { font-size: 26px; margin: 0 0 4px; } h2 { font-size: 18px; margin: 28px 0 8px; border-bottom: 2px solid #d0d7de;
padding-bottom: 4px; } .muted { color: #59636e; } .banner { background: #fff8c5; border: 1px solid #d4a72c;
padding: 8px 12px; border-radius: 6px; margin: 12px 0; }
.figures { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin: 16px 0; }
.figure { border: 1px solid #d0d7de; border-radius: 6px; padding: 10px 12px; }
.figure .label { font-size: 12px; color: #59636e; } .figure .value { font-size: 20px; font-weight: 600; }
table { border-collapse: collapse; width: 100%; margin: 6px 0 10px; font-size: 13px; }
th, td { text-align: left; padding: 5px 8px; border-bottom: 1px solid #d0d7de; vertical-align: top; }
th { background: #f6f8fa; } td.num, th.num { text-align: right; white-space: nowrap; }
tr.total td { font-weight: 600; border-top: 2px solid #1f2328; } ul { margin: 4px 0; padding-left: 20px; }
.verdict { background: #ddf4ff; border-left: 4px solid #0969da; padding: 8px 12px; margin: 8px 0; }
@media print { body { margin: 0; max-width: none; } h2 { break-after: avoid; } tr { break-inside: avoid; } }
"""


def _e(value) -> str:
    return escape(str(value if value is not None else ""))


def _table(headers: list[str], rows: list[list], numeric: set[int] = frozenset(), total: list | None = None) -> str:
    def cells(row, tag):
        return "".join(f'<{tag}{" class=num" if i in numeric else ""}>{_e(v)}</{tag}>' for i, v in enumerate(row))
    body = "".join(f"<tr>{cells(r, 'td')}</tr>" for r in rows)
    if total:
        body += f'<tr class="total">{cells(total, "td")}</tr>'
    return f"<table><thead><tr>{cells(headers, 'th')}</tr></thead><tbody>{body}</tbody></table>"


def _date(d: date | None) -> str:
    return d.strftime("%d %b %Y") if d else "—"


def trip_summary_html(bundle: TripBundle, today: date) -> str:
    trip = bundle.trip
    legs = [l for l in bundle.legs if l.option_id is None]
    expenses = [e for e in bundle.expenses if e.option_id is None]
    dests = bundle.destinations
    summary = summarize(trip, legs, expenses, dests)
    parts: list[str] = []

    # ---- header and key figures
    dates = (f"{trip.start_date:%a %d %b %Y} – {trip.end_date:%a %d %b %Y}"
             if trip.start_date and trip.end_date else "")
    parts.append(f"<h1>{_e(trip.name)}</h1><div class=muted>{_e(dates)} · {trip.travellers} traveller(s) · "
                 f"summary generated {_e(today.strftime('%d %b %Y'))}</div>")
    if trip.is_demo:
        parts.append('<div class="banner"><b>Fictional demo trip.</b> Dates, prices, durations and exchange rates '
                     "are illustrative placeholders, not current or verified travel information.</div>")
    if trip.notes:
        parts.append(f"<p>{_e(trip.notes)}</p>")
    remaining_label = "Budget remaining" if summary.remaining_nzd >= 0 else "Over budget"
    figures = [
        ("Total cost (whole group)", format_nzd(summary.total_nzd)),
        ("Cost per traveller", format_nzd(summary.per_traveller_nzd)),
        (f"{remaining_label} (group)", format_nzd(abs(summary.remaining_nzd))),
        ("Budget (group)", format_nzd(summary.budget_nzd)),
    ]
    parts.append('<div class="figures">' + "".join(
        f'<div class="figure"><div class="label">{_e(l)}</div><div class="value">{_e(v)}</div></div>'
        for l, v in figures) + "</div>")
    warnings = itinerary_issues(trip, dests) + [
        f"No transport leg from {a.name} to {b.name}." for a, b in missing_connections(dests, legs)]
    warnings += [f"Not included in totals (missing exchange rate): {l.description}" for l in summary.unpriced_lines]
    warnings = [w for w in warnings if "coordinates" not in w]
    if warnings:
        parts.append("<p><b>Things to check:</b></p><ul>" + "".join(f"<li>{_e(w)}</li>" for w in warnings) + "</ul>")

    # ---- itinerary
    parts.append("<h2>Itinerary</h2>")
    parts.append(_table(
        ["#", "Place", "Country", "Arrive", "Depart", "Nights", "Notes"],
        [[i, d.name, d.country, _date(d.arrival), _date(d.departure),
          "" if stop_nights(d) is None else stop_nights(d), d.notes] for i, d in enumerate(dests, start=1)],
        numeric={0, 5},
    ) if dests else "<p class=muted>No stops yet.</p>")

    # ---- transport
    parts.append("<h2>Transport</h2>")
    lines_by_record = {(l.kind, l.record_id): l for l in summary.lines}
    if legs:
        parts.append(_table(
            ["From", "To", "Mode", "Departs (local)", "Door-to-door", "Price", "Group NZD", "Status"],
            [[l.origin, l.destination, l.mode + (" ⚠ disruptive" if l.disruptive else ""), l.depart_local,
              f"{l.duration_hours.normalize():f} h",
              f"{l.currency} {l.base_fare + l.extra_costs:,.2f} {BASIS[l.basis]}",
              format_nzd(lines_by_record[("transport", l.id)].total_nzd), l.status] for l in legs],
            numeric={4, 6},
            total=["Total", "", "", "", f"{summary.transport_hours.normalize():f} h", "",
                   format_nzd(summary.by_category.get("transport", Decimal(0))), ""],
        ))
    else:
        parts.append("<p class=muted>No transport legs yet.</p>")

    # ---- budget by category, then expenses
    parts.append("<h2>Budget</h2>")
    cat_rows = [[c, format_nzd(v), format_nzd(v / trip.travellers if trip.travellers else None)]
                for c, v in sorted(summary.by_category.items(), key=lambda kv: -kv[1])]
    parts.append(_table(["Category", "Group NZD", "Per traveller NZD"], cat_rows, numeric={1, 2},
                        total=["Total", format_nzd(summary.total_nzd), format_nzd(summary.per_traveller_nzd)]))
    grouped = defaultdict(list)
    for l in summary.lines:
        if l.kind == "expense":
            grouped[l.category].append(l)
    for category, lines in grouped.items():
        parts.append(f"<p><b>{_e(category.capitalize())}</b></p>")
        parts.append(_table(
            ["Item", "Stop", "Price × qty", "Basis", "Group NZD", "Status"],
            [[l.description, l.destination, f"{l.currency} {l.unit_amount:,.2f} × {l.quantity.normalize():f}",
              BASIS[l.basis], format_nzd(l.total_nzd), l.status] for l in lines],
            numeric={4},
        ))

    # ---- deadlines
    deadlines = upcoming_deadlines(legs, expenses, today)
    if deadlines:
        parts.append("<h2>Deadlines</h2>")
        parts.append(_table(["Due", "Deadline", "Item", "Status"],
                            [[_date(d.due) + (" (overdue)" if d.urgency == "overdue" else ""), d.label,
                              d.description, d.status] for d in deadlines]))

    # ---- route comparisons: the first two options for each start/end pair
    by_route = defaultdict(list)
    for opt in bundle.options:
        by_route[(opt.origin.strip().lower(), opt.destination.strip().lower())].append(opt)
    pairs = [opts[:2] for opts in by_route.values() if len(opts) >= 2]
    if pairs:
        parts.append("<h2>Route comparisons</h2>")
    for a_opt, b_opt in pairs:
        summaries = [summarize_option(trip, o, [l for l in bundle.legs if l.option_id == o.id],
                                      [e for e in bundle.expenses if e.option_id == o.id], dests)
                     for o in (a_opt, b_opt)]
        c = compare_options(*summaries, trip.travellers, trip.time_value_nzd_per_hour)
        parts.append(f"<p><b>{_e(a_opt.origin)} → {_e(a_opt.destination)}</b>: A = {_e(a_opt.name)}; "
                     f"B = {_e(b_opt.name)}</p>")
        parts.append(_table(
            ["", "Option A", "Option B"],
            [["Total (group)", format_nzd(c.a.total_nzd), format_nzd(c.b.total_nzd)],
             ["Per person", format_nzd(c.a.per_person_nzd), format_nzd(c.b.per_person_nzd)],
             ["Door-to-door hours", f"{c.a.travel_hours.normalize():f}", f"{c.b.travel_hours.normalize():f}"],
             ["Transfers", c.a.transfers, c.b.transfers]],
            numeric={1, 2},
        ))
        verdicts = [c.verdict]
        be = c.break_even
        if be.cheap_fare_label is not None:
            verdicts.append(
                f"Break-even: option {be.cheap_fare_label}'s cheaper headline fare leaves "
                + (f"{format_nzd(be.remaining_allowance_nzd)} of allowance after its extra costs."
                   if be.saving_survives else f"nothing; extra costs exceed the saving by {format_nzd(be.excess_nzd)}."))
        verdicts.append(c.time_value.verdict)
        parts.append("".join(f'<div class="verdict">{_e(v)}</div>' for v in verdicts))

    # ---- conventions
    rates = ", ".join(f"1 {code} = NZD {rate}" for code, rate in sorted(trip.rates.items())) or "none entered"
    parts.append("<h2>Notes on the figures</h2><ul>"
                 "<li>All money is whole-group NZD unless marked per person/traveller.</li>"
                 f"<li>Exchange rates used (NZD per 1 unit): {_e(rates)}.</li>"
                 "<li>Each cost line is rounded to cents; totals are sums of rounded lines.</li>"
                 "<li>Travel times are the entered door-to-door hours; departure times are local reference only."
                 "</li><li>Route options are not included in the trip totals unless applied.</li></ul>")

    return (f"<!doctype html><html lang=en><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width, initial-scale=1'>"
            f"<title>{_e(trip.name)} – trip summary</title><style>{CSS}</style></head>"
            f"<body>{''.join(parts)}</body></html>")
