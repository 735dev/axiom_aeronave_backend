from fastapi import APIRouter, Depends, Response, status
from fastapi_restful.cbv import cbv

from app.api.base import ContractController
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    PasswordChangeRequest,
    ProfileUpdate,
    RefreshRequest,
    SessionResponse,
    TokenPairResponse,
)
from app.services.auth_service import AuthService
from app.services.dependency import get_auth_service

router = APIRouter(prefix="/auth", tags=["Auth"])


@cbv(router)
class AuthController(ContractController):
    service: AuthService = Depends(get_auth_service)
    schema_class = SessionResponse

    @router.post("/login/", response_model=LoginResponse, summary="Login")
    async def login(self, data: LoginRequest):
        return LoginResponse.model_validate(await self.service.login(data.email, data.password))

    @router.post("/refresh/", response_model=TokenPairResponse, summary="Renew the token pair")
    async def refresh(self, data: RefreshRequest):
        return TokenPairResponse.model_validate(await self.service.refresh(data.refresh_token))

    @router.post("/logout/", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
    async def logout(self):
        await self.service.logout()

    @router.get("/me/", response_model=SessionResponse, summary="Current session")
    async def me(self):
        return self.format_response(await self.service.session())

    @router.patch("/me/", response_model=SessionResponse, summary="Update own profile")
    async def update_me(self, data: ProfileUpdate):
        result = await self.service.update_profile(data.model_dump(exclude_unset=True))
        return self.format_response(result)

    @router.post("/password/", response_model=TokenPairResponse, summary="Change own password")
    async def change_password(self, data: PasswordChangeRequest):
        result = await self.service.change_password(data.current_password, data.new_password)
        return TokenPairResponse.model_validate(result)
