"""Reconciliation (§6.18): differences of a month, justifications, close and export."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi_basekit.exceptions.api_exceptions import NotFoundException

from app.config.closing import (
    CSV_FILENAME,
    CSV_HEADERS,
    MIN_JUSTIFICATION,
    PERIOD_ALREADY_CLOSED_MESSAGE,
    PERIOD_FORMAT_MESSAGE,
    PERIOD_PATTERN,
)
from app.config.tickets import BASE_CURRENCY
from app.exceptions.closing import ReconciliationError
from app.models.reconciliation import ReconciliationPeriod
from app.models.ticket import Ticket
from app.repositories.reconciliation import ReconciliationPeriodRepository, ReconciliationRepository
from app.services.base import ProjectService
from app.utils import csv_export
from app.utils.numbering import business_today
from app.utils.reconciliation import Report, compute, csv_rows, period_bounds, round2


class ReconciliationService(ProjectService[Ticket]):
    repository: ReconciliationRepository
    periods: ReconciliationPeriodRepository

    # ------------------------------------------------------------------ reads

    @staticmethod
    def _valid(period: str) -> str:
        if not PERIOD_PATTERN.match(period or ""):
            raise ReconciliationError(
                "validation", PERIOD_FORMAT_MESSAGE, "period", codigo="periodo"
            )
        return period

    async def _compute(self, period: str, *, scoped: bool) -> Report:
        return compute(
            period,
            await self.repository.expenses_around(period, scoped=scoped),
            await self.repository.acknowledgements(period),
            BASE_CURRENCY,
        )

    def _present(self, report: Report, closed: ReconciliationPeriod | None) -> dict[str, Any]:
        return {
            "period": report.period,
            "start": report.start,
            "end": report.end,
            "base_currency": report.base_currency,
            "rows": report.rows,
            "differences": report.differences,
            "open_differences": report.open_differences,
            "totals": {
                "reconciled_base": report.reconciled_base,
                "difference_base": report.difference_base,
                "discarded_base": report.discarded_base,
            },
            "close": closed,
        }

    async def report(self, period: str) -> dict[str, Any]:
        await self.throttle("heavy_list")
        period = self._valid(period)
        report = await self._compute(period, scoped=True)
        return self._present(report, await self.periods.get_period(period))

    async def closed_periods(self) -> list[ReconciliationPeriod]:
        return await self.periods.all_closed()

    # ------------------------------------------------------------------ justify

    async def acknowledge(self, data: Any) -> dict[str, Any]:
        body = data.model_dump() if hasattr(data, "model_dump") else dict(data)
        period, key = self._valid(body["period"]), str(body["key"])
        if await self.periods.is_closed(period, lock=True):
            raise ReconciliationError(
                "conflict", PERIOD_ALREADY_CLOSED_MESSAGE.format(period=period)
            )
        # Unscoped: whoever justifies answers for the whole month, not for their view.
        report = await self._compute(period, scoped=False)
        difference = next((d for d in report.differences if d.key == key), None)
        if difference is None:
            # A key that is not a standing difference is not justified: nothing to accept.
            raise NotFoundException("no encontrado")
        note = str(body["note"]).strip()
        if len(note) < MIN_JUSTIFICATION:
            raise ReconciliationError(
                "validation",
                "La justificación es obligatoria.",
                "note",
                codigo="justificacion",
                errors={"note": "La justificación es obligatoria."},
            )
        await self.repository.acknowledge(
            period,
            key,
            note,
            self.principal,
            f"Justificó la diferencia {difference.kind.value} de {difference.ticket_code} en {period}",
        )
        return self._present(await self._compute(period, scoped=False), None)

    # ------------------------------------------------------------------ close

    async def close(self, data: Any) -> dict[str, Any]:
        body = data.model_dump() if hasattr(data, "model_dump") else dict(data)
        period = self._valid(body["period"])
        # Exclusive lock: edits in flight finish first, and the ones that start afterwards
        # see the period closed. Two closes take turns; the second finds it closed.
        await self.repository.lock_for_close(period)
        if await self.periods.is_closed(period):
            raise ReconciliationError(
                "conflict", PERIOD_ALREADY_CLOSED_MESSAGE.format(period=period)
            )
        # A month that has not started has nothing to close: it would close empty and block
        # future bookings.
        if period_bounds(period)[0] > business_today():
            raise ReconciliationError(
                "validation", "No se puede cerrar un período futuro.", codigo="periodo_futuro"
            )
        report = await self._compute(period, scoped=False)
        open_ones = [d for d in report.differences if d.acknowledgement is None]
        if open_ones:
            raise ReconciliationError(
                "validation",
                f"No se puede cerrar {period}: quedan {len(open_ones)} diferencia(s) "
                "sin justificar.",
                codigo="diferencias_abiertas",
                errors={d.key: "Diferencia sin justificar" for d in open_ones},
            )
        total = round2(sum((r.amount_base for r in report.rows), Decimal(0)))
        closed = await self.repository.close(
            period,
            self.principal,
            len(report.rows),
            total,
            len(report.differences),
            f"Cerró el período {period} ({len(report.rows)} gasto(s), "
            f"{len(report.differences)} diferencia(s) justificada(s))",
        )
        if closed is None:
            raise ReconciliationError(
                "conflict", PERIOD_ALREADY_CLOSED_MESSAGE.format(period=period)
            )
        return self._present(report, closed)

    # ------------------------------------------------------------------ export

    async def export_csv(self, period: str) -> tuple[str, str]:
        """(filename, text). The fact is recorded: who took which month out of the system."""
        await self.throttle("export")
        period = self._valid(period)
        report = await self._compute(period, scoped=True)
        await self.repository.record_export(period, len(report.rows))
        text = csv_export.build_csv(CSV_HEADERS, csv_rows(report))
        return CSV_FILENAME.format(period=period), text

    async def record_export(self, period: str) -> dict[str, Any]:
        """Client-built exports (the simulator's flow): the server only leaves the record."""
        await self.throttle("export")
        period = self._valid(period)
        report = await self._compute(period, scoped=True)
        await self.repository.record_export(period, len(report.rows))
        return {"period": period, "rows": len(report.rows)}
