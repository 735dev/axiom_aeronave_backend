from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi_restful.cbv import cbv

from app.api.base import ContractController
from app.schemas.rule import RuleCreate, RuleRead, RuleUpdate
from app.services.dependency import get_rule_service
from app.services.rule_service import RuleService

router = APIRouter(prefix="/rules", tags=["Rules"])


@cbv(router)
class RuleController(ContractController):
    service: RuleService = Depends(get_rule_service)
    schema_class = RuleRead

    @router.get("/", response_model=list[RuleRead], summary="Business rules")
    async def list_rules(
        self,
        type_code: str | None = Query(default=None, max_length=60),
        is_active: bool | None = Query(default=None),
    ):
        return self.format_response(await self.service.list_all(self._params()["filters"]))

    @router.get("/{id}/", response_model=RuleRead, summary="A rule")
    async def retrieve(self, id: UUID):
        return await super().retrieve(id)

    @router.post("/", response_model=RuleRead, status_code=status.HTTP_201_CREATED)
    async def create(self, data: RuleCreate):
        return await super().create(data)

    @router.patch("/{id}/", response_model=RuleRead, summary="Update a rule")
    async def update(self, id: UUID, data: RuleUpdate):
        return await super().update(id, data)

    @router.delete("/{id}/", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
    async def delete(self, id: UUID):
        await super().delete(id)
