from fastapi import APIRouter, Depends, Query, Response
from fastapi_restful.cbv import cbv

from app.api.base import ContractController
from app.schemas.reconciliation import (
    AcknowledgementBody,
    ClosedPeriodRead,
    ExportReceiptRead,
    PeriodBody,
    ReportRead,
)
from app.services.dependency import get_reconciliation_service
from app.services.reconciliation_service import ReconciliationService

router = APIRouter(prefix="/reconciliation", tags=["Reconciliation"])


@cbv(router)
class ReconciliationController(ContractController):
    service: ReconciliationService = Depends(get_reconciliation_service)
    schema_class = ReportRead

    @router.get("/", response_model=ReportRead, summary="Report of a month")
    async def report(self, period: str = Query(default="", max_length=7)):
        return ReportRead.model_validate(await self.service.report(period))

    @router.get("/periods/", response_model=list[ClosedPeriodRead], summary="Closed periods")
    async def periods(self):
        return [ClosedPeriodRead.model_validate(p) for p in await self.service.closed_periods()]

    @router.post("/acknowledgements/", response_model=ReportRead, summary="Justify a difference")
    async def acknowledge(self, data: AcknowledgementBody):
        return ReportRead.model_validate(await self.service.acknowledge(data))

    @router.post("/close/", response_model=ReportRead, summary="Close a period")
    async def close(self, data: PeriodBody):
        return ReportRead.model_validate(await self.service.close(data))

    @router.post(
        "/export/", response_model=ExportReceiptRead, summary="Record an export built elsewhere"
    )
    async def record_export(self, data: PeriodBody):
        return ExportReceiptRead.model_validate(await self.service.record_export(data.period))

    @router.get("/export/", summary="CSV of the detail (UTF-8 with BOM)")
    async def export(self, period: str = Query(default="", max_length=7)):
        filename, text = await self.service.export_csv(period)
        return Response(
            content=text.encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
