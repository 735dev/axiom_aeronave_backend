"""Closing summary of an operation: progress, expenses, supports, blockers and signer.

Read-only. The closing act itself is the ordinary status change of the ticket, guarded by
`closing_hooks.closing_check` and sealed by `closing_hooks.seal_closing`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi_basekit.exceptions.api_exceptions import NotFoundException

from app.config.closing import SIGNATURE_STEP_CODE
from app.config.tickets import PERM_DECIDE_ANY, DecisionValue
from app.repositories.closing import TicketClosingRepository
from app.repositories.ticket_analytics import Totals
from app.repositories.ticket_support import TicketSupportRepository
from app.services.base import ProjectService
from app.models.ticket import Ticket
from app.utils import closing as rules


class ClosingService(ProjectService[Ticket]):
    repository: TicketClosingRepository
    supports: TicketSupportRepository

    async def summary(self, id: UUID) -> dict[str, Any]:  # noqa: A002
        found = await self.repository.expedient(id)
        if found is None:
            # Unknown, out of scope, or not an operation: all the same answer.
            raise NotFoundException("no encontrado")
        root, children = found
        members = [root, *children]
        ids = [m.id for m in members]
        counts = await self.supports.counts(ids)
        inventory = await self.supports.inventory(members)
        blockers = rules.blockers(
            root,
            children,
            counts,
            [(i.ticket_id, i.ticket_code, i.name) for i in inventory if i.awaiting_validation],
        )

        scope = {"parent_id": root.id}
        totals = await self.repository.totals(scope)
        confirmed = await self.repository.totals({**scope, "status__is_final": True})
        in_progress = await self.repository.totals({**scope, "status__is_final": False})
        labels = await self.repository.concept_labels(children)

        by_type: dict[str, int] = {}
        for item in inventory:
            by_type[item.document_type] = by_type.get(item.document_type, 0) + 1

        stamp = await self.repository.stamp_of(root.id) if root.closing_record else None
        return {
            "ticket_id": root.id,
            "ticket_code": root.code,
            "closed": root.status.is_final,
            "progress": rules.role_progress(members),
            "expenses": [
                {
                    "ticket_id": c.id,
                    "code": c.code,
                    "concept": labels[c.id],
                    "amount": c.amount,
                    "currency": c.currency,
                    "amount_base": c.amount_base,
                    "status": c.status.name,
                    "counts": c.status.counts_as_committed,
                    "has_support": counts.get(c.id, 0) > 0,
                }
                for c in children
                if c.amount is not None
            ],
            "totals": totals,
            "confirmed": self._money(confirmed),
            "in_progress": self._money(in_progress),
            "supports": inventory,
            "supports_by_type": [
                {"document_type": k, "count": v} for k, v in sorted(by_type.items())
            ],
            "blockers": blockers,
            "ready": rules.is_ready(blockers),
            "signer": await self._signer(root),
            "stamp": (
                {
                    "signed_by": stamp.signed_by,
                    "signed_role": stamp.signed_role,
                    "signed_at": stamp.signed_at,
                    "totals": stamp.totals,
                    "files_count": stamp.files_count,
                    "folder": stamp.folder,
                    "has_signature": True,
                    "signature_url": stamp.signature_url,
                }
                if stamp
                else None
            ),
        }

    @staticmethod
    def _money(totals: Totals) -> dict[str, Any]:
        return {
            "base": totals.committed_base + totals.pending_base,
            "by_currency": totals.by_currency,
        }

    async def _signer(self, root: Ticket) -> dict[str, Any]:
        principal = self.principal
        step = root.type.step_by_code(SIGNATURE_STEP_CODE)
        decision = next((d for d in root.decisions if d.step_code == SIGNATURE_STEP_CODE), None)
        can_sign = step is not None and (
            step.role_id == principal.role_id or PERM_DECIDE_ANY in principal.permissions
        )
        return {
            "user_name": principal.full_name,
            "role_name": principal.role_name,
            "has_signature": await self.repository.has_signature(principal.user_id),
            "can_sign": can_sign,
            "step_code": step.code if step else None,
            "step_role_name": getattr(step, "role_name", None) if step else None,
            "step_signed": bool(
                decision and decision.decision == DecisionValue.DONE.value and decision.signed
            ),
        }
