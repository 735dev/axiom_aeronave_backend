from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi_restful.cbv import cbv

from app.api.base import ContractController
from app.config.closing import MAX_PASSENGER_QUERY
from app.schemas.passenger import (
    ManifestBody,
    PassengerMatchRead,
    PassengerRead,
    PassengerSearchRead,
)
from app.services.dependency import get_passenger_service
from app.services.passenger_service import PassengerService

manifest_router = APIRouter(prefix="/tickets", tags=["Passengers"])
router = APIRouter(prefix="/passengers", tags=["Passengers"])


def _manifest(rows) -> list[PassengerRead]:
    return [
        PassengerRead(
            id=r.id,
            full_name=r.full_name,
            document=r.document,
            nationality=r.nationality,
            passport_file_id=r.passport_ref,
        )
        for r in rows
    ]


@cbv(manifest_router)
class ManifestController(ContractController):
    service: PassengerService = Depends(get_passenger_service)

    @manifest_router.get(
        "/{id}/passengers/", response_model=list[PassengerRead], summary="Manifest of an operation"
    )
    async def read_manifest(self, id: UUID):
        return _manifest(await self.service.manifest(id))

    @manifest_router.put(
        "/{id}/passengers/", response_model=list[PassengerRead], summary="Replace the manifest"
    )
    async def replace_manifest(self, id: UUID, data: ManifestBody):
        return _manifest(await self.service.replace(id, data))

    @manifest_router.post(
        "/{id}/passengers/",
        response_model=list[PassengerRead],
        summary="Replace the manifest (same as PUT, the simulator's verb)",
    )
    async def post_manifest(self, id: UUID, data: ManifestBody):
        return _manifest(await self.service.replace(id, data))


@cbv(router)
class PassengerSearchController(ContractController):
    service: PassengerService = Depends(get_passenger_service)

    @router.get("/search/", response_model=PassengerSearchRead, summary="Who travelled where")
    async def search(self, q: str = Query(default="", max_length=MAX_PASSENGER_QUERY)):
        found = await self.service.search(q)
        return PassengerSearchRead(data=[PassengerMatchRead.model_validate(m) for m in found])
