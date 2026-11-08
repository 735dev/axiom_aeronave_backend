"""Shapes shared by every endpoint: the paginated envelope and the error body."""

from __future__ import annotations

from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, BeforeValidator, ConfigDict, model_validator

T = TypeVar("T")


class PageLinks(BaseModel):
    next: str | None = None
    previous: str | None = None


class Page(BaseModel, Generic[T]):
    """Contract envelope: `{data, total, page, limit, links}`."""

    data: list[T]
    total: int
    page: int
    limit: int
    links: PageLinks

    model_config = ConfigDict(from_attributes=True)


class ErrorBody(BaseModel):
    title: str
    detail: str
    errors: dict[str, str] | None = None


def contains_nul(value) -> bool:
    """True when a NUL character hides anywhere inside a (nested) JSON value."""
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(contains_nul(k) or contains_nul(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(contains_nul(v) for v in value)
    return False


class RequestModel(BaseModel):
    """Base of every request schema. PostgreSQL text cannot hold NUL: reject it at the door
    instead of letting the driver fail with a 500."""

    @model_validator(mode="before")
    @classmethod
    def _reject_nul(cls, data):
        if contains_nul(data):
            raise ValueError("El texto contiene caracteres no permitidos")
        return data


def blank_to_none(value):
    """Forms send '' for an empty optional field; the API stores NULL."""
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


OptionalText = Annotated[str | None, BeforeValidator(blank_to_none)]


def build_page(
    schema: type[BaseModel],
    items: list,
    total: int,
    page: int,
    limit: int,
    context: dict | None = None,
) -> dict:
    """Page envelope as a dict, with `?page=N&limit=M` navigation links."""
    has_next = page * limit < total
    return {
        "data": [schema.model_validate(i, context=context) for i in items],
        "total": total,
        "page": page,
        "limit": limit,
        "links": {
            "next": f"?page={page + 1}&limit={limit}" if has_next else None,
            "previous": f"?page={page - 1}&limit={limit}" if page > 1 else None,
        },
    }
