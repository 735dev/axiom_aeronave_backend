"""Hide the separate flight-log screen on an EXISTING database.

`python -m app.scripts.hide_quicklog_module`. The seed never rewrites a module that
already exists, so a database created before this change still shows «Bitácora de vuelo».
The flight times and the finding stay on the technical report ticket. Run once per
database (harmless to repeat).
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import database
from app.repositories.module import ModuleRepository
from app.scripts.init import SeedReport

logger = logging.getLogger(__name__)

MODULE_CODE = "quicklog"


async def _hide(session: AsyncSession, report: SeedReport) -> None:
    modules = ModuleRepository(session)
    record = await modules.get_by_code(MODULE_CODE)
    if record is None or (not record.is_active and not record.show_in_menu):
        return
    await modules.update(record.id, {"is_active": False, "show_in_menu": False})
    report.add("quicklog_module")


async def run_upgrade(session: AsyncSession) -> SeedReport:
    report = SeedReport()
    await _hide(session, report)
    return report


async def main() -> SeedReport:
    async with database.AsyncSessionFactory() as session:
        report = await run_upgrade(session)
        await session.commit()
    await database.close_db()
    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = asyncio.run(main())
    print(f"quicklog module: {result.created or 'already hidden'}")
