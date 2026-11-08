from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi_restful.cbv import cbv

from app.api.base import ContractController
from app.schemas.totals import TotalsRead
from app.services.dependency import get_ticket_totals_service
from app.services.ticket_totals_service import TicketTotalsService

#: Mounted before the tickets router: `/tickets/totals/` must not be read as `/tickets/{id}/`.
router = APIRouter(prefix="/tickets", tags=["Tickets"])


@cbv(router)
class TicketTotalsController(ContractController):
    service: TicketTotalsService = Depends(get_ticket_totals_service)
    schema_class = TotalsRead

    @router.get("/totals/", response_model=TotalsRead, summary="Expense totals")
    async def totals(
        self,
        q: str | None = Query(default=None, max_length=100),
        status_code: str | None = Query(default=None, max_length=60),
        type_code: str | None = Query(default=None, max_length=60),
        parent_id: UUID | None = Query(default=None),
        assigned_role_id: UUID | None = Query(default=None),
        assigned_user_id: UUID | None = Query(default=None),
        is_root: bool | None = Query(default=None),
        open: bool | None = Query(default=None),
        aircraft: str | None = Query(default=None, max_length=20),
        mine: bool | None = Query(default=None),
        from_: str | None = Query(default=None, alias="from", max_length=10),
        to: str | None = Query(default=None, max_length=10),
    ):
        params = self._params()
        return self.format_response(
            await self.service.totals(params["filters"], params.get("search"))
        )
