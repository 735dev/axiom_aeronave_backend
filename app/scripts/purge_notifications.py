"""Retention purge of notices: `python -m app.scripts.purge_notifications [--dry-run]`.

Deletes notices older than NOTIFICATION_RETENTION_DAYS (their audience and read marks go
with them) and leaves one summary event in the audit trail. Safe to run from cron at any
time and as often as wanted: it only ever removes rows past the retention line. A value of
0 disables the purge. Runs with the runtime role: a notice is not part of the audit trail.
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
from app.models.notification import Notification
from app.repositories.notification import NotificationRepository

logger = logging.getLogger(__name__)


async def run(session: AsyncSession, *, dry_run: bool = False, now: datetime | None = None) -> int:
    """Rows purged (or that would be, in a dry run). Does not commit."""
    days = get_settings().NOTIFICATION_RETENTION_DAYS
    if days <= 0:
        return 0
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    if dry_run:
        query = (
            select(func.count()).select_from(Notification).where(Notification.created_at < cutoff)
        )
        return int((await session.execute(query)).scalar_one())
    return await NotificationRepository(session).purge_older_than(cutoff)


async def main(dry_run: bool) -> int:
    async with database.AsyncSessionFactory() as session:
        purged = await run(session, dry_run=dry_run)
        await session.commit()
    await database.close_db()
    return purged


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="count, change nothing")
    args = parser.parse_args()
    count = asyncio.run(main(args.dry_run))
    print(f"notifications {'to purge' if args.dry_run else 'purged'}: {count}")
