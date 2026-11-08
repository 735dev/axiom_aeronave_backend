"""Retention of passengers' personal data:
`python -m app.scripts.purge_personal_data [--dry-run]`.

Anonymizes (does not delete) the manifest rows of closed operations whose accounting date is
older than PASSENGER_RETENTION_DAYS: name, document, nationality and the passport reference
are erased, the row and its place in the manifest stay, and the passport files are detached
from the operation. One summary event is left in the audit trail (counts only, no personal
data). 0 disables the policy. Idempotent; runs with the runtime role.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import database
from app.config.settings import get_settings
from app.models.passenger import TicketPassenger
from app.models.ticket import Ticket, TicketType, TicketTypeStatus
from app.repositories.passenger import TicketPassengerRepository
from app.utils.numbering import business_today

logger = logging.getLogger(__name__)


async def run(
    session: AsyncSession, *, dry_run: bool = False, today: date | None = None
) -> tuple[int, int]:
    """(passengers, passports) anonymized, or that would be. Does not commit."""
    days = get_settings().PASSENGER_RETENTION_DAYS
    if days <= 0:
        return 0, 0
    cutoff = (today or business_today()) - timedelta(days=days)
    if dry_run:
        query = (
            select(func.count())
            .select_from(TicketPassenger)
            .join(Ticket, Ticket.id == TicketPassenger.ticket_id)
            .join(TicketType, TicketType.id == Ticket.type_id)
            .join(TicketTypeStatus, TicketTypeStatus.id == Ticket.status_id)
            .where(
                TicketType.is_root.is_(True),
                TicketTypeStatus.is_final.is_(True),
                Ticket.accounting_date < cutoff,
                TicketPassenger.anonymized_at.is_(None),
            )
        )
        return int((await session.execute(query)).scalar_one()), 0
    return await TicketPassengerRepository(session).anonymize_expired(cutoff, days)


async def main(dry_run: bool) -> tuple[int, int]:
    async with database.AsyncSessionFactory() as session:
        counts = await run(session, dry_run=dry_run)
        await session.commit()
    await database.close_db()
    return counts


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="count, change nothing")
    args = parser.parse_args()
    people, files = asyncio.run(main(args.dry_run))
    print(f"passengers anonymized: {people}, passports detached: {files}")
