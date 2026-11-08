from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi_restful.cbv import cbv

from app.api.base import ContractController
from app.schemas.closing import ClosingSummaryRead
from app.services.closing_service import ClosingService
from app.services.dependency import get_closing_service

router = APIRouter(prefix="/tickets", tags=["Closing"])


@cbv(router)
class ClosingController(ContractController):
    service: ClosingService = Depends(get_closing_service)
    schema_class = ClosingSummaryRead

    @router.get(
        "/{id}/closing/",
        response_model=ClosingSummaryRead,
        summary="Closing summary of an operation",
    )
    async def summary(self, id: UUID):
        return self.format_response(await self.service.summary(id))
