"""How estimates changed over time: first recorded cost vs current cost.

Both figures are converted with the trip's *current* exchange rates and
traveller count, so the comparison measures price and booking changes, not
exchange-rate movements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .calculations import expense_total_nzd, leg_total_nzd, line_total_nzd
from .models import CostSnapshot, Destination, Expense, TransportLeg, Trip
from .money import MissingRateError, round_money


@dataclass
class EstimateChange:
    record_type: str
    record_id: int
    description: str
    category: str
    first_status: str
    current_status: str
    first_recorded_at: str
    first_nzd: Decimal
    current_nzd: Decimal

    @property
    def change_nzd(self) -> Decimal:
        return self.current_nzd - self.first_nzd

    @property
    def change_pct(self) -> Decimal | None:
        if self.first_nzd == 0:
            return None
        return round_money(self.change_nzd / self.first_nzd * 100)


@dataclass
class AccuracyReport:
    rows: list[EstimateChange] = field(default_factory=list)
    booked_first_nzd: Decimal = Decimal("0")
    booked_current_nzd: Decimal = Decimal("0")
    not_booked_current_nzd: Decimal = Decimal("0")

    @property
    def booked_change_pct(self) -> Decimal | None:
        """How far booked prices ended up from their first estimates, in percent."""
        if self.booked_first_nzd == 0:
            return None
        return round_money((self.booked_current_nzd - self.booked_first_nzd) / self.booked_first_nzd * 100)

    @property
    def projected_extra_nzd(self) -> Decimal | None:
        """Extra cost if not-yet-booked items drift by the same percentage as booked ones did."""
        pct = self.booked_change_pct
        if pct is None:
            return None
        return round_money(self.not_booked_current_nzd * pct / 100)


def snapshot_total_nzd(snapshot: CostSnapshot, trip: Trip) -> Decimal:
    return line_total_nzd(snapshot.amount, snapshot.quantity, snapshot.currency, snapshot.basis,
                          trip.travellers, trip.rates)


def accuracy_report(
    trip: Trip,
    legs: list[TransportLeg],
    expenses: list[Expense],
    destinations: list[Destination],
    first_snapshots: dict[tuple[str, int], CostSnapshot],
) -> AccuracyReport:
    """Compare each active item's first recorded cost with its current cost.

    Items with no history (created before history existed) use their current
    state as the first estimate. Items in a currency with no rate are skipped.
    """
    dest_by_id = {d.id: d for d in destinations if d.id is not None}
    items = [("leg", leg.id, f"{leg.origin} → {leg.destination} ({leg.mode})", "transport", leg.status,
              lambda leg=leg: leg_total_nzd(leg, trip)) for leg in legs]
    items += [("expense", exp.id, exp.description, exp.category, exp.status,
               lambda exp=exp: expense_total_nzd(exp, trip, dest_by_id)) for exp in expenses]

    report = AccuracyReport()
    for record_type, record_id, description, category, status, current_total in items:
        try:
            current = current_total()
            first = first_snapshots.get((record_type, record_id))
            first_nzd = snapshot_total_nzd(first, trip) if first else current
        except MissingRateError:
            continue
        report.rows.append(EstimateChange(
            record_type=record_type, record_id=record_id, description=description, category=category,
            first_status=first.status if first else status, current_status=status,
            first_recorded_at=first.recorded_at if first else "", first_nzd=first_nzd, current_nzd=current,
        ))
        if status == "booked":
            report.booked_first_nzd += first_nzd
            report.booked_current_nzd += current
        else:
            report.not_booked_current_nzd += current
    return report
