from fastapi import APIRouter, Depends, Query, Response
from fastapi_restful.cbv import cbv

from app.api.base import ContractController
from app.config.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_NUMBER, MAX_PAGE_SIZE
from app.schemas.audit import AuditEventRead
from app.schemas.common import Page
from app.services.audit_service import AuditService
from app.services.dependency import get_audit_service

router = APIRouter(prefix="/audit", tags=["Audit"])


@cbv(router)
class AuditController(ContractController):
    """Read-only. Events are written by the repositories, never by a client."""

    service: AuditService = Depends(get_audit_service)
    schema_class = AuditEventRead

    @router.get("/", response_model=Page[AuditEventRead], summary="Audit trail")
    async def list_events(
        self,
        entity: str | None = Query(default=None, max_length=30),
        entity_id: str | None = Query(default=None, max_length=255),
        action: str | None = Query(default=None, max_length=30),
        actor_id: str | None = Query(default=None, max_length=64),
        from_: str | None = Query(default=None, alias="from", max_length=40),
        to: str | None = Query(default=None, max_length=40),
        search: str | None = Query(default=None, max_length=100),
        page: int = Query(default=1, ge=1, le=MAX_PAGE_NUMBER),
        limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    ):
        return await super().list()

    @router.get("/export/", summary="CSV of the trail (UTF-8 with BOM), same filters")
    async def export_events(
        self,
        entity: str | None = Query(default=None, max_length=30),
        entity_id: str | None = Query(default=None, max_length=255),
        action: str | None = Query(default=None, max_length=30),
        actor_id: str | None = Query(default=None, max_length=64),
        from_: str | None = Query(default=None, alias="from", max_length=40),
        to: str | None = Query(default=None, max_length=40),
        search: str | None = Query(default=None, max_length=100),
    ):
        filters = {
            "entity": entity,
            "entity_id": entity_id,
            "action": action,
            "actor_id": actor_id,
            "from": from_,
            "to": to,
        }
        filename, text = await self.service.export_csv(
            {k: v for k, v in filters.items() if v}, search
        )
        return Response(
            content=text.encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
