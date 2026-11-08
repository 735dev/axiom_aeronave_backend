from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.config.closing import (
    MAX_PASSENGERS_PER_MANIFEST,
    PASSENGER_DOCUMENT_MAX,
    PASSENGER_NAME_MAX,
    PASSENGER_NATIONALITY_MAX,
)
from app.schemas.common import OptionalText, RequestModel


class PassengerBody(RequestModel):
    """What a client may set. The comparable forms and the position are the server's."""

    full_name: str = Field(min_length=1, max_length=PASSENGER_NAME_MAX)
    document: str = Field(min_length=1, max_length=PASSENGER_DOCUMENT_MAX)
    nationality: OptionalText = Field(default=None, max_length=PASSENGER_NATIONALITY_MAX)
    passport_file_id: UUID | None = None


class ManifestBody(RequestModel):
    passengers: list[PassengerBody] = Field(max_length=MAX_PASSENGERS_PER_MANIFEST)


class PassengerRead(BaseModel):
    id: UUID
    full_name: str
    document: str
    nationality: str | None = None
    passport_file_id: UUID | None = None

    model_config = ConfigDict(from_attributes=True)


class PassengerOperationRead(BaseModel):
    id: UUID
    code: str
    date: date
    route: str
    status_code: str
    status_name: str
    status_is_final: bool

    model_config = ConfigDict(from_attributes=True)


class PassportRefRead(BaseModel):
    ticket_id: UUID
    file_id: UUID


class PassengerMatchRead(BaseModel):
    document: str
    full_name: str
    nationality: str | None = None
    passport: PassportRefRead | None = None
    operations: list[PassengerOperationRead]

    model_config = ConfigDict(from_attributes=True)


class PassengerSearchRead(BaseModel):
    data: list[PassengerMatchRead]
