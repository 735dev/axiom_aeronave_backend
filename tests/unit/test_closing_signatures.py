"""Rules of the multi-signature closing, with no database: the pure rules in
app.utils.closing and the stamp read of a closing sealed before the change."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.config.closing import BlockerKind
from app.config.tickets import DecisionValue
from app.services.closing_service import ClosingService
from app.utils import closing as rules

ROLES = {
    "sign_captain": "Piloto",
    "sign_maintenance": "Mantenimiento",
    "sign_avsec": "Coordinación AVSEC",
    "sign_general": "Gerencia General",
}


def step(code, *, signs=True, required=True, role=None):
    return SimpleNamespace(
        code=code,
        name=f"Paso {code}",
        required=required,
        requires_signature=signs,
        role_id=uuid4(),
        role_name=role or ROLES.get(code),
    )


def decision(code, *, done=True, signed=True):
    return SimpleNamespace(
        step_code=code,
        decision=DecisionValue.DONE.value if done else DecisionValue.REJECTED.value,
        signed=signed,
    )


def root(steps, decisions):
    ticket_type = SimpleNamespace(
        steps=steps, signing_steps=[s for s in steps if s.requires_signature]
    )
    return SimpleNamespace(id=uuid4(), code="OP-1", type=ticket_type, decisions=decisions)


def four():
    return [step(code) for code in ROLES]


class TestSignatureProblem:
    def test_a_type_without_signing_steps_closes_without_signatures(self):
        assert rules.signature_problem(root([step("plan", signs=False)], [])) is None

    def test_all_four_signed_is_fine(self):
        decisions = [decision(code) for code in ROLES]
        assert rules.signature_problem(root(four(), decisions)) is None

    def test_missing_ones_are_named_by_role_in_order(self):
        message = rules.signature_problem(root(four(), [decision("sign_captain")]))
        assert message == (
            "No se puede cerrar: faltan las firmas de Mantenimiento, "
            "Coordinación AVSEC, Gerencia General."
        )

    def test_a_pending_decision_counts_as_missing(self):
        decisions = [decision(c) for c in ROLES]
        decisions[2] = decision("sign_avsec", done=False)
        assert "Coordinación AVSEC" in rules.signature_problem(root(four(), decisions))

    def test_a_decision_without_the_image_asks_to_load_it(self):
        decisions = [decision(c) for c in ROLES]
        decisions[1] = decision("sign_maintenance", signed=False)
        message = rules.signature_problem(root(four(), decisions))
        assert "cargar su firma en su perfil" in message and "Mantenimiento" in message

    def test_missing_wins_over_unsigned_image(self):
        decisions = [decision("sign_captain", signed=False)]
        assert "faltan las firmas" in rules.signature_problem(root(four(), decisions))


class TestBlockers:
    def test_each_unsigned_step_is_a_signature_blocker_with_its_role(self):
        found = rules.blockers(root(four(), [decision("sign_captain")]), [], {}, [])
        assert [b.step_code for b in found] == ["sign_maintenance", "sign_avsec", "sign_general"]
        assert {b.kind for b in found} == {BlockerKind.SIGNATURE}
        assert [b.role_name for b in found] == [
            "Mantenimiento",
            "Coordinación AVSEC",
            "Gerencia General",
        ]

    def test_ready_means_only_signatures_are_left(self):
        only_signatures = rules.blockers(root(four(), []), [], {}, [])
        assert rules.is_ready(only_signatures) is True
        with_step = rules.blockers(root([step("plan", signs=False), *four()], []), [], {}, [])
        assert rules.is_ready(with_step) is False

    def test_all_signed_leaves_no_blocker(self):
        decisions = [decision(c) for c in ROLES]
        assert rules.blockers(root(four(), decisions), [], {}, []) == []


class FakeRepository:
    def __init__(self, signatures):
        self._signatures = signatures

    async def signatures_of(self, closing_id):
        return self._signatures


def stamp():
    return SimpleNamespace(
        id=uuid4(),
        signed_by="Gerencia de Operaciones",
        signed_role="Gerencia de Operaciones",
        signed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        totals={},
        files_count=0,
        folder="F",
        signature_url="data:image/png;base64,AA==",
    )


class TestStampOfAClosingSealedBeforeTheChange:
    def run(self, signatures):
        service = SimpleNamespace(repository=FakeRepository(signatures))
        return asyncio.run(ClosingService._stamp(service, stamp()))

    def test_it_has_no_signature_rows_and_still_reads(self):
        body = self.run([])
        assert body["signatures"] == []
        assert body["signed_by"] == "Gerencia de Operaciones" and body["has_signature"] is True

    def test_a_seal_with_rows_lists_them_with_their_images(self):
        row = SimpleNamespace(
            step_code="sign_captain",
            step_name="Firma del capitán de nave",
            role_name="Piloto",
            user_name="Cap. Rodríguez",
            signed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            signature_url="data:image/png;base64,AA==",
        )
        body = self.run([row])
        assert body["signatures"][0]["step_code"] == "sign_captain"
        assert body["signatures"][0]["signature_url"].startswith("data:image/png")
