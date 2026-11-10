from __future__ import annotations

import re
from typing import Any

from fastapi_basekit.exceptions.api_exceptions import NotFoundException

from app.config.catalogs import CATALOG_DEFINITIONS, CATALOG_ITEM_CODE_MAX, CATALOG_KEYS
from app.exceptions.admin import AdminError
from app.models.catalog import CatalogItem
from app.repositories.catalog import CatalogItemRepository
from app.services.base import ProjectService
from app.services.bulk_import import BulkImportMixin
from app.utils.bulk_import import RowPlan
from app.utils.slug import slugify

_CODE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class CatalogService(BulkImportMixin, ProjectService[CatalogItem]):
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

    @staticmethod
    def _resolve_code(name: str, raw_code: str | None) -> str:
        code = (raw_code or "").strip() or slugify(name)
        if not code or not _CODE_PATTERN.match(code):
            raise AdminError(
                "validation",
                "El código solo admite letras, números, punto, guion y guion bajo.",
                field="code",
            )
        return code

    # ---- bulk import hooks (see BulkImportMixin) -----------------------------------

    async def import_prepare(self, rows, *, catalog: str) -> dict[str, Any]:
        if catalog not in CATALOG_KEYS:
            raise AdminError("validation", "El catálogo indicado no existe.", field="catalog")
        return {
            "catalog": catalog,
            "existing": await self.repository.items_by_code(catalog),
            "seen": set(),
            "next_order": await self.repository.next_sort_order(catalog),
        }

    async def import_plan_row(self, row, context) -> RowPlan:
        name = (row.name or "").strip()
        if not 2 <= len(name) <= 200:
            raise AdminError("validation", "El nombre debe tener entre 2 y 200 caracteres.", "name")
        code = self._resolve_code(name, row.code)
        if len(code) > CATALOG_ITEM_CODE_MAX:
            raise AdminError(
                "validation", f"El código admite hasta {CATALOG_ITEM_CODE_MAX} caracteres.", "code"
            )
        if code in context["seen"]:
            raise AdminError("duplicate", "El código se repite en el archivo.", field="code")
        context["seen"].add(code)
        extra = (row.extra or "").strip() or None
        if extra and len(extra) > 2000:
            raise AdminError("validation", "El detalle admite hasta 2000 caracteres.", "extra")
        current = context["existing"].get(code)
        if current is None:
            return RowPlan("create", {"code": code, "name": name, "extra": extra})
        if current.name == name and current.extra == extra:
            return RowPlan("skip")
        return RowPlan("update", {"name": name, "extra": extra}, target=current)

    async def import_apply_plan(self, plan: RowPlan, context) -> None:
        if plan.action == "update":
            await self.repository.update(plan.target.id, plan.data)
            return
        await self.repository.create(
            {
                **plan.data,
                "catalog": context["catalog"],
                "is_system": False,
                "is_active": True,
                "sort_order": context["next_order"],
            }
        )
        context["next_order"] += 1

    async def create(self, payload, check_fields=None) -> CatalogItem:
        data = payload.model_dump() if hasattr(payload, "model_dump") else dict(payload)
        catalog = data["catalog"]
        if catalog not in CATALOG_KEYS:
            raise AdminError("validation", "El catálogo indicado no existe.", field="catalog")
        name = data["name"].strip()
        code = self._resolve_code(name, data.get("code"))
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
