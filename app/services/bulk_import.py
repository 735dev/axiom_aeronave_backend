"""Reusable bulk import: validate every row first, then write all or nothing.

A service plugs in with three hooks and gets the rest for free: the row cap, the throttle,
`dry_run`, error collection per row and the single-transaction write. Services never
commit (`get_db` commits once per request and rolls back on any exception), so an error
raised while applying aborts the whole import with nothing persisted.

    prepare(rows, **params)  -> context   one-off lookups (existing records, allowed codes)
    plan_row(row, context)   -> RowPlan   validate ONE row; raise RowIssue / AdminError
    apply_plan(plan, context)             write ONE planned row (create or update)
"""

from __future__ import annotations

from typing import Any, ClassVar, Sequence

from app.exceptions.admin import AdminError
from app.utils.bulk_import import IMPORT_MAX_ROWS, ImportReport, RowError, RowIssue, RowPlan

ROW_LIMIT_MESSAGE = f"Una importación admite hasta {IMPORT_MAX_ROWS} filas."


class BulkImportMixin:
    #: Action budget (`ActionThrottle`) spent by each import call.
    import_budget: ClassVar[str] = "import"

    async def import_prepare(self, rows: Sequence[Any], **params: Any) -> dict[str, Any]:
        return {}

    async def import_plan_row(self, row: Any, context: dict[str, Any]) -> RowPlan:
        raise NotImplementedError

    async def import_apply_plan(self, plan: RowPlan, context: dict[str, Any]) -> None:
        raise NotImplementedError

    async def run_import(self, rows: Sequence[Any], dry_run: bool, **params: Any) -> ImportReport:
        await self.throttle(self.import_budget)  # type: ignore[attr-defined]
        if len(rows) > IMPORT_MAX_ROWS:
            raise AdminError("validation", ROW_LIMIT_MESSAGE, field="rows")

        context = await self.import_prepare(rows, **params)
        report = ImportReport(dry_run=dry_run, total=len(rows))
        plans: list[RowPlan] = []
        for number, row in enumerate(rows, start=1):
            try:
                plans.append(await self.import_plan_row(row, context))
            except (RowIssue, AdminError) as exc:
                report.errors.append(RowError(number, exc.field, exc.message))

        if report.errors:
            return report

        for plan in plans:
            if plan.action == "skip":
                report.skipped += 1
                continue
            if not dry_run:
                await self.import_apply_plan(plan, context)
            if plan.action == "create":
                report.created += 1
            else:
                report.updated += 1
        return report
