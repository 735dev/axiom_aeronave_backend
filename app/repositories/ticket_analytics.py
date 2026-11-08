"""Expense totals: one named aggregation shared by `GET /tickets/totals/` and the closing.

The rows summed are exactly the rows of the listing: same scoped base query, same filters,
same search. If they diverged the total would not match what the user sees.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, select

from app.config.closing import UNCLASSIFIED_KEY, UNCLASSIFIED_LABEL
from app.config.tickets import BASE_CURRENCY, FieldRole
from app.models.catalog import CatalogItem
from app.models.ticket import Ticket, TicketType, TicketTypeField, TicketTypeStatus
from app.repositories.ticket import TicketRepository

ZERO = Decimal(0)
_CENT = Decimal("0.01")


def _money(value: Any) -> Decimal:
    return Decimal(value or 0).quantize(_CENT)


@dataclass
class CurrencyTotal:
    currency: str
    currency_name: str | None
    amount: Decimal = ZERO
    amount_base: Decimal = ZERO
    count: int = 0


@dataclass
class ConceptTotal:
    key: str
    label: str
    amount_base: Decimal = ZERO
    count: int = 0


@dataclass
class Totals:
    base_currency: str = BASE_CURRENCY
    committed_base: Decimal = ZERO
    pending_base: Decimal = ZERO
    excluded_base: Decimal = ZERO
    by_currency: list[CurrencyTotal] = field(default_factory=list)
    by_concept: list[ConceptTotal] = field(default_factory=list)


class TicketAnalyticsRepository(TicketRepository):
    def _filtered_ids(
        self,
        filters: dict[str, Any] | None,
        search: str | None,
        search_fields: list[str] | None,
        scoped: bool = True,
    ) -> Select:
        queryset = self.apply_list_filters(
            self.build_list_queryset(filters=filters) if scoped else select(Ticket),
            filters=filters,
            search=search,
            search_fields=search_fields,
        )
        return queryset.with_only_columns(Ticket.id).order_by(None)

    async def totals(
        self,
        filters: dict[str, Any] | None = None,
        search: str | None = None,
        search_fields: list[str] | None = None,
        scoped: bool = True,
    ) -> Totals:
        """Three bags kept apart on purpose, because mixing them gives a number nobody can
        defend: confirmed (final status that counts), committed but still in progress, and
        excluded (rejected or cancelled), which is reported so it is seen to exist.

        Tickets without an amount are ignored. Excluded ones never reach the per-currency
        and per-concept breakdowns.

        `scoped=False` is for sealing a closing, which must total the whole expedient and not
        only what the closer may view."""
        ids = self._filtered_ids(filters, search, search_fields, scoped)
        money = Ticket.amount.is_not(None)
        result = Totals()

        bags = await self.session.execute(
            select(
                TicketTypeStatus.counts_as_committed,
                TicketTypeStatus.is_final,
                func.coalesce(func.sum(Ticket.amount_base), 0),
            )
            .join(Ticket, Ticket.status_id == TicketTypeStatus.id)
            .where(Ticket.id.in_(ids), money)
            .group_by(TicketTypeStatus.counts_as_committed, TicketTypeStatus.is_final)
        )
        for counts, final, total in bags.all():
            if not counts:
                result.excluded_base += _money(total)
            elif final:
                result.committed_base += _money(total)
            else:
                result.pending_base += _money(total)

        counted = (
            Ticket.id.in_(ids),
            money,
            TicketTypeStatus.counts_as_committed.is_(True),
        )
        currencies = await self.session.execute(
            select(
                Ticket.currency,
                func.max(Ticket.currency_name),
                func.sum(Ticket.amount),
                func.sum(Ticket.amount_base),
                func.count(Ticket.id),
            )
            .join(TicketTypeStatus, Ticket.status_id == TicketTypeStatus.id)
            .where(*counted)
            .group_by(Ticket.currency)
            .order_by(Ticket.currency)
        )
        result.by_currency = [
            CurrencyTotal(code, name, _money(amount), _money(base), int(count))
            for code, name, amount, base, count in currencies.all()
        ]
        result.by_concept = await self._by_concept(ids)
        return result

    async def _by_concept(self, ids: Select) -> list[ConceptTotal]:
        item_fields = {
            row.type_id: row
            for row in (
                await self.session.execute(
                    select(TicketTypeField).where(TicketTypeField.role == FieldRole.ITEM.value)
                )
            )
            .scalars()
            .all()
        }
        types = {t.id: t for t in (await self.session.execute(select(TicketType))).scalars().all()}
        grouped: dict[str, ConceptTotal] = {}
        catalog_codes: dict[str, set[str]] = {}
        pending: list[tuple[str, str, str, Decimal, int]] = []

        # One grouped query per type: the concept key lives in a different payload field
        # per type, and the number of types is small.
        for type_id, ticket_type in types.items():
            definition = item_fields.get(type_id)
            column = Ticket.payload[definition.key].astext if definition else None
            selected = [column] if column is not None else []
            query = (
                select(*selected, func.sum(Ticket.amount_base), func.count(Ticket.id))
                .join(TicketTypeStatus, Ticket.status_id == TicketTypeStatus.id)
                .where(
                    Ticket.id.in_(ids),
                    Ticket.amount.is_not(None),
                    Ticket.type_id == type_id,
                    TicketTypeStatus.counts_as_committed.is_(True),
                )
            )
            if column is not None:
                query = query.group_by(column)
            for row in (await self.session.execute(query)).all():
                raw_value, total, count = row if column is not None else (None, *row)
                if not count:
                    continue
                if definition is None:
                    key, label, catalog = ticket_type.code, ticket_type.name, ""
                elif raw_value in (None, ""):
                    key, label, catalog = UNCLASSIFIED_KEY, UNCLASSIFIED_LABEL, ""
                else:
                    key, label, catalog = str(raw_value), str(raw_value), definition.catalog or ""
                    if catalog:
                        catalog_codes.setdefault(catalog, set()).add(key)
                pending.append((key, label, catalog, _money(total), int(count)))

        names = await self._catalog_names(catalog_codes)
        for key, label, catalog, total, count in pending:
            entry = grouped.setdefault(key, ConceptTotal(key, names.get((catalog, key), label)))
            entry.amount_base += total
            entry.count += count
        return sorted(grouped.values(), key=lambda c: (-c.amount_base, c.key))

    async def _catalog_names(self, wanted: dict[str, set[str]]) -> dict[tuple[str, str], str]:
        names: dict[tuple[str, str], str] = {}
        for catalog, codes in wanted.items():
            rows = await self.session.execute(
                select(CatalogItem.code, CatalogItem.name).where(
                    CatalogItem.catalog == catalog, CatalogItem.code.in_(sorted(codes))
                )
            )
            names.update({(catalog, code): name for code, name in rows.all()})
        return names

    async def concept_labels(self, tickets: list[Ticket]) -> dict[Any, str]:
        """Readable concept of each ticket: the catalog name of its `item` field, the raw
        code if the catalog lost it, 'Sin clasificar' when empty, the type name when the type
        has no concept field."""
        wanted: dict[str, set[str]] = {}
        for ticket in tickets:
            item = ticket.type.field_by_role(FieldRole.ITEM.value)
            value = (ticket.payload or {}).get(item.key) if item else None
            if item and item.catalog and value:
                wanted.setdefault(item.catalog, set()).add(str(value))
        names = await self._catalog_names(wanted)
        labels: dict[Any, str] = {}
        for ticket in tickets:
            item = ticket.type.field_by_role(FieldRole.ITEM.value)
            if item is None:
                labels[ticket.id] = ticket.type.name
                continue
            raw = (ticket.payload or {}).get(item.key)
            if raw in (None, ""):
                labels[ticket.id] = UNCLASSIFIED_LABEL
            else:
                labels[ticket.id] = names.get((item.catalog or "", str(raw)), str(raw))
        return labels
