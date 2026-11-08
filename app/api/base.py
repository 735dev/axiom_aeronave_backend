"""Base controller that speaks the frontend contract.

basekit's own envelope is `{data, message, status, pagination}` with `page`/`count`
query params. The contract the frontend already consumes is different:

- single resources are returned bare, not wrapped in `data`;
- listings are `{data, total, page, limit, links}` and take `page` and `limit`;
- text search arrives as `q`.

Only the response shaping and the query-param names are adapted here. Pagination,
filtering and ordering still run in basekit's repository engine.
"""

from __future__ import annotations

from typing import Any, ClassVar

from fastapi_basekit.aio.sqlalchemy.controller.base import SQLAlchemyBaseController
from pydantic import BaseModel

from app.config.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_NUMBER, MAX_PAGE_SIZE
from app.exceptions.admin import AdminError
from app.schemas.common import build_page, contains_nul


class ContractController(SQLAlchemyBaseController):
    #: Authorization is enforced by the access middleware from the route registry, so
    #: controllers declare no `permission_classes`.
    permission_classes: ClassVar[list] = []

    def _params(self, skip_frames: int = 1) -> dict[str, Any]:
        params = super()._params()
        filters = dict(params["filters"])
        limit = self._as_int(filters.pop("limit", None), DEFAULT_PAGE_SIZE)
        params["count"] = max(1, min(limit, MAX_PAGE_SIZE))
        # basekit only knows `search`; the contract calls it `q`.
        search = filters.pop("q", None)
        if search and not params.get("search"):
            params["search"] = str(search)
        # The client never picks the sort column: basekit would resolve any attribute.
        params["order_by"] = None
        params["page"] = max(1, min(self._as_int(params.get("page"), 1), MAX_PAGE_NUMBER))
        self._reject_nul(params.get("search"), *filters.values())
        params["filters"] = {
            k: v for k, v in filters.items() if k not in {"count", "page"} and v is not None
        }
        return params

    @staticmethod
    def _reject_nul(*values: Any) -> None:
        if any(contains_nul(v) for v in values if v is not None):
            raise AdminError("validation", "El texto contiene caracteres no permitidos.")

    def validation_context(self) -> dict[str, Any] | None:
        """Context handed to the response schema; subclasses add what their schema reads."""
        return None

    def format_response(
        self,
        data: Any,
        pagination: dict[str, Any] | None = None,
        message: str | None = None,
        response_status: str = "success",
    ) -> Any:
        schema = self.get_schema_class()
        context = self.validation_context()
        if pagination is not None:
            return build_page(
                schema,
                list(data),
                pagination["total"],
                pagination["page"],
                pagination["count"],
                context,
            )
        if data is None:
            return None
        if isinstance(data, BaseModel) and not isinstance(data, schema):
            return data
        if isinstance(data, list):
            return [schema.model_validate(item, context=context) for item in data]
        return schema.model_validate(data, context=context)
