"""Booking and free-cancellation deadlines for the active itinerary.

* "Book by": shown until the item is marked booked; overdue once the date passes.
* "Free cancellation until": shown for booked items until the date passes,
  as a reminder of the last day you can still change your mind for free.
* Checklist items with a due date: shown until ticked off.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .models import Expense, TodoItem, TransportLeg

SOON_DAYS = 30
WEEK_DAYS = 7


@dataclass
class Deadline:
    kind: str  # "book", "cancel" or "todo"
    due: date
    description: str
    status: str
    record_type: str  # "leg", "expense" or "todo"
    record_id: int | None
    days_left: int

    @property
    def urgency(self) -> str:
        if self.days_left < 0:
            return "overdue"
        if self.days_left <= WEEK_DAYS:
            return "this week"
        if self.days_left <= SOON_DAYS:
            return "within 30 days"
        return "later"

    @property
    def label(self) -> str:
        return {"book": "Book by", "cancel": "Free cancellation until", "todo": "To do by"}[self.kind]


def upcoming_deadlines(
    legs: list[TransportLeg], expenses: list[Expense], today: date, todos: list[TodoItem] = ()
) -> list[Deadline]:
    """Open deadlines, soonest (or most overdue) first."""
    items = [("leg", leg.id, f"{leg.origin} → {leg.destination} ({leg.mode})", leg.status, leg.book_by,
              leg.cancel_by) for leg in legs]
    items += [("expense", exp.id, exp.description, exp.status, exp.book_by, exp.cancel_by) for exp in expenses]

    found = []
    for record_type, record_id, description, status, book_by, cancel_by in items:
        if book_by and status != "booked":
            found.append(Deadline("book", book_by, description, status, record_type, record_id,
                                  (book_by - today).days))
        if cancel_by and status == "booked" and cancel_by >= today:
            found.append(Deadline("cancel", cancel_by, description, status, record_type, record_id,
                                  (cancel_by - today).days))
    for todo in todos:
        if todo.due and not todo.done:
            found.append(Deadline("todo", todo.due, todo.title, "to do", "todo", todo.id, (todo.due - today).days))
    return sorted(found, key=lambda d: (d.due, d.kind, d.description))
