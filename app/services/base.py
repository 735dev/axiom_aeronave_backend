"""Service base: filter whitelist, unpaginated reads and the authenticated caller."""

from __future__ import annotations

from typing import Any, ClassVar, TypeVar

from fastapi_basekit.aio.sqlalchemy.service.base import BaseService

from app.utils.client_ip import client_ip
from app.utils.principal import Principal, current_principal
from app.utils.rate_limit import get_action_throttle

ModelT = TypeVar("ModelT")


class ProjectService(BaseService[ModelT]):
    #: Query-string filters the client may use, by model attribute (or alias renamed in
    #: `get_filters`). basekit resolves ANY attribute name it is given, including hidden
    #: ones such as password_hash, so anything not listed here is dropped.
    filter_whitelist: ClassVar[frozenset[str]] = frozenset()

    #: Action budget (`ActionThrottle`) spent by every paginated listing of the service.
    list_budget: ClassVar[str | None] = None

    @property
    def principal(self) -> Principal:
        return current_principal(self.request)

    async def throttle(self, action: str) -> None:
        """Spend one unit of the caller's budget for a costly action (429 when it is out)."""
        await get_action_throttle().check(
            action, str(self.principal.user_id), client_ip(self.request)
        )

    async def list(self, *args: Any, **kwargs: Any):
        if self.list_budget:
            await self.throttle(self.list_budget)
        return await super().list(*args, **kwargs)

    def get_filters(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        allowed: dict[str, Any] = {}
        for key, value in (filters or {}).items():
            if key.split("__", 1)[0] in self.filter_whitelist:
                allowed[key] = value
        return allowed

    async def list_all(self, filters: dict[str, Any] | None = None) -> list[ModelT]:
        """Whole result for reference lists that the client loads without paginating."""
        return await self.repository.list_all(self.get_filters(filters))
