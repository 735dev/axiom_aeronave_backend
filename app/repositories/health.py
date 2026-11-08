from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class HealthRepository:
    def __init__(self, db: AsyncSession):
        self._session = db

    async def ping(self) -> bool:
        await self._session.execute(text("SELECT 1"))
        return True
