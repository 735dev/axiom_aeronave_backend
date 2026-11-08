"""The signature of the authenticated user. The owner always comes from the token."""

from __future__ import annotations

from fastapi_basekit.aio.sqlalchemy.service.base import BaseService

from app.config.signature import SIGNATURE_MESSAGES
from app.exceptions.auth import AuthError
from app.models.access import AppUser
from app.repositories.user import UserRepository
from app.utils.principal import current_principal
from app.utils.signature import SignatureProblem, parse_signature


class SignatureService(BaseService[AppUser]):
    repository: UserRepository

    async def current(self) -> str | None:
        principal = current_principal(self.request)
        return await self.repository.get_signature(principal.user_id)

    async def replace(self, raw: str | None) -> str | None:
        principal = current_principal(self.request)
        user = await self.repository.get_for_session(principal.user_id)
        if user is None:
            raise AuthError("invalid_token", "Tu sesión no es válida o expiró.")

        new_value: str | None = None
        if raw and raw.strip():
            try:
                new_value = parse_signature(raw.strip()).data_url
            except SignatureProblem as problem:
                raise AuthError(
                    "invalid_signature", SIGNATURE_MESSAGES[problem.code], field="signature_url"
                ) from None

        previous = await self.repository.get_signature(user.id)
        if new_value == previous:
            return previous
        await self.repository.set_signature(user, new_value)
        # The image itself never enters the trail: only the fact, who and when.
        if new_value:
            summary = f"{'Reemplazó' if previous else 'Cargó'} la firma de {user.full_name}"
        else:
            summary = f"Eliminó la firma de {user.full_name}"
        await self.repository.emit_sensitive(
            user,
            summary,
            "signature",
            "Firma digital",
            "registrada" if new_value else None,
            before="registrada" if previous else None,
        )
        return new_value
