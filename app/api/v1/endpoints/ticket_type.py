from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi_restful.cbv import cbv

from app.api.base import ContractController
from app.schemas.ticket_type import TicketTypeCreate, TicketTypeRead, TicketTypeUpdate
from app.services.dependency import get_ticket_type_service
from app.services.ticket_type_service import TicketTypeService

router = APIRouter(prefix="/ticket-types", tags=["Ticket types"])


@cbv(router)
class TicketTypeController(ContractController):
    service: TicketTypeService = Depends(get_ticket_type_service)
    schema_class = TicketTypeRead

    @router.get("/", response_model=list[TicketTypeRead], summary="Ticket types")
    async def list_types(
        self,
        is_active: bool | None = Query(default=None),
        is_root: bool | None = Query(default=None),
    ):
        return self.format_response(await self.service.list_all(self._params()["filters"]))

    @router.get("/{id}/", response_model=TicketTypeRead, summary="A ticket type")
    async def retrieve(self, id: UUID):
        return await super().retrieve(id)

    @router.post("/", response_model=TicketTypeRead, status_code=status.HTTP_201_CREATED)
    async def create(self, data: TicketTypeCreate):
        return await super().create(data)

    @router.patch("/{id}/", response_model=TicketTypeRead, summary="Update a ticket type")
    async def update(self, id: UUID, data: TicketTypeUpdate):
        return await super().update(id, data)

    @router.delete("/{id}/", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
    async def delete(self, id: UUID):
        await super().delete(id)
