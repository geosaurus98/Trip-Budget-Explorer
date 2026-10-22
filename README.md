# Trip Route & Budget Explorer

A small local app for planning a trip by hand, seeing the route on a map, tracking estimated costs in NZD,
and answering one question:

> **Is this cheaper route actually worth the extra travel time and disruption?**

Built with Python, Streamlit, SQLite and Folium (via `streamlit-folium`). No accounts, no paid APIs and no live
prices: everything is entered manually and stored on your machine.

---

## Setup

Requires Python 3.10+ (developed on 3.13).

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Dependencies: `streamlit`, `folium`, `streamlit-folium`, `pandas`, and `pytest` for the tests.

## Run

```bash
streamlit run app.py
```

Streamlit opens the app at <http://localhost:8501>.

Run the tests with:

```bash
pytest
```

## Demo data

The first time the app starts with an **empty database**, it loads one clearly labelled fictional demo trip:
Bali → Banyuwangi/Ijen → Bromo → Malang → Yogyakarta → Jakarta → Medan → Bukit Lawang → Lake Toba → Singapore.
It includes two route options from Yogyakarta to Medan: A flies direct, and B takes the train to Jakarta and
then flies. All dates, prices, durations and exchange rates are **illustrative placeholders, not current or verified
travel information**.

- The demo is only seeded once. If you delete it, it stays deleted. A flag in the `meta` table records that
  first setup has happened.
- To load it again, go to **Settings & import/export → Demo data**. Reloading *replaces* the existing demo trip,
  so you never get duplicates.

## Where data is stored

- SQLite database: `data/trips.db` (git-ignored). Set the `TRIP_EXPLORER_DB` environment variable to use a
  different file, e.g. `TRIP_EXPLORER_DB=~/trips/summer.db streamlit run app.py`.
- To back up or move a trip, use **Settings → Export whole trip (JSON)** and **Import a trip (JSON)**.

## Project layout

```
app.py                       Streamlit entry point: sidebar, navigation only
trip_explorer/
  models.py                  Dataclasses (Trip, Destination, TransportLeg, Expense, RouteOption, TripBundle)
  money.py                   Decimal parsing, rounding, currency conversion
  calculations.py            Nights, cost lines, budget summary, itinerary and leg checks
  comparison.py              Route option summaries, comparison, break-even and time value
  scenarios.py               What-if price/exchange-rate scenarios and saving margins
  daily.py                   Spreading the budget over the trip's days
  history.py                 First estimate vs current cost (uses recorded cost history)
  storage.py                 SQLite Repository (all SQL lives here)
  transfer.py                JSON export/import with validation; CSV budget export
  demo.py                    Fictional demo trip and first-run seeding
  mapping.py                 Folium map builder
  ui/                        One module per screen + shared form helpers
    overview.py itinerary.py budget.py compare.py settings.py common.py
tests/                       pytest suite
```

Calculations and storage never import Streamlit, so you can use and test them from a plain Python shell:

```python
from trip_explorer.storage import Repository
from trip_explorer.calculations import summarize
repo = Repository("data/trips.db")
trip = repo.list_trips()[0]
print(summarize(trip, repo.list_legs(trip.id), repo.list_expenses(trip.id), repo.list_destinations(trip.id)).total_nzd)
```

## Calculation conventions

**Exchange rates** are always entered as **NZD per 1 unit of foreign currency**. For example, if 1 SGD costs 1.27 NZD,
enter `1.27`; if 1 IDR costs 0.000105 NZD, enter `0.000105`. NZD is always 1, and the conversion is
`NZD = foreign amount × rate`.

**One cost model** applies to every cost line:

```
group total (NZD) = round_to_cents( unit price × quantity × rate × basis multiplier )
basis multiplier  = number of travellers  if "per person"
                  = 1                     if "whole group"
```

- **Transport legs** use quantity = 1, and the unit price is the base fare plus additional costs (baggage,
  transfers…). The base fare and the additional costs are each converted and rounded separately, and the leg total
  is their sum.
- **Accommodation** uses the *nightly price* as the unit price and the number of nights as the quantity. Tick
  "use the linked stop's nights" to take the nights from the stop's dates. They update automatically whenever the
  dates change.
  - **Whole group** means the nightly price covers the room(s) for everyone. If you need two rooms, enter their
    combined nightly price.
  - **Per person** means the price is per guest per night (e.g. dorm beds).
  - There is no separate "rooms" multiplier, so a cost can't be multiplied by both travellers and rooms.
- **Nights** are departure date − arrival date.

**Money** uses Python `Decimal` throughout and never floats. Each cost line is rounded once to cents (half-up), and
totals are sums of the rounded lines, so breakdowns always add up exactly. "Per traveller" is the group total ÷
travellers, rounded to cents.

**Labels.** Group totals and per-person figures are labelled separately everywhere. Unless a figure says
*per person/traveller*, it is for the whole group.

**Missing rates.** A cost in a currency with no rate is *not* silently counted as zero. It is left out of
totals and shown as a warning.

**Durations** are the door-to-door hours you enter, and they are what comparisons use. Departure and arrival
times are free-text local reference times. No timezone maths is done.

**Itinerary checks** flag departures before arrivals, overlaps with the previous stop, stops outside the trip
dates and missing coordinates. Arriving on the day the previous stop departs is *not* an overlap. The checks are
warnings only, so you can still save unfinished drafts.

**Leg checks.** On **Itinerary → Transport legs**, the app checks that every pair of consecutive stops is joined by
a transport leg, or by a chain of legs through places that aren't stops (e.g. an airport). Place names match
ignoring capitals and extra spaces. When a connection is missing:

- It is flagged on the Itinerary screen and the Overview, because travel costs may be missing from the total.
- **Create placeholder legs** adds zero-cost "estimated" legs, in the right order, for you to fill in.
- **Sort legs by stop order** reorders legs to follow your stops. A chain of legs stays together.
- Leg places that don't match any stop are listed, which helps catch typos.

## Budget views

**By day** spreads the active budget over the trip's dates and shows a stacked chart, a running total, and
how much budget is left each day. All amounts are whole-group NZD. The allocation rules are:

- **Accommodation:** split evenly over the linked stop's nights.
- **Food:** split over the linked stop's nights. Food that isn't linked to a stop is split over every night of the
  trip, which needs trip dates.
- **Transport:** placed on the date in *Departure (local)* when it starts with `YYYY-MM-DD`. Otherwise it goes on the
  departure date of the stop the leg leaves from.
- **Activities and misc** linked to a stop: placed on the stop's arrival date.
- **Everything else** (e.g. travel insurance) is listed as *not tied to a date*. It counts in the running total
  from day one.

Splits are done in whole cents, so the days plus the undated amount always add up exactly to the budget total.

**Estimates vs current.** A snapshot is saved automatically whenever a leg's or expense's price, quantity,
currency, basis or booking status changes. Edits to notes, names or dates don't create snapshots, except that
changing a stop's dates records new snapshots for accommodation that takes its nights from that stop.

- The tab compares each item's *first recorded* cost with its cost now, and you can view any item's full history.
- It also shows how far your **booked** items moved from their first estimates.
- It then projects what happens if everything **not yet booked** drifts by the same percentage.
- Both the first and current figures use today's exchange rates and traveller count, so the comparison shows
  price changes, not currency movements.
- Items created before history tracking existed start their history at the next change.

## Comparing route options

A **route option** is a named alternative between a start point and an end point. It holds its own transport legs
and associated expenses, such as an extra hotel night or meals. Options are stored separately and **never count
towards the active itinerary totals** until you apply one.

**All options.** The top of the Compare screen lists every option side by side: group and per-person cost, hours,
effective hours, transfers and (when set) cost including time. Click a column header to sort it. A chart plots
cost against hours; lower-left is cheaper and faster.

**Two options in detail.** For a chosen pair A and B, the Compare screen shows:

| Measure | Definition |
|---|---|
| Total cost (group / per person) | All the option's legs and expenses, in NZD |
| Extra accommodation | The option's accommodation expenses |
| Door-to-door hours | Sum of its legs' entered durations |
| Transfers | Number of legs − 1 |
| Cost difference / time difference | B − A |
| Savings per extra travel hour | `cost saving ÷ extra hours`. Only shown when one option is cheaper **and** slower. |

Equal times, equal costs, and an option that is both cheaper and faster each get a plain-language verdict instead
of a division by zero.

### Break-even: maximum extra repositioning spend

Each option has a **headline fare**: the base fare of its main leg (e.g. the flight). You choose this leg under
*Edit options → Option details*. By default it is the leg with the largest base fare.

```
initial fare-saving allowance = headline fare(other option) − headline fare(cheap-fare option)
extra costs                   = non-headline costs(cheap-fare option) − non-headline costs(other option)
remaining allowance           = initial allowance − extra costs
                              = total(other option) − total(cheap-fare option)
```

"Non-headline costs" means everything else in the option: other legs' fares, baggage, transfers, extra hotel
nights and meals. All values are whole-group NZD, and per-person equivalents are shown alongside. If the remaining
allowance is negative, the app shows **how much the extra costs exceed the fare saving** instead of a misleading
positive allowance.

For example, in the demo Option B's flight from Jakarta saves NZD 252 (group) on the headline fare. The train, the
extra Jakarta night and the extra meals cost NZD 210 more, which leaves NZD 42 of allowance. That works out to
about NZD 6.46 saved per extra travel hour (6.5 h longer).

### Is it worth it for you? (time value)

Set two optional values under **Settings → Trip settings**:

- **Value of one hour of one traveller's time** (NZD per person-hour).
- **Disruption penalty** (hours) added for each leg you mark **disruptive**, such as overnight travel or a very
  early start.

The calculation, in group NZD:

```
effective hours  = door-to-door hours + penalty × number of disruptive legs
time cost        = time value × travellers × effective hours
cost incl. time  = total cost + time cost          → lower is the better deal for you
break-even value = cost saving ÷ (extra effective hours × travellers)   (NZD per person-hour)
```

If one traveller-hour is worth less to you than the break-even value, the cheaper-but-slower option is worth it.
Without a time value set, the app shows just the break-even value. If one option is cheaper without being slower,
there is no trade-off, and it says so. Raw door-to-door hours and "savings per extra hour" never include the
disruption penalty.

For example, the demo sets NZD 15 per person-hour and a 2 h penalty for the 6 am Jakarta flight. Option B's NZD 42
saving over 8.5 extra effective hours for 2 people gives a break-even of NZD 2.47 per person-hour, so the verdict
is *not worth it*.

### What if prices change?

Below the detailed comparison, the app shows how robust the saving is. It says how much the cheaper option's
costs can rise, or the other option's costs can fall, before the cheaper option stops being cheaper:

```
cheaper option may rise by = saving ÷ cheaper total × 100%
other option may fall by   = saving ÷ other total × 100%
```

The scenario sliders move every foreign exchange rate, and option A's or B's prices (fares, additional costs and
expense prices), by a percentage. The cost, break-even and time-value results are then recalculated
side by side with the current figures. Scenarios never change saved data.

### Applying an option

**Compare routes → Apply an option** does two things:

1. It **copies** the option's legs and expenses into the active itinerary. The option itself is kept, so you can
   keep comparing.
2. It **moves** the active legs/expenses you select into a new option called *"Previous plan (replaced by …)"*.
   They stop counting towards totals but are not deleted.

The app pre-selects the active legs that run from the option's start to its end, plus expenses at the
intermediate stops (e.g. the Jakarta hotel). This prevents double counting. Applying the same option twice
warns you first. Stops (destinations) are not changed, so remove a skipped stop on the Itinerary screen yourself.

## Import and export

- **JSON export** contains the whole trip: settings, rates, stops, active legs and expenses, route options and their items.
- **JSON import** validates the entire file first: format and version, required fields, numbers, dates,
  coordinates, allowed values and cross-references. Every problem is listed. Only a fully valid file is
  imported, as a **new** trip in a single database transaction, so existing data is never modified.
- **CSV export** (on Budget or Settings) lists every active cost line with its group NZD total, followed by total,
  per-traveller, budget and remaining rows.

## Known limitations

- Prices, durations and rates are manual. There are no live prices, geocoding, routing or booking.
- The map draws **straight dashed lines in itinerary order**, not real roads or flight paths. It needs an
  internet connection to load the Leaflet library and OpenStreetMap tiles. Stops without coordinates are
  skipped.
- There are no timezones: departure/arrival times are reference text only.
- Each trip uses one fixed exchange rate per currency, with no historical or date-specific rates.
- The detailed comparison covers two options at a time, though the overview table shows them all. Stops are not
  updated when an option is applied.
- Legs are matched to stops by place name, so a renamed stop needs its legs renamed too.
- Cost history is not included in JSON exports, so an imported trip starts with fresh history.
- The what-if sliders apply one percentage to all of an option's prices. To test a single fare, edit it directly.
- Time value is one number for everyone in the group. There is no separate weighting for transfers or comfort.
- It is a single-user local app with no authentication, and it isn't designed for several people editing the
  same database at once.
- Database upgrades are simple: new columns are added automatically when the app starts. There is no
  framework for renaming or removing columns, so export your trips to JSON before any larger upgrade.
