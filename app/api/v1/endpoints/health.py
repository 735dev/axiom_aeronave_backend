from fastapi import APIRouter, Depends
from fastapi_restful.cbv import cbv

from app.api.base import ContractController
from app.services.dependency import get_health_service
from app.services.health_service import HealthService

router = APIRouter(tags=["Health"])


@cbv(router)
class HealthController(ContractController):
    service: HealthService = Depends(get_health_service)

    @router.get("/health", summary="Liveness probe")
    async def live(self) -> dict[str, str]:
        return self.service.liveness()

    @router.get("/health/ready", summary="Readiness probe (checks the database)")
    async def ready(self) -> dict[str, str]:
        return await self.service.readiness()
