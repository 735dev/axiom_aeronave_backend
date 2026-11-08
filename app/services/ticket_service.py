"""Tickets: creation, edition, status moves, decisions, authorization and readings.

Everything that decides lives here (validation against the type, money, numbering, rules,
closing requirements). Persistence is the repository's; the controller only adapts HTTP.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi_basekit.exceptions.api_exceptions import NotFoundException

from app.config.settings import get_settings
from app.config.closing import ARRIVAL_KEY, SIGNATURE_STEP_CODE
from app.config.tickets import (
    AUTO_REPORT_PAYLOAD,
    AUTO_REPORT_ROLE_CODE,
    AUTO_REPORT_TITLE,
    AUTO_REPORT_TYPE_CODE,
    AIRCRAFTS_CATALOG,
    AIRPORTS_CATALOG,
    CONTROL_NUMBER_FIELD,
    CURRENCY_CATALOG,
    EXCHANGE_RATE_KEY,
    PERM_DECIDE_ANY,
    DecisionValue,
    ROUTE_DESTINATION_KEY,
    ROUTE_ORIGIN_KEY,
    ExtractionSource,
    ExtractionStatus,
    FieldRole,
    RelationKind,
    RuleAction,
)
from app.exceptions.tickets import TicketError
from app.integrations.receipt_reader import ReadHints
from app.models.ticket import Ticket, TicketExtraction, TicketType, TicketTypeStatus
from app.repositories.business_rule import BusinessRuleRepository
from app.repositories.catalog import CatalogItemRepository
from app.repositories.audit_trail import AuditEventRepository
from app.repositories.closing import TicketClosingRepository
from app.repositories.reconciliation import ReconciliationPeriodRepository
from app.repositories.role import RoleRepository
from app.repositories.ticket_support import TicketSupportRepository
from app.repositories.ticket import TicketRepository
from app.repositories.ticket_extraction import TicketExtractionRepository
from app.repositories.ticket_type import TicketTypeRepository
from app.repositories.user import UserRepository
from app.services.base import ProjectService
from app.services.period_lock import PeriodLockMixin
from app.services.ticket_hooks import CLOSE_CHECKS, TICKET_HOOKS, TicketContext, TicketEvent
from app.utils import numbering
from app.utils.money import Money, resolve_money, to_decimal
from app.utils.quick_log import flight_time_problem
from app.utils.review_gate import corrections as reading_corrections
from app.utils.review_gate import unreviewed_fields
from app.utils.rules_engine import (
    Facts,
    RuleLike,
    evaluate,
    merge_hits,
    missing_authorization,
    route_of,
)
from app.utils.ticket_access import can_create, can_edit
from app.utils.ticket_payload import (
    PayloadResult,
    catalogs_needed,
    missing_labels,
    option_values,
    validate_payload,
)

NO_EDIT_MESSAGE = "No tienes permiso para editar este tipo de registro."


def _money_of(ticket: Ticket) -> Money | None:
    if ticket.amount is None:
        return None
    return Money(
        amount=ticket.amount,
        currency=ticket.currency or "",
        currency_name=ticket.currency_name or "",
        rate=ticket.rate or Decimal(1),
        amount_base=ticket.amount_base or Decimal(0),
        base_currency=ticket.base_currency or "",
        rated_at=ticket.rated_at or datetime.now(timezone.utc),
        rate_source=ticket.rate_source or "base",
    )


def _money_columns(money: Money | None) -> dict[str, Any]:
    if money is None:
        return dict.fromkeys(
            (
                "amount",
                "currency",
                "currency_name",
                "rate",
                "amount_base",
                "base_currency",
                "rated_at",
                "rate_source",
            )
        )
    return {
        "amount": money.amount,
        "currency": money.currency,
        "currency_name": money.currency_name,
        "rate": money.rate,
        "amount_base": money.amount_base,
        "base_currency": money.base_currency,
        "rated_at": money.rated_at,
        "rate_source": money.rate_source,
    }


class TicketService(PeriodLockMixin, ProjectService[Ticket]):
    repository: TicketRepository
    periods: ReconciliationPeriodRepository
    closings: TicketClosingRepository
    supports: TicketSupportRepository
    types: TicketTypeRepository
    catalog: CatalogItemRepository
    roles: RoleRepository
    users: UserRepository
    rules: BusinessRuleRepository
    extractions: TicketExtractionRepository
    events: AuditEventRepository

    filter_whitelist = frozenset(
        {
            "code",
            "status_code",
            "type_code",
            "parent_id",
            "assigned_role_id",
            "assigned_user_id",
            "is_root",
            "open",
            "aircraft",
            "mine",
            "from",
            "to",
        }
    )
    search_fields = ["code", "title", "control_number"]
    list_budget = "heavy_list"

    # ------------------------------------------------------------------ filters

    def get_filters(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        incoming = super().get_filters(filters)
        applied: dict[str, Any] = {}
        for key, value in incoming.items():
            if key == "code":
                applied["code"] = str(value).strip()
            elif key == "status_code":
                applied["status__code"] = value
            elif key == "type_code":
                applied["type__code"] = value
            elif key == "is_root":
                applied["type__is_root"] = value
            elif key == "open":
                applied["status__is_final"] = not value
            elif key == "aircraft":
                applied["aircraft_code"] = str(value).strip().upper()
            elif key in ("from", "to"):
                suffix = "gte" if key == "from" else "lte"
                applied[f"accounting_date__{suffix}"] = self._date_filter(key, value)
            elif key == "mine":
                if value:
                    applied["mine"] = True
            else:
                applied[key] = value
        return applied

    @staticmethod
    def _date_filter(key: str, value: Any) -> date:
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            raise TicketError("validation", "Fecha no válida. Usa AAAA-MM-DD.", field=key) from None

    # ------------------------------------------------------------------ helpers

    @property
    def held(self) -> frozenset[str]:
        return self.principal.permissions

    async def _hooks(self, event: TicketEvent, ticket: Ticket, **extra: Any) -> None:
        context = TicketContext(self, ticket, ticket.type, event, extra)
        for hook in TICKET_HOOKS[event]:
            await hook(context)

    async def _reject_if_locked(self, ticket: Ticket, attempt: str) -> None:
        """A ticket in a final state is immutable, whoever asks (not even decide_any). So is
        an expense whose accounting period was closed by the reconciliation."""
        if ticket.status.is_final:
            await self.repository.record_denied(ticket, attempt)
            raise TicketError("locked", f"{ticket.code} está cerrado y no admite cambios.")
        await self._reject_if_period_closed(ticket, attempt)

    async def _scoped(self, record_id: UUID, lock: bool = False, fresh: bool = False) -> Ticket:
        ticket = (
            await self.repository.get_locked(record_id, fresh=fresh)
            if lock
            else await self.repository.get(record_id)
        )
        if ticket is None:
            raise NotFoundException(f"id={record_id} no encontrado")
        return ticket

    def _require_edit(self, ticket_type: TicketType) -> None:
        if not can_edit(self.held, ticket_type.code):
            raise TicketError("forbidden", NO_EDIT_MESSAGE)

    def _payload_failure(self, result: PayloadResult, ticket_type: TicketType) -> None:
        labels = ticket_type.field_labels()
        if result.errors:
            required = missing_labels(result.errors, labels)
            if required and len(required) == len(result.errors):
                detail = f"Faltan campos obligatorios: {', '.join(required)}."
            else:
                names = ", ".join(labels.get(k, k) for k in result.errors)
                detail = f"Revisa los campos marcados: {names}."
            raise TicketError("validation", detail, errors=result.errors)
        if result.forbidden:
            names = ", ".join(result.forbidden.values())
            raise TicketError(
                "forbidden",
                f"No tienes permiso para editar: {names}.",
                errors={k: "Sin permiso para editar este campo" for k in result.forbidden},
            )

    async def _catalogs_for(self, ticket_type: TicketType, keys: Any) -> dict[str, set[str]]:
        return {
            name: await self.catalog.active_codes(name)
            for name in catalogs_needed(ticket_type.fields, keys)
        }

    async def _checked_payload(
        self,
        ticket_type: TicketType,
        incoming: dict[str, Any],
        *,
        existing: dict[str, Any] | None,
        creating: bool,
        held: frozenset[str],
    ) -> PayloadResult:
        catalogs = await self._catalogs_for(ticket_type, incoming.keys())
        result = validate_payload(
            ticket_type.fields,
            incoming,
            existing=existing,
            creating=creating,
            held=held,
            catalogs=catalogs,
        )
        self._payload_failure(result, ticket_type)
        problem = flight_time_problem(result.payload)
        if problem:
            raise TicketError("validation", problem, field=ARRIVAL_KEY)
        return result

    # ------------------------------------------------------------------ derived values

    async def _value_money(
        self,
        ticket_type: TicketType,
        payload: dict[str, Any],
        parent: Ticket | None,
        previous: Money | None,
    ) -> Money | None:
        amount_field = ticket_type.field_by_role(FieldRole.AMOUNT.value)
        if amount_field is None:
            return None
        currency_field = ticket_type.field_by_role(FieldRole.CURRENCY.value)
        currency = payload.get(currency_field.key) if currency_field else None
        name = await self.catalog.name_of(CURRENCY_CATALOG, currency) if currency else None
        result = resolve_money(
            amount_value=payload.get(amount_field.key),
            currency=currency,
            currency_name=name,
            manual_rate=payload.get(EXCHANGE_RATE_KEY),
            parent_rate=(parent.payload or {}).get(EXCHANGE_RATE_KEY) if parent else None,
            previous=previous,
        )
        if result.error:
            raise TicketError("validation", result.error, field=amount_field.key)
        return result.money

    @staticmethod
    def _accounting_date(ticket_type: TicketType, payload: dict[str, Any], fallback: date) -> date:
        definition = ticket_type.field_by_role(FieldRole.ACCOUNTING_DATE.value)
        raw = payload.get(definition.key) if definition else None
        if not raw:
            return fallback
        try:
            return date.fromisoformat(str(raw))
        except ValueError:
            return fallback

    @staticmethod
    def _registration(ticket_type: TicketType, payload: dict[str, Any]) -> str | None:
        definition = ticket_type.field_by_role(FieldRole.AIRCRAFT.value)
        raw = payload.get(definition.key) if definition else None
        if not raw:
            return None
        registration = numbering.normalize_registration(raw)
        if registration is None:
            raise TicketError(
                "validation",
                "La matrícula solo admite letras y números, sin guiones ni espacios.",
                field=definition.key,
            )
        return registration

    async def _normalized_relations(
        self, ticket_type: TicketType, raw: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        allowed = set(ticket_type.relation_kinds or [])
        seen: set[tuple[str, str]] = set()
        relations: list[dict[str, Any]] = []
        for item in raw:
            kind, code = str(item["kind"]), str(item["code"]).strip()
            if kind not in allowed:
                raise TicketError(
                    "validation",
                    f"{ticket_type.name} no admite relaciones de tipo «{kind}».",
                    field="relations",
                )
            label = item.get("label")
            if kind in (RelationKind.AIRCRAFT.value, RelationKind.AIRPORT.value):
                name = await self.catalog.name_of(
                    AIRCRAFTS_CATALOG if kind == RelationKind.AIRCRAFT.value else AIRPORTS_CATALOG,
                    code,
                )
                if name is None:
                    raise TicketError(
                        "validation", f"La relación «{code}» no existe.", field="relations"
                    )
                label = name
            if (kind, code) in seen:
                continue
            seen.add((kind, code))
            relations.append({"kind": kind, "code": code, "label": label})
        return relations

    async def _derived_relations(
        self,
        ticket_type: TicketType,
        payload: dict[str, Any],
        aircraft_code: str | None,
        relations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Add what the payload already says: the aircraft and any airport field."""
        allowed = set(ticket_type.relation_kinds or [])
        have = {(r["kind"], r["code"]) for r in relations}
        derived: list[tuple[str, str, str]] = []
        if aircraft_code and RelationKind.AIRCRAFT.value in allowed:
            derived.append((RelationKind.AIRCRAFT.value, aircraft_code, AIRCRAFTS_CATALOG))
        if RelationKind.AIRPORT.value in allowed:
            for definition in ticket_type.fields:
                value = payload.get(definition.key)
                if definition.catalog == AIRPORTS_CATALOG and value:
                    derived.append((RelationKind.AIRPORT.value, str(value), AIRPORTS_CATALOG))
        result = list(relations)
        for kind, code, catalog in derived:
            if (kind, code) in have:
                continue
            name = await self.catalog.name_of(catalog, code)
            if name is not None:
                result.append({"kind": kind, "code": code, "label": name})
                have.add((kind, code))
        return result

    async def _check_role(self, role_id: UUID | None) -> None:
        if role_id is None:
            return
        role = await self.roles.get(role_id)
        if role is None or not role.is_active:
            raise TicketError("validation", "El rol responsable no es válido.", "assigned_role_id")

    async def _check_user(self, user_id: UUID | None) -> None:
        if user_id is None:
            return
        user = await self.users.get(user_id)
        if user is None or not user.is_active:
            raise TicketError(
                "validation", "El responsable puntual no es válido.", "assigned_user_id"
            )

    # ------------------------------------------------------------------ rules

    async def _rule_state(
        self,
        ticket_type: TicketType,
        ticket_id: UUID | None,
        parent: Ticket | None,
        payload: dict[str, Any],
        amount_base: Decimal | None,
        attachments: int,
        previous_hits: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Evaluate the active rules of the type over a (prospective) ticket state."""
        rules = [
            RuleLike(
                id=str(r.id),
                name=r.name,
                field=r.field,
                operator=r.operator,
                value=r.value,
                action=r.action,
                message=r.message,
                route_limits=r.route_limits,
            )
            for r in await self.rules.active_for_type(ticket_type.id)
        ]
        item = ticket_type.field_by_role(FieldRole.ITEM.value)
        route = None
        if parent is not None:
            route = route_of(
                (parent.payload or {}).get(ROUTE_ORIGIN_KEY),
                (parent.payload or {}).get(ROUTE_DESTINATION_KEY),
            )
        facts = Facts(
            amount_base=amount_base,
            attachments=attachments,
            concept=str(payload.get(item.key)) if item and payload.get(item.key) else None,
            route=route,
        )
        return merge_hits(previous_hits, evaluate(rules, facts))

    async def _record_new_hits(self, ticket: Ticket, new_hits: list[dict[str, Any]]) -> None:
        for hit in new_hits:
            await self.repository.record_rule_hit(ticket, hit)

    # ------------------------------------------------------------------ create

    async def create(self, payload, check_fields=None) -> Ticket:
        data = payload.model_dump() if hasattr(payload, "model_dump") else dict(payload)
        return await self._create_ticket(data, system=False)

    async def _create_ticket(self, data: dict[str, Any], *, system: bool) -> Ticket:
        code = str(data["type_code"])
        if not system and not can_create(self.held, code):
            raise TicketError("forbidden", "No tienes permiso para crear este tipo de registro.")
        ticket_type = await self.types.get_by_code(code)
        if ticket_type is None:
            raise TicketError("validation", "El tipo de ticket no existe.", "type_code")
        if not ticket_type.is_active:
            raise TicketError("validation", "Ese tipo de ticket está desactivado.", "type_code")

        parent: Ticket | None = None
        if data.get("parent_id"):
            parent = (
                await self.repository.get_internal(data["parent_id"])
                if system
                else await self._scoped(data["parent_id"], lock=True)
            )
            if parent is None:
                raise NotFoundException("parent no encontrado")
            await self._reject_if_locked(parent, "agregar trabajo a")
            if ticket_type.is_root:
                raise TicketError(
                    "validation", "Una operación no puede colgar de otro ticket.", "parent_id"
                )
            if ticket_type.code not in (parent.type.child_type_codes or []):
                raise TicketError(
                    "validation",
                    f"{parent.type.name} no admite «{ticket_type.name}» como trabajo asociado.",
                    "parent_id",
                )

        held = (
            frozenset(f.edit_permission for f in ticket_type.fields if f.edit_permission)
            if system
            else self.held
        )
        checked = await self._checked_payload(
            ticket_type, dict(data.get("payload") or {}), existing=None, creating=True, held=held
        )
        body = checked.payload

        money = await self._value_money(ticket_type, body, parent, None)
        accounting = self._accounting_date(ticket_type, body, numbering.business_today())
        await self._reject_if_date_in_closed_period(
            ticket_type, accounting, data.get("title") or ticket_type.name, "registrar un gasto en"
        )
        yy = numbering.year_suffix(accounting)
        registration = self._registration(ticket_type, body)

        await self._check_role(data.get("assigned_role_id"))
        await self._check_user(data.get("assigned_user_id"))
        role_id = data.get("assigned_role_id") or ticket_type.default_role_id

        relations = await self._normalized_relations(
            ticket_type, [dict(r) for r in data.get("relations") or []]
        )
        aircraft_code = registration or next(
            (r["code"] for r in relations if r["kind"] == RelationKind.AIRCRAFT.value),
            parent.aircraft_code if parent else None,
        )
        relations = await self._derived_relations(ticket_type, body, aircraft_code, relations)

        if ticket_type.is_root and registration:
            number = await self.repository.next_number(numbering.operation_series(registration, yy))
            ticket_code = numbering.operation_code(registration, number, yy)
        else:
            ticket_code = numbering.ticket_code(
                await self.repository.next_number(numbering.ticket_series())
            )
        control = None
        if ticket_type.is_root:
            control = numbering.control_number(
                await self.repository.next_number(numbering.control_series(yy)), yy
            )
            if any(f.key == CONTROL_NUMBER_FIELD for f in ticket_type.fields):
                body = {**body, CONTROL_NUMBER_FIELD: control}

        hits, new_hits = await self._rule_state(
            ticket_type,
            None,
            parent,
            body,
            money.amount_base if money else None,
            0,
            [],
        )
        actor = self.principal
        created = await super().create(
            {
                "code": ticket_code,
                "control_number": control,
                "type_id": ticket_type.id,
                "title": data.get("title") or ticket_type.name,
                "status_id": ticket_type.initial_status.id,
                "parent_id": parent.id if parent else None,
                "assigned_role_id": role_id,
                "assigned_user_id": data.get("assigned_user_id"),
                "created_by_id": actor.user_id,
                "created_by_name": actor.full_name,
                "due_at": data.get("due_at"),
                "payload": body,
                "relations": relations,
                "accounting_date": accounting,
                "aircraft_code": aircraft_code,
                "rule_hits": hits,
                **_money_columns(money),
            }
        )
        await self._record_new_hits(created, new_hits)
        await self._hooks(TicketEvent.CREATED, created, system=system)
        if ticket_type.is_root and AUTO_REPORT_TYPE_CODE in (ticket_type.child_type_codes or []):
            await self._open_technical_report(created)
        return created

    async def _open_technical_report(self, operation: Ticket) -> None:
        """Every operation carries its technical report from birth, finding or not: it is
        left open, assigned to the pilot, and blocks the close until someone resolves it."""
        report_type = await self.types.get_by_code(AUTO_REPORT_TYPE_CODE)
        if report_type is None or not report_type.is_active:
            return
        pilot = await self.roles.get_by_code(AUTO_REPORT_ROLE_CODE)
        await self._create_ticket(
            {
                "type_code": AUTO_REPORT_TYPE_CODE,
                "title": AUTO_REPORT_TITLE.format(code=operation.code),
                "parent_id": operation.id,
                "assigned_role_id": pilot.id if pilot and pilot.is_active else None,
                "payload": dict(AUTO_REPORT_PAYLOAD),
            },
            system=True,
        )

    # ------------------------------------------------------------------ update

    async def update(self, id, data) -> Ticket:  # noqa: A002
        body = data.model_dump(exclude_unset=True) if hasattr(data, "model_dump") else dict(data)
        ticket = await self._scoped(id, lock=True)
        ticket_type = ticket.type
        self._require_edit(ticket_type)
        await self._reject_if_locked(ticket, "modificar")

        values: dict[str, Any] = {}
        followups: list[Any] = []
        payload_after = dict(ticket.payload or {})
        money_after = _money_of(ticket)
        before_status = ticket.status

        if body.get("title") is not None:
            values["title"] = body["title"].strip()
        if "due_at" in body:
            values["due_at"] = body["due_at"]

        if body.get("payload") is not None:
            payload_after, money_after = await self._apply_payload(
                ticket, ticket_type, body["payload"], values
            )

        if "relations" in body and body["relations"] is not None:
            relations = await self._normalized_relations(
                ticket_type, [dict(r) for r in body["relations"]]
            )
            values["relations"] = await self._derived_relations(
                ticket_type,
                payload_after,
                values.get("aircraft_code", ticket.aircraft_code),
                relations,
            )

        await self._reassign(ticket, body, values, followups)

        parent = await self.repository.get_internal(ticket.parent_id) if ticket.parent_id else None
        hits, new_hits = await self._rule_state(
            ticket_type,
            ticket.id,
            parent,
            payload_after,
            money_after.amount_base if money_after else None,
            ticket.attachments_count,
            ticket.rule_hits or [],
        )
        if hits != (ticket.rule_hits or []):
            values["rule_hits"] = hits

        target: TicketTypeStatus | None = None
        if body.get("status_code") and body["status_code"] != before_status.code:
            target = await self._plan_status(
                ticket, ticket_type, body["status_code"], hits, money_after
            )
            values["status_id"] = target.id
            values["closed_at"] = datetime.now(timezone.utc) if target.is_final else None

        updated = await super().update(ticket.id, values)
        if target is not None:
            await self.repository.record_status_change(updated, before_status.name, target.name)
        for followup in followups:
            await followup(updated)
        if body.get("comment"):
            await self.repository.add_comment(
                updated, self.principal.user_id, self.principal.full_name, body["comment"].strip()
            )
            await self.repository.record_comment(updated, body["comment"].strip())
        await self._record_new_hits(updated, new_hits)
        await self._hooks(TicketEvent.UPDATED, updated)
        if target is not None:
            await self._hooks(
                TicketEvent.STATUS_CHANGED, updated, previous_status=before_status.code
            )
        return updated

    async def _apply_payload(
        self,
        ticket: Ticket,
        ticket_type: TicketType,
        incoming: dict[str, Any],
        values: dict[str, Any],
        held: frozenset[str] | None = None,
    ) -> tuple[dict[str, Any], Money | None]:
        """Validate and merge a payload change into `values`. Returns the new payload and
        the money it resolves to (the frozen one when amount and currency did not move)."""
        checked = await self._checked_payload(
            ticket_type,
            incoming,
            existing=dict(ticket.payload or {}),
            creating=False,
            held=held if held is not None else self.held,
        )
        merged = checked.payload
        aircraft = ticket_type.field_by_role(FieldRole.AIRCRAFT.value)
        if aircraft and aircraft.key in checked.changed:
            if ticket_type.is_root:
                raise TicketError(
                    "validation",
                    "La matrícula de una operación no se puede cambiar: anúlala y crea otra.",
                    field=aircraft.key,
                )
            registration = self._registration(ticket_type, merged)
            values["aircraft_code"] = registration
        parent = await self.repository.get_internal(ticket.parent_id) if ticket.parent_id else None
        # The rate stays frozen: editing a note must not revalue an old expense.
        money = await self._value_money(ticket_type, merged, parent, _money_of(ticket))
        values["payload"] = merged
        values.update(_money_columns(money))
        date_field = ticket_type.field_by_role(FieldRole.ACCOUNTING_DATE.value)
        if date_field and date_field.key in checked.changed:
            created_day = ticket.created_at.astimezone(
                ZoneInfo(get_settings().BUSINESS_TIMEZONE)
            ).date()
            values["accounting_date"] = self._accounting_date(ticket_type, merged, created_day)
            await self._reject_if_date_in_closed_period(
                ticket_type,
                values["accounting_date"],
                ticket.code,
                "mover a un período cerrado",
            )
        return merged, money

    async def _reassign(
        self, ticket: Ticket, body: dict[str, Any], values: dict[str, Any], followups: list[Any]
    ) -> None:
        if "assigned_role_id" in body and body["assigned_role_id"] != ticket.assigned_role_id:
            await self._check_role(body["assigned_role_id"])
            before = ticket.assigned_role_name
            values["assigned_role_id"] = body["assigned_role_id"]
            new_id = body["assigned_role_id"]

            async def note_role(updated: Ticket, before=before, new_id=new_id) -> None:
                await self.repository.record_reassignment(
                    updated, "assigned_role", "Responsable", before, updated.assigned_role_name
                )

            followups.append(note_role)
        if "assigned_user_id" in body and body["assigned_user_id"] != ticket.assigned_user_id:
            await self._check_user(body["assigned_user_id"])
            before_user = ticket.assigned_user_name
            values["assigned_user_id"] = body["assigned_user_id"]

            async def note_user(updated: Ticket, before=before_user) -> None:
                await self.repository.record_reassignment(
                    updated,
                    "assigned_user",
                    "Responsable puntual",
                    before,
                    updated.assigned_user_name,
                )

            followups.append(note_user)

    async def _plan_status(
        self,
        ticket: Ticket,
        ticket_type: TicketType,
        code: str,
        hits: list[dict[str, Any]],
        money: Money | None,
    ) -> TicketTypeStatus:
        target = ticket_type.status_by_code(code)
        if target is None:
            raise TicketError("validation", "Ese estado no existe para este tipo.", "status_code")
        allowed = ticket.status.next_codes
        if allowed is not None and target.code not in allowed:
            raise TicketError(
                "validation",
                f"No se puede pasar de «{ticket.status.name}» a «{target.name}».",
                "status_code",
            )
        if target.is_final:
            await self._check_closing(ticket, ticket_type, target, hits, money)
        return target

    async def _check_closing(
        self,
        ticket: Ticket,
        ticket_type: TicketType,
        target: TicketTypeStatus,
        hits: list[dict[str, Any]],
        money: Money | None,
    ) -> None:
        """What must hold before a ticket reaches a final state that counts.

        Rejecting or cancelling is always possible: demanding the whole circuit to refuse
        something would force people to fabricate approvals.
        """
        if not target.counts_as_committed:
            return
        if ticket_type.is_root:
            blockers = await self.repository.unresolved_blockers(ticket.id)
            if blockers:
                await self._hooks(TicketEvent.CLOSE_BLOCKED, ticket, blockers=blockers)
                codes = ", ".join(b.code for b in blockers)
                raise TicketError(
                    "validation", f"No se puede cerrar: {codes} sigue(n) sin resolver."
                )
        if missing_authorization(hits, ticket.authorization, money.amount_base if money else None):
            raise TicketError(
                "validation",
                "Una regla exige la autorización de la dirección antes de dar por bueno este gasto.",
            )
        done = {d.step_code for d in ticket.decisions}
        pending = [s.name for s in ticket_type.steps if s.required and s.code not in done]
        if pending:
            raise TicketError("validation", f"Faltan pasos por registrar: {', '.join(pending)}.")
        if await self.extractions.has_pending_doubt(ticket.id):
            raise TicketError(
                "validation",
                "Hay lecturas de comprobantes con datos de baja confianza sin validar.",
            )
        context = TicketContext(
            self, ticket, ticket_type, TicketEvent.STATUS_CHANGED, {"target_status": target}
        )
        for check in CLOSE_CHECKS:
            message = await check(context)
            if message:
                raise TicketError("validation", message)

    # ------------------------------------------------------------------ decisions

    async def decide(self, id, data) -> Ticket:  # noqa: A002
        body = data.model_dump() if hasattr(data, "model_dump") else dict(data)
        ticket = await self._scoped(id, lock=True)
        ticket_type = ticket.type
        self._require_edit(ticket_type)
        step = ticket_type.step_by_code(body["step_code"])
        if step is None:
            raise TicketError("validation", "Ese paso no existe para este tipo.", "step_code")
        await self._reject_if_locked(ticket, "registrar una decisión en")
        principal = self.principal
        if (
            step.role_id is not None
            and step.role_id != principal.role_id
            and PERM_DECIDE_ANY not in principal.permissions
        ):
            raise TicketError(
                "forbidden", "Ese paso le corresponde a otro rol; no puedes registrarlo."
            )
        # Only the fulfilled signature step carries the signer's signature image.
        signature = (
            await self.users.get_signature(principal.user_id)
            if str(body["decision"]) == DecisionValue.DONE.value
            and step.code == SIGNATURE_STEP_CODE
            else None
        )
        previous, current = await self.repository.upsert_decision(
            ticket,
            step.code,
            step.name,
            str(body["decision"]),
            body.get("observation"),
            principal.user_id,
            principal.full_name,
            signature or None,
        )
        await self.repository.record_decision(ticket, step.name, previous, current)
        await self._advance_after_decision(ticket, ticket_type)
        await self._hooks(TicketEvent.DECISION, ticket, step_code=step.code)
        return ticket

    async def _advance_after_decision(self, ticket: Ticket, ticket_type: TicketType) -> None:
        """The first decision moves a ticket out of its initial status into the first
        in-progress one, so nobody has to remember to do it by hand."""
        if not ticket.status.is_initial:
            return
        progress = next(
            (s for s in ticket_type.statuses if not s.is_initial and not s.is_final), None
        )
        if progress is None:
            return
        allowed = ticket.status.next_codes
        if allowed is not None and progress.code not in allowed:
            return
        before = ticket.status.name
        await self.repository.touch(ticket, status_id=progress.id)
        await self.repository.record_status_change(ticket, before, progress.name)

    # ------------------------------------------------------------------ authorization

    async def authorize(self, id, data) -> Ticket:  # noqa: A002
        """Management approval of an expense a rule flagged, tied to the amount approved."""
        note = (data.model_dump() if hasattr(data, "model_dump") else dict(data)).get("note")
        ticket = await self._scoped(id, lock=True)
        await self._reject_if_locked(ticket, "autorizar")
        if not any(
            h.get("action") == RuleAction.REQUIRE_AUTHORIZATION for h in ticket.rule_hits or []
        ):
            raise TicketError("validation", "Este registro no necesita autorización.")
        actor = self.principal
        approved = ticket.amount_base or Decimal(0)
        stamp = {
            "by": actor.full_name,
            "by_id": str(actor.user_id),
            "role": actor.role_name,
            "at": datetime.now(timezone.utc).isoformat(),
            "note": note,
            "amount_base": float(approved),
        }
        await self.repository.touch(ticket, authorization=stamp)
        await self.repository.record_authorization(
            ticket,
            actor.full_name,
            f"autorizó {ticket.code} ({approved} {ticket.base_currency or ''})".rstrip(),
        )
        await self._hooks(TicketEvent.AUTHORIZED, ticket)
        return ticket

    # ------------------------------------------------------------------ attachments support
    # Public entry points of the attachments module (it owns the files, not the ticket rules).

    async def visible_ticket(self, id) -> Ticket:  # noqa: A002
        """A ticket the caller may view; anything else is a 404."""
        return await self._scoped(id)

    async def open_for_change(self, id, attempt: str, fresh: bool = False) -> Ticket:  # noqa: A002
        """Lock a ticket for a change by someone who may edit its type. A closed one is
        refused (409) and the attempt is audited as `denied`. `fresh` re-reads the row (a
        second lock on the same request after a commit)."""
        ticket = await self._scoped(id, lock=True, fresh=fresh)
        self._require_edit(ticket.type)
        await self._reject_if_locked(ticket, attempt)
        return ticket

    def may_edit_type(self, ticket_type: TicketType) -> bool:
        return can_edit(self.held, ticket_type.code) or can_create(self.held, ticket_type.code)

    async def reevaluate_rules(self, ticket: Ticket) -> None:
        """Re-run the rules after something they read changed (the attachment count)."""
        parent = await self.repository.get_internal(ticket.parent_id) if ticket.parent_id else None
        hits, new_hits = await self._rule_state(
            ticket.type,
            ticket.id,
            parent,
            dict(ticket.payload or {}),
            ticket.amount_base,
            ticket.attachments_count,
            ticket.rule_hits or [],
        )
        changed = {"rule_hits": hits} if hits != (ticket.rule_hits or []) else {}
        await self.repository.touch(ticket, **changed)
        await self._record_new_hits(ticket, new_hits)

    async def fire(self, event: TicketEvent, ticket: Ticket, **extra: Any) -> None:
        """Run the hooks of an event raised by another module (attachments)."""
        await self._hooks(event, ticket, **extra)

    async def reading_hints(self, ticket_type: TicketType) -> ReadHints | None:
        """What a reader may propose for this type; None when the type records no amount."""
        amount = ticket_type.field_by_role(FieldRole.AMOUNT.value)
        if amount is None:
            return None
        by_role = {role: ticket_type.field_by_role(role.value) for role in FieldRole}
        item = by_role[FieldRole.ITEM]
        concepts = (
            sorted(await self.catalog.active_codes(item.catalog)) if item and item.catalog else []
        )
        if item and not item.catalog:
            concepts = sorted(option_values(item.options))

        def key_of(role: FieldRole, default: str) -> str:
            found = by_role[role]
            return found.key if found else default

        return ReadHints(
            currencies=tuple(sorted(await self.catalog.active_codes(CURRENCY_CATALOG))),
            concept_codes=tuple(concepts),
            amount_key=amount.key,
            currency_key=key_of(FieldRole.CURRENCY, "currency"),
            item_key=key_of(FieldRole.ITEM, "item"),
            accounting_date_key=key_of(FieldRole.ACCOUNTING_DATE, "accounting_date"),
            supplier_key=key_of(FieldRole.SUPPLIER, "supplier"),
        )

    async def checked_reading(
        self, ticket_type: TicketType, proposed: list[tuple[str, Any, str]]
    ) -> list[dict[str, Any]]:
        """Run machine-proposed values through the same validation as a form: what the type
        would reject is dropped (never guessed), the rest is normalized."""
        definitions = {f.key for f in ticket_type.fields}
        everything = frozenset(f.edit_permission for f in ticket_type.fields if f.edit_permission)
        kept: list[dict[str, Any]] = []
        for key, value, confidence in proposed:
            if key not in definitions or value is None:
                continue
            catalogs = await self._catalogs_for(ticket_type, [key])
            result = validate_payload(
                ticket_type.fields,
                {key: value},
                existing=None,
                creating=False,
                held=everything,
                catalogs=catalogs,
            )
            if result.errors or result.forbidden or key not in result.payload:
                continue
            kept.append({"key": key, "value": result.payload[key], "confidence": confidence})
        return kept

    # ------------------------------------------------------------------ history

    async def history(self, id, page: int, count: int):  # noqa: A002
        ticket = await self._scoped(id)
        return await self.events.list_paginated(
            page=page,
            count=count,
            filters={"entity": "ticket", "entity_id": str(ticket.id)},
        )

    # ------------------------------------------------------------------ readings

    async def list_readings(self, id) -> list[TicketExtraction]:  # noqa: A002
        ticket = await self._scoped(id)
        return await self.extractions.for_ticket(ticket.id)

    async def propose_reading(self, id, data) -> TicketExtraction:  # noqa: A002
        ticket = await self._scoped(id, lock=True)
        self._require_edit(ticket.type)
        await self._reject_if_locked(ticket, "registrar una lectura en")
        body = data.model_dump(mode="json") if hasattr(data, "model_dump") else dict(data)
        return await self.register_reading(
            ticket, body["fields"], ExtractionSource.MANUAL, self.principal.user_id
        )

    async def register_reading(
        self,
        ticket: Ticket,
        fields: list[dict[str, Any]],
        source: ExtractionSource,
        created_by: UUID | None = None,
        attachment_id: UUID | None = None,
        status: ExtractionStatus = ExtractionStatus.EXTRACTED,
    ) -> TicketExtraction:
        """Store a reading as a proposal. Public: the attachments/OCR module calls it."""
        definitions = {f.key: f for f in ticket.type.fields}
        stored: list[dict[str, Any]] = []
        for item in fields:
            definition = definitions.get(item["key"])
            if definition is None or definition.type == "file" or definition.read_only:
                raise TicketError(
                    "validation", "La lectura trae un campo que no existe.", item["key"]
                )
            stored.append(
                {
                    "key": item["key"],
                    "label": definition.label,
                    "value": item["value"],
                    "confidence": str(item.get("confidence", "high")),
                }
            )
        if len({s["key"] for s in stored}) != len(stored):
            raise TicketError("validation", "La lectura repite un campo.", "fields")
        reading = await self.extractions.create(
            {
                "ticket_id": ticket.id,
                "attachment_id": attachment_id,
                "source": source.value,
                "status": status.value,
                "fields": stored,
                "corrections": [],
                "created_by_id": created_by,
            }
        )
        await self.repository.record_reading(ticket, len(stored), source.value)
        return reading

    async def validate_reading(self, id, reading_id, data) -> Ticket:  # noqa: A002
        """A person approves what the machine read. The review gate is enforced here."""
        body = data.model_dump(mode="json") if hasattr(data, "model_dump") else dict(data)
        ticket = await self._scoped(id, lock=True)
        ticket_type = ticket.type
        self._require_edit(ticket_type)
        await self._reject_if_locked(ticket, "validar un comprobante de")
        reading = await self.extractions.get_of_ticket(ticket.id, reading_id)
        if reading is None:
            raise NotFoundException("lectura no encontrada")
        if reading.status != ExtractionStatus.EXTRACTED.value:
            raise TicketError("validation", "Esa lectura no está pendiente de validación.")

        proposal = reading.fields
        known = {f["key"] for f in proposal}
        approved = {a["key"]: a["value"] for a in body.get("fields") or []}
        unknown = sorted((set(approved) | set(body.get("confirmed") or [])) - known)
        if unknown:
            raise TicketError(
                "validation",
                "La validación nombra campos que la lectura no tiene.",
                errors={k: "Campo ajeno a la lectura" for k in unknown},
            )
        pending = unreviewed_fields(proposal, approved, body.get("confirmed") or [])
        if pending:
            names = ", ".join(f["label"] for f in pending)
            raise TicketError(
                "validation",
                f"Revisa antes de aprobar: {names} (confianza baja).",
                errors={f["key"]: "Revisa este dato antes de aprobar" for f in pending},
            )

        final = {f["key"]: approved.get(f["key"], f["value"]) for f in proposal}
        values: dict[str, Any] = {}
        payload_after, money_after = await self._apply_payload(ticket, ticket_type, final, values)
        parent = await self.repository.get_internal(ticket.parent_id) if ticket.parent_id else None
        hits, new_hits = await self._rule_state(
            ticket_type,
            ticket.id,
            parent,
            payload_after,
            money_after.amount_base if money_after else None,
            ticket.attachments_count,
            ticket.rule_hits or [],
        )
        if hits != (ticket.rule_hits or []):
            values["rule_hits"] = hits
        changes = reading_corrections(proposal, approved)
        updated_fields = [{**f, "value": final[f["key"]]} for f in proposal]
        await self.extractions.mark_validated(
            reading, updated_fields, changes, self.principal.full_name
        )
        updated = await super().update(ticket.id, values)
        await self.repository.record_validation(updated, changes, ticket_type.field_labels())
        await self._record_new_hits(updated, new_hits)
        await self._hooks(TicketEvent.READING_VALIDATED, updated, reading_id=str(reading.id))
        return updated
