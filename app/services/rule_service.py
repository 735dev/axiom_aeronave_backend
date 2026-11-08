"""Business rules declared by the administrator."""

from __future__ import annotations

from typing import Any

from fastapi_basekit.exceptions.api_exceptions import NotFoundException

from app.config.tickets import RULE_FIELD_OPERATORS, ROUTE_PATTERN, RuleField, RuleOperator
from app.exceptions.tickets import TicketError
from app.models.ticket import BusinessRule
from app.repositories.business_rule import BusinessRuleRepository
from app.repositories.ticket_type import TicketTypeRepository
from app.services.base import ProjectService
from app.utils.money import to_decimal


class RuleService(ProjectService[BusinessRule]):
    repository: BusinessRuleRepository
    types: TicketTypeRepository

    filter_whitelist = frozenset({"type_code", "is_active", "field", "action"})
    search_fields = ["name", "message"]

    @staticmethod
    def _validate(values: dict[str, Any]) -> dict[str, Any]:
        """Check the rule is coherent and return the columns to store."""
        field = RuleField(values["field"])
        operator = values.get("operator")
        if field == RuleField.AMOUNT_VS_ROUTE:
            operator = RuleOperator.GT.value
        if operator is None:
            raise TicketError("validation", "Indica el operador de la regla.", "operator")
        if RuleOperator(operator) not in RULE_FIELD_OPERATORS[field]:
            raise TicketError("validation", "Ese operador no aplica al campo elegido.", "operator")
        out = {**values, "operator": operator}
        if field == RuleField.AMOUNT_VS_ROUTE:
            limits = values.get("route_limits") or {}
            if not limits:
                raise TicketError("validation", "Define al menos un tope por ruta.", "route_limits")
            for route, limit in limits.items():
                if not ROUTE_PATTERN.match(route):
                    raise TicketError(
                        "validation",
                        f"La ruta «{route}» no es válida. Usa el formato ORIGEN-DESTINO.",
                        "route_limits",
                    )
                if limit <= 0:
                    raise TicketError(
                        "validation",
                        "Los topes por ruta deben ser mayores que cero.",
                        "route_limits",
                    )
            out["value"] = None
        else:
            out["route_limits"] = None
            if field == RuleField.ATTACHMENTS:
                out["value"] = None
            elif values.get("value") in (None, ""):
                raise TicketError("validation", "Indica el valor de la regla.", "value")
            elif field == RuleField.AMOUNT_BASE:
                number = to_decimal(values["value"])
                if number is None or number < 0:
                    raise TicketError(
                        "validation", "El valor debe ser un número no negativo.", "value"
                    )
                out["value"] = format(number, "f")
            else:
                out["value"] = str(values["value"]).strip()
        return out

    async def create(self, payload, check_fields=None) -> BusinessRule:
        body = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else dict(payload)
        ticket_type = await self.types.get_by_code(body["type_code"])
        if ticket_type is None:
            raise TicketError("validation", "El tipo de ticket no existe.", "type_code")
        stored = self._validate(body)
        return await super().create(
            {
                "type_id": ticket_type.id,
                "name": stored["name"].strip(),
                "field": stored["field"],
                "operator": stored["operator"],
                "value": stored["value"],
                "action": stored["action"],
                "message": stored["message"].strip(),
                "route_limits": stored["route_limits"],
                "is_active": stored["is_active"],
            }
        )

    async def update(self, id, data) -> BusinessRule:  # noqa: A002
        body = (
            data.model_dump(mode="json", exclude_unset=True)
            if hasattr(data, "model_dump")
            else dict(data)
        )
        rule = await self.repository.get(id)
        if rule is None:
            raise NotFoundException(f"id={id} no encontrado")
        merged = {
            "name": rule.name,
            "field": rule.field,
            "operator": rule.operator,
            "value": rule.value,
            "action": rule.action,
            "message": rule.message,
            "route_limits": rule.route_limits,
            "is_active": rule.is_active,
        }
        for key, value in body.items():
            if value is None and key in ("name", "field", "action", "message", "is_active"):
                raise TicketError("validation", "Este campo no admite valores vacíos.", key)
            merged[key] = value
        if "field" in body and body["field"] != rule.field:
            # A different field invalidates the operator and value it had.
            merged["operator"] = body.get("operator")
            merged["value"] = body.get("value")
            merged["route_limits"] = body.get("route_limits")
        stored = self._validate(merged)
        stored["name"] = stored["name"].strip()
        stored["message"] = stored["message"].strip()
        return await super().update(id, stored)
