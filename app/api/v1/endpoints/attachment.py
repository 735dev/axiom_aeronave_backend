from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, Query, Response, UploadFile, status
from fastapi_restful.cbv import cbv

from app.api.base import ContractController
from app.config.attachments import FIELD_KEY_MAX, MAX_BYTES
from app.config.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_NUMBER, MAX_PENDING_PAGE_SIZE
from app.schemas.attachment import (
    AttachmentLink,
    AttachmentRead,
    PendingAttachmentRead,
    ReadingPreview,
)
from app.schemas.common import Page
from app.schemas.ticket import ExtractionValidate, TicketRead
from app.services.attachment_service import AttachmentService
from app.services.dependency import get_attachment_service

#: `/tickets/{id}/attachments/...`: files of one ticket.
ticket_router = APIRouter(prefix="/tickets", tags=["Attachments"])
#: `/attachments/...`: receipt reading and the validation queue.
router = APIRouter(prefix="/attachments", tags=["Attachments"])

REPLAY_HEADER = "Idempotent-Replay"


async def _bytes_of(upload: UploadFile) -> bytes:
    """At most one byte over the limit: enough to tell it is too big, never the whole body."""
    return await upload.read(MAX_BYTES + 1)


@cbv(ticket_router)
class TicketAttachmentController(ContractController):
    service: AttachmentService = Depends(get_attachment_service)
    schema_class = TicketRead

    def validation_context(self):
        return {"permissions": self.service.principal.permissions}

    @ticket_router.get(
        "/{id}/attachments/", response_model=list[AttachmentRead], summary="Files of a ticket"
    )
    async def list_files(self, id: UUID):
        rows = await self.service.list_for_ticket(id)
        return [AttachmentRead.model_validate(r) for r in rows]

    @ticket_router.post(
        "/{id}/attachments/",
        response_model=TicketRead,
        status_code=status.HTTP_201_CREATED,
        summary="Attach a file (multipart); archived in Blackbox",
    )
    async def upload(
        self,
        id: UUID,
        response: Response,
        file: UploadFile = File(...),
        field_key: str | None = Form(default=None, max_length=FIELD_KEY_MAX),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=200),
    ):
        content = await _bytes_of(file)
        result = await self.service.upload(id, file.filename, content, field_key, idempotency_key)
        if result.replayed:
            response.headers[REPLAY_HEADER] = "true"
        return self.format_response(result.ticket)

    @ticket_router.get(
        "/{id}/attachments/{file_id}/download/",
        response_model=AttachmentLink,
        summary="Signed link to the original (~5 minutes, never stored)",
    )
    async def download(self, id: UUID, file_id: UUID):
        return AttachmentLink.model_validate(await self.service.download_link(id, file_id))

    @ticket_router.post(
        "/{id}/attachments/{file_id}/validate/",
        response_model=TicketRead,
        summary="Validate the reading of a receipt (review gate)",
    )
    async def validate(self, id: UUID, file_id: UUID, data: ExtractionValidate):
        return self.format_response(await self.service.validate_reading(id, file_id, data))

    @ticket_router.delete(
        "/{id}/attachments/{file_id}/",
        response_model=TicketRead,
        summary="Detach a file from an open ticket",
    )
    async def remove(self, id: UUID, file_id: UUID):
        return self.format_response(await self.service.remove(id, file_id))


@cbv(router)
class AttachmentController(ContractController):
    service: AttachmentService = Depends(get_attachment_service)
    schema_class = PendingAttachmentRead

    @router.post(
        "/extract/",
        response_model=ReadingPreview,
        summary="Read a receipt before the record exists (saves nothing)",
    )
    async def extract(
        self,
        type_code: str = Form(min_length=1, max_length=60),
        file: UploadFile = File(...),
    ):
        content = await _bytes_of(file)
        return ReadingPreview.model_validate(
            await self.service.extract(type_code, file.filename, content)
        )

    @router.get(
        "/pending/",
        response_model=Page[PendingAttachmentRead],
        summary="Receipts read by the machine that still wait for a person",
    )
    async def pending(
        self,
        ticket_id: UUID | None = Query(default=None),
        page: int = Query(default=1, ge=1, le=MAX_PAGE_NUMBER),
        limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PENDING_PAGE_SIZE),
    ):
        return await super().list()
