"""Append-only is enforced below the HTTP layer too."""

import pytest

from app.config import database
from app.exceptions.base import AppDomainError
from app.models.audit import AuditEvent
from app.repositories.audit_trail import AuditEntry, AuditEventRepository
from app.config.audit import AuditAction, AuditEntity
from app.utils.audit_context import SYSTEM_ACTOR


async def _new_event(session):
    repo = AuditEventRepository(session)
    await repo.append(
        AuditEntry(
            action=AuditAction.UPDATE,
            entity=AuditEntity.CATALOG,
            entity_id="t",
            entity_label="t",
            summary="integration",
            actor=SYSTEM_ACTOR,
        )
    )
    await session.flush()
    return repo


class TestAppendOnly:
    async def test_repository_refuses_update_delete_save(self):
        async with database.AsyncSessionFactory() as session:
            repo = AuditEventRepository(session)
            with pytest.raises(AppDomainError):
                await repo.update("x", {"summary": "tampered"})
            with pytest.raises(AppDomainError):
                await repo.delete("x")
            with pytest.raises(AppDomainError):
                await repo.save(object())
            with pytest.raises(AppDomainError):
                await repo.hard_delete(object())

    async def test_orm_update_is_refused(self):
        async with database.AsyncSessionFactory() as session:
            await _new_event(session)
            event = (
                await session.execute(
                    __import__("sqlalchemy")
                    .select(AuditEvent)
                    .order_by(AuditEvent.seq.desc())
                    .limit(1)
                )
            ).scalar_one()
            event.summary = "tampered"
            with pytest.raises(Exception):
                await session.flush()
            await session.rollback()

    async def test_orm_delete_is_refused(self):
        async with database.AsyncSessionFactory() as session:
            await _new_event(session)
            event = (
                await session.execute(
                    __import__("sqlalchemy")
                    .select(AuditEvent)
                    .order_by(AuditEvent.seq.desc())
                    .limit(1)
                )
            ).scalar_one()
            await session.delete(event)
            with pytest.raises(Exception):
                await session.flush()
            await session.rollback()

    async def test_seq_is_monotonic(self):
        async with database.AsyncSessionFactory() as session:
            await _new_event(session)
            await _new_event(session)
            rows = (
                (
                    await session.execute(
                        __import__("sqlalchemy")
                        .select(AuditEvent.seq)
                        .order_by(AuditEvent.seq.desc())
                        .limit(2)
                    )
                )
                .scalars()
                .all()
            )
            assert rows[0] > rows[1]
            await session.rollback()
