"""Replay cache of mutating requests.

Used by the idempotency middleware, which runs outside the request transaction, so every
method here works in a session of its own and commits at once: a claim must be visible to
a concurrent retry immediately, and must survive the rollback of a failed request.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import database
from app.config.settings import get_settings
from app.config.tickets import IDEMPOTENCY_PENDING_SECONDS
from app.models.ticket import IdempotencyRecord


@dataclass(frozen=True)
class Stored:
    state: str
    fingerprint: str
    status_code: int | None
    content_type: str | None
    body: bytes | None
    claimed_at: datetime


class IdempotencyRepository:
    """No basekit base: it has no request session, by design (see module docstring)."""

    async def claim(self, actor_id: str, path: str, key: str, fingerprint: str) -> Stored | None:
        """Reserve the key for the request about to run.

        None means the caller owns the key and must run the request. Otherwise the record
        that already holds it is returned (a stale `pending` claim is taken over).
        """
        now = datetime.now(timezone.utc)
        async with database.AsyncSessionFactory() as session:
            insert = (
                pg_insert(IdempotencyRecord)
                .values(
                    actor_id=actor_id,
                    path=path,
                    key=key,
                    fingerprint=fingerprint,
                    state="pending",
                    created_at=now,
                    expires_at=now + timedelta(seconds=get_settings().IDEMPOTENCY_TTL_SECONDS),
                )
                .on_conflict_do_nothing(constraint="uq_idempotency_actor_path_key")
                .returning(IdempotencyRecord.id)
            )
            if (await session.execute(insert)).first() is not None:
                await session.commit()
                return None
            query = (
                select(IdempotencyRecord)
                .where(
                    IdempotencyRecord.actor_id == actor_id,
                    IdempotencyRecord.path == path,
                    IdempotencyRecord.key == key,
                )
                .with_for_update()
            )
            record = (await session.execute(query)).scalars().first()
            if record is None:  # purged between the insert and the read: try again
                await session.rollback()
                return await self.claim(actor_id, path, key, fingerprint)
            stale = now - record.created_at > timedelta(seconds=IDEMPOTENCY_PENDING_SECONDS)
            if record.state == "pending" and stale:
                record.created_at = now
                record.fingerprint = fingerprint
                await session.commit()
                return None
            if record.expires_at <= now:
                await session.execute(
                    delete(IdempotencyRecord).where(IdempotencyRecord.id == record.id)
                )
                await session.commit()
                return await self.claim(actor_id, path, key, fingerprint)
            stored = Stored(
                record.state,
                record.fingerprint,
                record.status_code,
                record.content_type,
                record.body,
                record.created_at,
            )
            await session.rollback()
            return stored

    async def complete(
        self,
        actor_id: str,
        path: str,
        key: str,
        status_code: int,
        content_type: str | None,
        body: bytes,
    ) -> None:
        async with database.AsyncSessionFactory() as session:
            await session.execute(
                update(IdempotencyRecord)
                .where(
                    IdempotencyRecord.actor_id == actor_id,
                    IdempotencyRecord.path == path,
                    IdempotencyRecord.key == key,
                )
                .values(state="done", status_code=status_code, content_type=content_type, body=body)
            )
            await session.commit()

    async def release(self, actor_id: str, path: str, key: str) -> None:
        """Forget a claim whose request failed, so the client can correct it and retry."""
        async with database.AsyncSessionFactory() as session:
            await session.execute(
                delete(IdempotencyRecord).where(
                    IdempotencyRecord.actor_id == actor_id,
                    IdempotencyRecord.path == path,
                    IdempotencyRecord.key == key,
                    IdempotencyRecord.state == "pending",
                )
            )
            await session.commit()

    async def purge_expired(self) -> int:
        async with database.AsyncSessionFactory() as session:
            result = await session.execute(
                delete(IdempotencyRecord).where(
                    IdempotencyRecord.expires_at <= datetime.now(timezone.utc)
                )
            )
            await session.commit()
            return int(result.rowcount or 0)
