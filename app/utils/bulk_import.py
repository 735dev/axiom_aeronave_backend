"""Value objects of a bulk import: row issues, per-row plans and the final report."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

IMPORT_MAX_ROWS = 1000

PlanAction = Literal["create", "update", "skip"]


class RowIssue(Exception):
    """A row that cannot be imported. Raised by a service's per-row hook."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


@dataclass(frozen=True)
class RowPlan:
    """What a valid row will do once the whole file has been validated."""

    action: PlanAction
    data: dict[str, Any] = field(default_factory=dict)
    #: Hook-private reference (the existing record to update, for instance).
    target: Any = None


@dataclass(frozen=True)
class RowError:
    row: int
    field: str | None
    message: str


@dataclass
class ImportReport:
    dry_run: bool
    total: int
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[RowError] = field(default_factory=list)
