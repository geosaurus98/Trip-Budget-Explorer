"""Who paid what, and who owes whom.

Model: every active cost line that has a payer is shared *equally* between
all named travellers. Each line's group NZD total is split in whole cents
(leftover cents go to the first names), so shares always add up exactly.

    balance = amount paid − share of everything paid so far
    positive balance → is owed money; negative → owes money

Settle-up transfers pair the largest debtor with the largest creditor
repeatedly, which needs at most (travellers − 1) payments.

Amounts use the trip's current exchange rates, so this is a planning figure;
if you paid at a different rate, enter that rate before settling.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from .calculations import CostLine, cost_lines
from .models import Destination, Expense, TransportLeg, Trip
from .money import split_evenly


@dataclass
class Balance:
    name: str
    paid: Decimal
    share: Decimal

    @property
    def net(self) -> Decimal:
        return self.paid - self.share


@dataclass
class Transfer:
    payer: str  # who pays
    payee: str  # who receives
    amount: Decimal


@dataclass
class Settlement:
    balances: list[Balance]
    transfers: list[Transfer]
    paid_total: Decimal
    not_paid: list[CostLine] = field(default_factory=list)
    unknown_payer: list[tuple[CostLine, str]] = field(default_factory=list)


def settle_up(
    trip: Trip,
    legs: list[TransportLeg],
    expenses: list[Expense],
    destinations: list[Destination],
) -> Settlement:
    names = trip.traveller_names
    if not names:
        raise ValueError("Add traveller names in Settings to share costs")
    payer_of = {("transport", l.id): l.paid_by for l in legs}
    payer_of.update({("expense", e.id): e.paid_by for e in expenses})
    known = {n.lower(): n for n in names}

    paid: dict[str, Decimal] = defaultdict(Decimal)
    share: dict[str, Decimal] = defaultdict(Decimal)
    not_paid, unknown = [], []
    for line in cost_lines(trip, legs, expenses, destinations):
        if line.total_nzd is None:
            continue  # unpriced (missing exchange rate)
        payer = payer_of.get((line.kind, line.record_id), "").strip()
        if not payer:
            not_paid.append(line)
            continue
        if payer.lower() not in known:
            unknown.append((line, payer))
            continue
        paid[known[payer.lower()]] += line.total_nzd
        for name, part in zip(names, split_evenly(line.total_nzd, len(names))):
            share[name] += part

    balances = [Balance(n, paid[n], share[n]) for n in names]
    return Settlement(
        balances=balances,
        transfers=_transfers(balances),
        paid_total=sum(paid.values(), Decimal("0")),
        not_paid=not_paid,
        unknown_payer=unknown,
    )


def _transfers(balances: list[Balance]) -> list[Transfer]:
    creditors = [[b.name, b.net] for b in balances if b.net > 0]
    debtors = [[b.name, -b.net] for b in balances if b.net < 0]
    transfers = []
    while creditors and debtors:
        creditors.sort(key=lambda x: -x[1])
        debtors.sort(key=lambda x: -x[1])
        creditor, debtor = creditors[0], debtors[0]
        amount = min(creditor[1], debtor[1])
        transfers.append(Transfer(payer=debtor[0], payee=creditor[0], amount=amount))
        creditor[1] -= amount
        debtor[1] -= amount
        creditors = [c for c in creditors if c[1] > 0]
        debtors = [d for d in debtors if d[1] > 0]
    return transfers
