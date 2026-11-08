"""Closed accounting periods: an expense of a closed month admits no change at all.

Mixed into `TicketService`, so every write that touches an expense goes through the same
check. Only types with an `amount` field are concerned (they are what gets reconciled).
"""

from __future__ import annotations

from datetime import date

from app.config.closing import PERIOD_CLOSED_MESSAGE
from app.config.tickets import FieldRole
from app.exceptions.closing import ClosedPeriodError
from app.models.ticket import Ticket, TicketType
from app.repositories.reconciliation import ReconciliationPeriodRepository
from app.repositories.ticket import TicketRepository
from app.utils.reconciliation import period_of


class PeriodLockMixin:
    repository: TicketRepository
    periods: ReconciliationPeriodRepository

    @staticmethod
    def _is_reconciled_type(ticket_type: TicketType) -> bool:
        return ticket_type.field_by_role(FieldRole.AMOUNT.value) is not None

    async def _reject_if_period_closed(self, ticket: Ticket, attempt: str) -> None:
        """409 when the expense belongs to a closed period. The refused attempt is kept in
        the trail in its own transaction."""
        if not self._is_reconciled_type(ticket.type):
            return
        period = period_of(ticket.accounting_date)
        if await self.periods.is_closed(period, lock=True):
            await self.repository.record_denied_period(ticket, attempt, period)
            raise ClosedPeriodError(PERIOD_CLOSED_MESSAGE.format(period=period, label=ticket.code))

    async def _reject_if_date_in_closed_period(
        self, ticket_type: TicketType, day: date, label: str, attempt: str
    ) -> None:
        """Same, for an expense that does not exist yet or whose date is being moved: without
        this a new expense could slip into a closed month and the close would stop being what
        was signed."""
        if not self._is_reconciled_type(ticket_type):
            return
        period = period_of(day)
        if await self.periods.is_closed(period, lock=True):
            await self.repository.record_denied_new_expense(label, attempt, period)
            raise ClosedPeriodError(PERIOD_CLOSED_MESSAGE.format(period=period, label=label))
