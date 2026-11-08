from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi_restful.cbv import cbv

from app.api.base import ContractController
from app.schemas.module import ModuleRead, ModuleUpdate
from app.services.dependency import get_module_service
from app.services.module_service import ModuleService

router = APIRouter(prefix="/modules", tags=["Modules"])


@cbv(router)
class ModuleController(ContractController):
    """Modules are seed-only: the API reads them and configures their menu entry."""

    service: ModuleService = Depends(get_module_service)
    schema_class = ModuleRead

    @router.get("/", response_model=list[ModuleRead], summary="Modules (drive the side menu)")
    async def list_modules(self):
        return self.format_response(await self.service.list_all())

    @router.patch("/{id}/", response_model=ModuleRead, summary="Configure a module's menu entry")
    async def update(self, id: UUID, data: ModuleUpdate):
        return await super().update(id, data)
