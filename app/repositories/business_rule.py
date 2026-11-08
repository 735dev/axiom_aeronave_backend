"""Business rules declared by the administrator."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.sql import Select

from app.config.audit import AuditEntity
from app.models.ticket import BusinessRule
from app.repositories.base import AuditedRepository


class BusinessRuleRepository(AuditedRepository[BusinessRule]):
    model = BusinessRule
    default_ordering = ("created_at",)
    # Rules are reported under the ticket-type entity, like the reference does.
    audit_entity = AuditEntity.TICKET_TYPE
    audit_labels = {
        "name": "Nombre",
        "field": "Campo",
        "operator": "Operador",
        "value": "Valor",
        "action": "Acción",
        "message": "Mensaje",
        "route_limits": "Topes por ruta",
        "is_active": "Activa",
    }

    def _subject(self) -> str:
        return "la regla"

    def audit_label(self, obj: BusinessRule) -> str:
        return obj.name

    def build_list_queryset(self, **kwargs: Any) -> Select:
        return select(BusinessRule).order_by(BusinessRule.created_at, BusinessRule.id)

    async def active_for_type(self, type_id: Any) -> list[BusinessRule]:
        query = (
            select(BusinessRule)
            .where(BusinessRule.type_id == type_id, BusinessRule.is_active.is_(True))
            .order_by(BusinessRule.created_at, BusinessRule.id)
        )
        return list((await self.session.execute(query)).scalars().all())

    async def exists(self, type_id: Any, name: str) -> bool:
        query = select(BusinessRule.id).where(
            BusinessRule.type_id == type_id, BusinessRule.name == name
        )
        return (await self.session.execute(query.limit(1))).first() is not None
