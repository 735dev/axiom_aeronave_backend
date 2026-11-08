from __future__ import annotations

import re
from typing import Any

from fastapi_basekit.exceptions.api_exceptions import NotFoundException

from app.config.catalogs import CATALOG_DEFINITIONS, CATALOG_KEYS
from app.exceptions.admin import AdminError
from app.models.catalog import CatalogItem
from app.repositories.catalog import CatalogItemRepository
from app.services.base import ProjectService
from app.utils.slug import slugify

_CODE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class CatalogService(ProjectService[CatalogItem]):
    repository: CatalogItemRepository

    filter_whitelist = frozenset({"catalog", "is_active"})
    search_fields = ["name", "code"]

    def get_filters(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        applied = super().get_filters(filters)
        catalog = applied.get("catalog")
        if catalog is not None and catalog not in CATALOG_KEYS:
            raise AdminError("validation", "El catálogo indicado no existe.", field="catalog")
        return applied

    async def definitions(self) -> list[dict[str, Any]]:
        counts = await self.repository.count_by_catalog()
        return [
            {
                "key": d.key,
                "name": d.name,
                "description": d.description,
                "items_count": counts.get(d.key, 0),
            }
            for d in CATALOG_DEFINITIONS
        ]

    async def create(self, payload, check_fields=None) -> CatalogItem:
        data = payload.model_dump() if hasattr(payload, "model_dump") else dict(payload)
        catalog = data["catalog"]
        if catalog not in CATALOG_KEYS:
            raise AdminError("validation", "El catálogo indicado no existe.", field="catalog")
        name = data["name"].strip()
        code = (data.get("code") or "").strip() or slugify(name)
        if not code or not _CODE_PATTERN.match(code):
            raise AdminError(
                "validation",
                "El código solo admite letras, números, punto, guion y guion bajo.",
                field="code",
            )
        if await self.repository.get_by_catalog_code(catalog, code):
            raise AdminError(
                "duplicate", "Ya existe un registro con ese código en este catálogo.", field="code"
            )
        return await self.repository.create(
            {
                "catalog": catalog,
                "code": code,
                "name": name,
                "extra": data.get("extra"),
                "is_system": False,
                "is_active": True,
                "sort_order": await self.repository.next_sort_order(catalog),
            }
        )

    async def update(self, id, data) -> CatalogItem:  # noqa: A002
        payload = data.model_dump(exclude_unset=True) if hasattr(data, "model_dump") else dict(data)
        item = await self.repository.get_scoped(id)
        if item is None:
            raise NotFoundException(f"id={id} no encontrado")
        if payload.get("is_active") is None:
            payload.pop("is_active", None)
        if payload.get("name") is None:
            payload.pop("name", None)
        elif "name" in payload:
            payload["name"] = payload["name"].strip()
        if payload.get("is_active") is False and item.is_system:
            raise AdminError("protected", "Ese registro es del sistema y no puede desactivarse.")
        allowed = {k: v for k, v in payload.items() if k in ("name", "extra", "is_active")}
        return await super().update(id, allowed)

    async def delete(self, id) -> bool:  # noqa: A002
        item = await self.repository.get_scoped(id)
        if item is None:
            raise NotFoundException(f"id={id} no encontrado")
        if item.is_system:
            raise AdminError("protected", "Ese registro es del sistema y no puede eliminarse.")
        if await self.repository.is_in_use(item):
            raise AdminError(
                "in_use", "El registro está en uso. Desactívalo en lugar de eliminarlo."
            )
        return await super().delete(id)
