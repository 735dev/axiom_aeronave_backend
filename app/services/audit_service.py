from __future__ import annotations

import json
from datetime import timezone
from typing import Any

from app.config.audit import (
    EXPORT_FILENAME,
    EXPORT_HEADERS,
    EXPORT_TOO_MANY_MESSAGE,
    AuditAction,
    AuditEntity,
)
from app.config.settings import get_settings
from app.repositories.audit_trail import AuditEntry
from app.utils import csv_export
from app.utils.numbering import business_today
from app.exceptions.admin import AdminError
from app.models.audit import AuditEvent
from app.services.base import ProjectService
from app.utils.dates import end_of_day_exclusive, parse_range_bound


class AuditService(ProjectService[AuditEvent]):
    """Read side of the trail. There is deliberately no create/update/delete here."""

    filter_whitelist = frozenset({"entity", "entity_id", "action", "actor_id"})
    # Free text is handled by the repository (it also looks inside the diff).
    search_fields: list[str] = []
    list_budget = "audit_search"

    def get_filters(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        incoming = dict(filters or {})
        applied = super().get_filters(incoming)
        self._validate_choice(applied, "entity", AuditEntity)
        self._validate_choice(applied, "action", AuditAction)
        if incoming.get("from"):
            applied["at__gte"] = self._bound("from", str(incoming["from"]))[0]
        if incoming.get("to"):
            moment, date_only = self._bound("to", str(incoming["to"]))
            if date_only:
                applied["at__lt"] = end_of_day_exclusive(moment)
            else:
                applied["at__lte"] = moment
        return applied

    @staticmethod
    def _validate_choice(filters: dict[str, Any], key: str, enum: Any) -> None:
        if key in filters and filters[key] not in {member.value for member in enum}:
            raise AdminError("validation", "Valor de filtro no válido.", field=key)

    @staticmethod
    def _bound(field: str, raw: str):
        try:
            return parse_range_bound(raw)
        except ValueError:
            raise AdminError(
                "validation", "Fecha no válida. Usa AAAA-MM-DD o una fecha ISO.", field=field
            ) from None

    async def export_csv(self, raw_filters: dict[str, Any], search: str | None) -> tuple[str, str]:
        """(filename, text). Same filters as the listing; oldest first. Leaves an `export`
        event (who took which slice of the trail out), written AFTER the read so the file
        never contains its own export."""
        await self.throttle("export")
        filters = self.get_filters(raw_filters)
        limit = get_settings().AUDIT_EXPORT_MAX_ROWS
        rows = await self.repository.export_rows(filters, (search or "").strip() or None, limit + 1)
        if len(rows) > limit:
            raise AdminError(
                "validation", EXPORT_TOO_MANY_MESSAGE.format(limit=limit), field="from"
            )
        text = csv_export.build_csv(
            EXPORT_HEADERS,
            (
                [
                    str(e.seq),
                    e.at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    e.actor_name,
                    e.actor_role,
                    e.action,
                    e.entity,
                    e.entity_id,
                    e.entity_label,
                    e.summary,
                    json.dumps(e.changes, ensure_ascii=False, separators=(",", ":")),
                    e.ip,
                ]
                for e in rows
            ),
        )
        applied = ", ".join(f"{k}={v}" for k, v in sorted(self._describe(raw_filters, search)))
        await self.repository.append(
            AuditEntry(
                action=AuditAction.EXPORT,
                entity=AuditEntity.AUDIT,
                entity_id="export",
                entity_label="Exportación de bitácora",
                summary=f"Exportó {len(rows)} evento(s) de la bitácora",
                changes=[
                    {"field": "rows", "label": "Eventos exportados", "from": None, "to": len(rows)},
                    {
                        "field": "filters",
                        "label": "Filtros",
                        "from": None,
                        "to": applied or "ninguno",
                    },
                ],
            )
        )
        return EXPORT_FILENAME.format(day=business_today().strftime("%Y%m%d")), text

    @staticmethod
    def _describe(raw: dict[str, Any], search: str | None):
        shown = {k: v for k, v in raw.items() if v not in (None, "")}
        if search:
            shown["search"] = search
        return [(k, str(v)[:60]) for k, v in shown.items()]
