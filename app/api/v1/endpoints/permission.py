from fastapi import APIRouter, Depends, Query
from fastapi_restful.cbv import cbv

from app.api.base import ContractController
from app.schemas.permission import PermissionRead
from app.services.dependency import get_permission_service
from app.services.permission_service import PermissionService

router = APIRouter(prefix="/permissions", tags=["Permissions"])


@cbv(router)
class PermissionController(ContractController):
    """Closed catalog: read-only. Permissions are defined by the code, not by an admin."""

    service: PermissionService = Depends(get_permission_service)
    schema_class = PermissionRead

    @router.get("/", response_model=list[PermissionRead], summary="Permission catalog")
    async def list_permissions(self, module_code: str | None = Query(default=None, max_length=60)):
        filters = {"module_code": module_code} if module_code else None
        return self.format_response(await self.service.list_all(filters))
