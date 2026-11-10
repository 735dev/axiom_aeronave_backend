from __future__ import annotations

from importlib import import_module
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.sql import Select

from app.config.audit import AuditEntity
from app.config.catalogs import CATALOG_USAGE
from app.models.catalog import CatalogItem
from app.repositories.base import AuditedRepository


def _resolve_column(model_path: str, column: str):
    module_name, _, class_name = model_path.rpartition(".")
    return getattr(getattr(import_module(module_name), class_name), column)


class CatalogItemRepository(AuditedRepository[CatalogItem]):
    model = CatalogItem
    audit_entity = AuditEntity.CATALOG
    audit_labels = {
        "catalog": "Catálogo",
        "code": "Código",
        "name": "Nombre",
        "extra": "Detalle",
        "is_active": "Activo",
    }

    def build_list_queryset(self, **kwargs: Any) -> Select:
        return select(CatalogItem).order_by(
            CatalogItem.catalog, CatalogItem.sort_order, CatalogItem.code
        )

    def audit_label(self, obj: CatalogItem) -> str:
        return f"{obj.name} ({obj.catalog}:{obj.code})"

    async def get_by_catalog_code(self, catalog: str, code: str) -> CatalogItem | None:
        query = select(CatalogItem).where(CatalogItem.catalog == catalog, CatalogItem.code == code)
        return (await self.session.execute(query)).scalars().first()

    async def items_by_code(self, catalog: str) -> dict[str, CatalogItem]:
        query = select(CatalogItem).where(CatalogItem.catalog == catalog)
        return {i.code: i for i in (await self.session.execute(query)).scalars().all()}

    async def active_code_exists(self, catalog: str, code: str) -> bool:
        query = (
            select(CatalogItem.id)
            .where(
                CatalogItem.catalog == catalog,
                CatalogItem.code == code,
                CatalogItem.is_active.is_(True),
            )
            .limit(1)
        )
        return (await self.session.execute(query)).first() is not None

    async def active_codes(self, catalog: str) -> set[str]:
        query = select(CatalogItem.code).where(
            CatalogItem.catalog == catalog, CatalogItem.is_active.is_(True)
        )
        return set((await self.session.execute(query)).scalars().all())

    async def active_extras(self, catalog: str) -> dict[str, str | None]:
        """Active items as `code -> extra` (the link to the parent catalog, when it has one)."""
        query = select(CatalogItem.code, CatalogItem.extra).where(
            CatalogItem.catalog == catalog, CatalogItem.is_active.is_(True)
        )
        return {code: extra for code, extra in (await self.session.execute(query)).all()}

    async def name_of(self, catalog: str, code: str) -> str | None:
        query = select(CatalogItem.name).where(
            CatalogItem.catalog == catalog, CatalogItem.code == code
        )
        return (await self.session.execute(query)).scalars().first()

    async def next_sort_order(self, catalog: str) -> int:
        query = select(func.coalesce(func.max(CatalogItem.sort_order), 0)).where(
            CatalogItem.catalog == catalog
        )
        return int((await self.session.execute(query)).scalar_one()) + 1

    async def count_by_catalog(self) -> dict[str, int]:
        query = select(CatalogItem.catalog, func.count(CatalogItem.id)).group_by(
            CatalogItem.catalog
        )
        return {key: int(total) for key, total in (await self.session.execute(query)).all()}

    async def is_in_use(self, item: CatalogItem) -> bool:
        """True when another table still stores this item's code."""
        for model_path, column_name in CATALOG_USAGE.get(item.catalog, ()):
            column = _resolve_column(model_path, column_name)
            query = select(column).where(column == item.code).limit(1)
            if (await self.session.execute(query)).first() is not None:
                return True
        return False
