from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config.tickets import (
    ROUTE_LIMITS_MAX,
    RULE_MESSAGE_MAX,
    RULE_NAME_MAX,
    RuleAction,
    RuleField,
    RuleOperator,
)
from app.schemas.common import RequestModel
from app.utils.money import to_decimal


class RuleCreate(RequestModel):
    name: str = Field(min_length=3, max_length=RULE_NAME_MAX)
    type_code: str = Field(min_length=1, max_length=60)
    field: RuleField = RuleField.AMOUNT_BASE
    operator: RuleOperator | None = None
    value: str | int | float | None = None
    action: RuleAction = RuleAction.WARN
    message: str = Field(min_length=3, max_length=RULE_MESSAGE_MAX)
    route_limits: dict[str, float] | None = Field(default=None, max_length=ROUTE_LIMITS_MAX)
    is_active: bool = True


class RuleUpdate(RequestModel):
    name: str | None = Field(default=None, min_length=3, max_length=RULE_NAME_MAX)
    field: RuleField | None = None
    operator: RuleOperator | None = None
    value: str | int | float | None = None
    action: RuleAction | None = None
    message: str | None = Field(default=None, min_length=3, max_length=RULE_MESSAGE_MAX)
    route_limits: dict[str, float] | None = Field(default=None, max_length=ROUTE_LIMITS_MAX)
    is_active: bool | None = None


class RuleRead(BaseModel):
    id: UUID
    name: str
    type_code: str
    field: str
    operator: str
    value: float | str | None = None
    action: str
    message: str
    route_limits: dict[str, float] | None = None
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def _numeric_value(cls, obj: Any) -> Any:
        if isinstance(obj, dict) or not hasattr(obj, "value"):
            return obj
        data = {name: getattr(obj, name) for name in cls.model_fields if hasattr(obj, name)}
        raw = obj.value
        number = to_decimal(raw) if raw is not None else None
        data["value"] = float(number) if number is not None else raw
        return data
