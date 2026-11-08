"""Sweep of abandoned upload slots: `python -m app.scripts.sweep_uploads [--dry-run]`.

An upload reserves a slot (`attachment_upload`) before it calls Blackbox and deletes it when the
document is linked or the call fails cleanly. A crash between the two leaves the slot; a client
that retries with the same Idempotency-Key resumes from it. Slots older than
UPLOAD_INTENT_STALE_HOURS will never be resumed, so they are dropped here. The document that may
have reached Blackbox has no reference (its API publishes no delete); the log line of the sweep
carries the count so an operator can reconcile if needed.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import database
from app.config.settings import get_settings
from app.models.attachment import AttachmentUpload
from app.repositories.attachment import AttachmentRepository

logger = logging.getLogger(__name__)


async def run(session: AsyncSession, *, dry_run: bool = False, now: datetime | None = None) -> int:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(
        hours=get_settings().UPLOAD_INTENT_STALE_HOURS
    )
    if dry_run:
        query = (
            select(func.count())
            .select_from(AttachmentUpload)
            .where(AttachmentUpload.created_at < cutoff)
        )
        return int((await session.execute(query)).scalar_one())
    swept = await AttachmentRepository(session).purge_stale_slots(cutoff)
    if swept:
        logger.warning("upload_slots_swept count=%s older_than=%s", swept, cutoff.isoformat())
    return swept


async def main(dry_run: bool) -> int:
    async with database.AsyncSessionFactory() as session:
        swept = await run(session, dry_run=dry_run)
        await session.commit()
    await database.close_db()
    return swept


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(
        f"upload slots {'to sweep' if args.dry_run else 'swept'}: {asyncio.run(main(args.dry_run))}"
    )
