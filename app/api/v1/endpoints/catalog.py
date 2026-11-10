from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi_restful.cbv import cbv

from app.api.base import ContractController
from app.config.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_NUMBER, MAX_PAGE_SIZE
from app.schemas.catalog import (
    CatalogDefinitionRead,
    CatalogItemCreate,
    CatalogItemRead,
    CatalogItemUpdate,
)
from app.schemas.common import Page
from app.schemas.importing import CatalogImportRequest, ImportResult
from app.services.catalog_service import CatalogService
from app.services.dependency import get_catalog_service

router = APIRouter(tags=["Catalogs"])


@cbv(router)
class CatalogController(ContractController):
    service: CatalogService = Depends(get_catalog_service)
    schema_class = CatalogItemRead

    @router.get("/catalogs/", response_model=list[CatalogDefinitionRead], summary="Catalogs")
    async def list_catalogs(self):
        return await self.service.definitions()

    @router.get("/catalog-items/", response_model=Page[CatalogItemRead], summary="Catalog items")
    async def list_items(
        self,
        q: str | None = Query(default=None, max_length=100),
        catalog: str | None = Query(default=None, max_length=40),
        is_active: bool | None = Query(default=None),
        page: int = Query(default=1, ge=1, le=MAX_PAGE_NUMBER),
        limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    ):
        return await super().list()

    @router.post(
        "/catalog-items/import/",
        response_model=ImportResult,
        summary="Bulk upsert of items by code (all or nothing; dry_run to preview)",
    )
    async def import_items(self, data: CatalogImportRequest):
        report = await self.service.run_import(data.rows, data.dry_run, catalog=data.catalog)
        return ImportResult.model_validate(report)

    @router.get("/catalog-items/{id}/", response_model=CatalogItemRead, summary="Catalog item")
    async def retrieve(self, id: UUID):
        return await super().retrieve(id)

    @router.post(
        "/catalog-items/", response_model=CatalogItemRead, status_code=status.HTTP_201_CREATED
    )
    async def create(self, data: CatalogItemCreate):
        return await super().create(data)

    @router.patch("/catalog-items/{id}/", response_model=CatalogItemRead, summary="Update an item")
    async def update(self, id: UUID, data: CatalogItemUpdate):
        return await super().update(id, data)

    @router.delete(
        "/catalog-items/{id}/", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
    )
    async def delete(self, id: UUID):
        await super().delete(id)
