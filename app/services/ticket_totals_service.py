"""`GET /tickets/totals/`: the aggregation over exactly the rows the listing would show."""

from __future__ import annotations

from typing import Any

from app.repositories.ticket_analytics import TicketAnalyticsRepository, Totals
from app.services.ticket_service import TicketService


class TicketTotalsService(TicketService):
    """Inherits the listing's filter whitelist, translation and search fields, so the total
    can never be computed over different rows than the table the user is looking at."""

    repository: TicketAnalyticsRepository

    async def totals(self, filters: dict[str, Any] | None, search: str | None) -> Totals:
        await self.throttle("heavy_list")
        return await self.repository.totals(
            self.get_filters(filters), search=search, search_fields=self.search_fields
        )
