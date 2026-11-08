from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi_restful.cbv import cbv

from app.api.base import ContractController
from app.config.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_NUMBER, MAX_PAGE_SIZE
from app.schemas.notification import (
    NotificationPage,
    NotificationRead,
    NotificationUpdate,
    ReadAllResult,
)
from app.services.dependency import get_notification_service
from app.services.notification_service import NotificationService

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@cbv(router)
class NotificationController(ContractController):
    """Every query is scoped to the caller's audience; reading is personal."""

    service: NotificationService = Depends(get_notification_service)
    schema_class = NotificationRead

    @router.get("/", response_model=NotificationPage, summary="My notices")
    async def list_notifications(
        self,
        kind: str | None = Query(default=None, max_length=30),
        ticket_id: UUID | None = Query(default=None),
        unread: bool | None = Query(default=None),
        page: int = Query(default=1, ge=1, le=MAX_PAGE_NUMBER),
        limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    ):
        body = await super().list()
        body["unread"] = await self.service.unread_count()
        return body

    @router.post("/read-all/", response_model=ReadAllResult, summary="Mark all mine as read")
    async def read_all(self):
        marked, unread = await self.service.mark_all_read()
        return ReadAllResult(marked=marked, unread=unread)

    @router.patch("/{id}/", response_model=NotificationRead, summary="Mark one read or unread")
    async def update(self, id: UUID, data: NotificationUpdate):
        return NotificationRead.model_validate(await self.service.set_read(id, data.read))
