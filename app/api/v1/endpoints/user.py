from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi_restful.cbv import cbv

from app.api.base import ContractController
from app.config.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_NUMBER, MAX_PAGE_SIZE
from app.schemas.common import Page
from app.schemas.importing import ImportResult, UserImportRequest
from app.schemas.user import (
    EffectivePermissionRead,
    TemporaryPasswordRead,
    UserCreate,
    UserRead,
    UserUpdate,
)
from app.services.dependency import get_user_service
from app.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["Users"])


@cbv(router)
class UserController(ContractController):
    service: UserService = Depends(get_user_service)
    schema_class = UserRead

    @router.get("/", response_model=Page[UserRead], summary="List users")
    async def list_users(
        self,
        q: str | None = Query(default=None, max_length=100),
        is_active: bool | None = Query(default=None),
        role_id: UUID | None = Query(default=None),
        location: str | None = Query(default=None, max_length=60),
        aircraft_id: str | None = Query(default=None, max_length=60),
        page: int = Query(default=1, ge=1, le=MAX_PAGE_NUMBER),
        limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    ):
        return await super().list()

    @router.post(
        "/import/",
        response_model=ImportResult,
        summary="Bulk create of users (all or nothing; dry_run to preview)",
    )
    async def import_users(self, data: UserImportRequest):
        report = await self.service.run_import(data.rows, data.dry_run)
        return ImportResult.model_validate(report)

    @router.get("/{id}/", response_model=UserRead, summary="Get a user")
    async def retrieve(self, id: UUID):
        return await super().retrieve(id)

    @router.post("/", response_model=UserRead, status_code=status.HTTP_201_CREATED)
    async def create(self, data: UserCreate):
        return await super().create(data)

    @router.patch("/{id}/", response_model=UserRead, summary="Update a user")
    async def update(self, id: UUID, data: UserUpdate):
        return await super().update(id, data)

    @router.get(
        "/{id}/effective-permissions/",
        response_model=list[EffectivePermissionRead],
        summary="Role permissions, grants and revocations of a user",
    )
    async def effective_permissions(self, id: UUID):
        rows = await self.service.effective_permissions(id)
        return [EffectivePermissionRead.model_validate(r) for r in rows]

    @router.post(
        "/{id}/reset-password/",
        response_model=TemporaryPasswordRead,
        summary="Issue a temporary password (shown once)",
    )
    async def reset_password(self, id: UUID):
        return TemporaryPasswordRead(temporary_password=await self.service.reset_password(id))
