from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi_restful.cbv import cbv

from app.api.base import ContractController
from app.config.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_NUMBER, MAX_PAGE_SIZE
from app.schemas.audit import AuditEventRead
from app.schemas.common import Page, build_page
from app.schemas.ticket import (
    AuthorizeBody,
    DecisionCreate,
    ExtractionCreate,
    ExtractionRead,
    ExtractionValidate,
    TicketCreate,
    TicketRead,
    TicketUpdate,
)
from app.services.dependency import get_ticket_service
from app.services.ticket_service import TicketService

router = APIRouter(prefix="/tickets", tags=["Tickets"])


@cbv(router)
class TicketController(ContractController):
    service: TicketService = Depends(get_ticket_service)
    schema_class = TicketRead

    def validation_context(self):
        return {"permissions": self.service.principal.permissions}

    @router.get("/", response_model=Page[TicketRead], summary="Tickets")
    async def list_tickets(
        self,
        q: str | None = Query(default=None, max_length=100),
        code: str | None = Query(default=None, max_length=40),
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
        page: int = Query(default=1, ge=1, le=MAX_PAGE_NUMBER),
        limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    ):
        return await super().list()

    @router.get("/{id}/", response_model=TicketRead, summary="A ticket")
    async def retrieve(self, id: UUID):
        return await super().retrieve(id)

    @router.post("/", response_model=TicketRead, status_code=status.HTTP_201_CREATED)
    async def create(self, data: TicketCreate):
        return await super().create(data)

    @router.patch("/{id}/", response_model=TicketRead, summary="Edit or change the status")
    async def update(self, id: UUID, data: TicketUpdate):
        return await super().update(id, data)

    @router.get("/{id}/history/", response_model=Page[AuditEventRead], summary="Timeline")
    async def history(
        self,
        id: UUID,
        page: int = Query(default=1, ge=1, le=MAX_PAGE_NUMBER),
        limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    ):
        items, total = await self.service.history(id, page, limit)
        return build_page(AuditEventRead, items, total, page, limit)

    @router.post("/{id}/decisions/", response_model=TicketRead, status_code=status.HTTP_201_CREATED)
    async def decide(self, id: UUID, data: DecisionCreate):
        return self.format_response(await self.service.decide(id, data))

    @router.post("/{id}/authorize/", response_model=TicketRead, summary="Management approval")
    async def authorize(self, id: UUID, data: AuthorizeBody):
        return self.format_response(await self.service.authorize(id, data))

    @router.get(
        "/{id}/extractions/", response_model=list[ExtractionRead], summary="Receipt readings"
    )
    async def list_extractions(self, id: UUID):
        rows = await self.service.list_readings(id)
        return [ExtractionRead.model_validate(r) for r in rows]

    @router.post(
        "/{id}/extractions/", response_model=ExtractionRead, status_code=status.HTTP_201_CREATED
    )
    async def create_extraction(self, id: UUID, data: ExtractionCreate):
        return ExtractionRead.model_validate(await self.service.propose_reading(id, data))

    @router.post(
        "/{id}/extractions/{extraction_id}/validate/",
        response_model=TicketRead,
        summary="Validate a reading (review gate)",
    )
    async def validate_extraction(self, id: UUID, extraction_id: UUID, data: ExtractionValidate):
        return self.format_response(await self.service.validate_reading(id, extraction_id, data))
