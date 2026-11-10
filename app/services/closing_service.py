"""Closing summary of an operation: progress, expenses, supports, blockers and signer.

Read-only. The closing act itself is the ordinary status change of the ticket, guarded by
`closing_hooks.closing_check` and sealed by `closing_hooks.seal_closing`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi_basekit.exceptions.api_exceptions import NotFoundException

from app.config.tickets import DecisionValue
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
            "signers": await self._signers(root),
            "stamp": await self._stamp(stamp),
        }

    @staticmethod
    def _money(totals: Totals) -> dict[str, Any]:
        return {
            "base": totals.committed_base + totals.pending_base,
            "by_currency": totals.by_currency,
        }

    async def _stamp(self, stamp: Any) -> dict[str, Any] | None:
        if stamp is None:
            return None
        # Closings sealed before the multi-signature change have no signature rows.
        signatures = await self.repository.signatures_of(stamp.id)
        return {
            "signed_by": stamp.signed_by,
            "signed_role": stamp.signed_role,
            "signed_at": stamp.signed_at,
            "totals": stamp.totals,
            "files_count": stamp.files_count,
            "folder": stamp.folder,
            "has_signature": True,
            "signature_url": stamp.signature_url,
            "signatures": [
                {
                    "step_code": s.step_code,
                    "step_name": s.step_name,
                    "role_name": s.role_name,
                    "user_name": s.user_name,
                    "signed_at": s.signed_at,
                    "signature_url": s.signature_url,
                }
                for s in signatures
            ],
        }

    async def _signers(self, root: Ticket) -> list[dict[str, Any]]:
        """Every signature the type demands, in order, and what the caller may do about it."""
        principal = self.principal
        has_signature = await self.repository.has_signature(principal.user_id)
        decisions = {d.step_code: d for d in root.decisions}
        locked = root.status.is_final
        result = []
        for step in root.type.signing_steps:
            decision = decisions.get(step.code)
            signed = bool(
                decision and decision.decision == DecisionValue.DONE.value and decision.signed
            )
            result.append(
                {
                    "step_code": step.code,
                    "step_name": step.name,
                    "role_code": getattr(step, "role_code", None),
                    "role_name": rules.role_label(step),
                    "required": True,
                    "signed": signed,
                    "signed_by_name": decision.decided_by if signed else None,
                    "signed_at": decision.decided_at if signed else None,
                    # Strictly the step's own role: a signature is personal, no bypass.
                    "can_sign": (not locked and not signed and step.role_id == principal.role_id),
                    "has_signature": has_signature,
                }
            )
        return result
