from fastapi import APIRouter, Depends
from fastapi_restful.cbv import cbv

from app.api.base import ContractController
from app.schemas.auth import SignatureRead, SignatureUpdate
from app.services.dependency import get_signature_service
from app.services.signature_service import SignatureService

router = APIRouter(prefix="/auth/me/signature", tags=["Auth"])


@cbv(router)
class SignatureController(ContractController):
    """Own signature. There is no user id anywhere in the route: the owner is the token."""

    service: SignatureService = Depends(get_signature_service)
    schema_class = SignatureRead

    @router.get("/", response_model=SignatureRead, summary="Own signature")
    async def read_signature(self):
        return SignatureRead(signature_url=await self.service.current())

    @router.patch(
        "/", response_model=SignatureRead, summary="Load, replace or remove own signature"
    )
    async def replace_signature(self, data: SignatureUpdate):
        return SignatureRead(signature_url=await self.service.replace(data.signature_url))
