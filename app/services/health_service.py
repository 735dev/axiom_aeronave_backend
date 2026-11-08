from __future__ import annotations

from fastapi_basekit.aio.sqlalchemy.service.base import BaseService

from app.config.settings import get_settings
from app.repositories.health import HealthRepository


class HealthService(BaseService):
    repository: HealthRepository  # type: ignore[assignment]

    def liveness(self) -> dict[str, str]:
        return {"status": "healthy", "version": get_settings().VERSION}

    async def readiness(self) -> dict[str, str]:
        await self.repository.ping()
        return {"status": "ready", "database": "ok"}
