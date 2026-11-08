from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi_restful.cbv import cbv

from app.api.base import ContractController
from app.config.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_NUMBER, MAX_PAGE_SIZE
from app.schemas.common import Page
from app.schemas.role import RoleCreate, RoleRead, RoleUpdate
from app.services.dependency import get_role_service
from app.services.role_service import RoleService

router = APIRouter(prefix="/roles", tags=["Roles"])


@cbv(router)
class RoleController(ContractController):
    service: RoleService = Depends(get_role_service)
    schema_class = RoleRead

    @router.get("/", response_model=Page[RoleRead], summary="List roles")
    async def list_roles(
        self,
        q: str | None = Query(default=None, max_length=100),
        is_active: bool | None = Query(default=None),
        is_system: bool | None = Query(default=None),
        page: int = Query(default=1, ge=1, le=MAX_PAGE_NUMBER),
        limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    ):
        return await super().list()

    @router.get("/{id}/", response_model=RoleRead, summary="Get a role")
    async def retrieve(self, id: UUID):
        return await super().retrieve(id)

    @router.post("/", response_model=RoleRead, status_code=status.HTTP_201_CREATED)
    async def create(self, data: RoleCreate):
        return await super().create(data)

    @router.patch("/{id}/", response_model=RoleRead, summary="Update a role")
    async def update(self, id: UUID, data: RoleUpdate):
        return await super().update(id, data)

    @router.delete("/{id}/", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
    async def delete(self, id: UUID):
        await super().delete(id)
