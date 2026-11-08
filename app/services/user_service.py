from __future__ import annotations


from fastapi_basekit.exceptions.api_exceptions import NotFoundException

from app.config.access import SUPERUSER_ROLE_CODE
from app.config.security import hash_password
from app.exceptions.admin import AdminError
from app.models.access import AppUser, Permission, Role
from app.repositories.catalog import CatalogItemRepository
from app.repositories.permission import PermissionRepository
from app.repositories.role import RoleRepository
from app.repositories.user import UserRepository
from app.services.base import ProjectService
from app.utils.effective_permissions import EffectivePermission, effective_breakdown
from app.utils.passwords import generate_temporary_password, generate_unusable_password

SELF_CHANGE_MESSAGE = "No puedes modificar tu propio rol, estado ni excepciones de permisos."
SIGNATURE_OWNER_MESSAGE = "La firma solo puede cargarla su propietario desde su perfil."
SCALAR_FIELDS = ("full_name", "phone", "avatar_url", "aircraft_id", "location", "is_active")


class UserService(ProjectService[AppUser]):
    repository: UserRepository
    roles: RoleRepository
    permissions: PermissionRepository
    catalog: CatalogItemRepository

    filter_whitelist = frozenset({"is_active", "role_id", "location", "aircraft_id"})
    search_fields = ["full_name", "email"]

    # ---- validation -----------------------------------------------------------------

    async def _validate_catalog_code(self, catalog: str, code: str | None, field: str) -> None:
        if code and not await self.catalog.active_code_exists(catalog, code):
            raise AdminError(
                "validation", "El valor seleccionado no existe en el catálogo.", field=field
            )

    async def _active_role(self, role_id) -> Role:
        role = await self.roles.get(role_id)
        if role is None or not role.is_active:
            raise AdminError("validation", "El rol seleccionado no existe.", field="role_id")
        return role

    def _require_role_within_reach(self, role: Role) -> None:
        """Assigning a role grants its permissions, so the caller must hold all of them."""
        held = self.principal.permissions
        beyond = sorted(p.code for p in role.permissions if p.is_active and p.code not in held)
        if beyond:
            raise AdminError(
                "privilege",
                "No puedes asignar un rol con permisos que tú no tienes.",
                field="role_id",
            )

    async def _resolve_overrides(
        self, role: Role, granted: list[str], revoked: list[str]
    ) -> tuple[list[Permission], list[Permission]]:
        found = await self.permissions.by_codes([*granted, *revoked])
        for field, codes in (("granted_permissions", granted), ("revoked_permissions", revoked)):
            unknown = [c for c in codes if c not in found]
            if unknown:
                raise AdminError(
                    "validation", f"Permisos que no existen: {', '.join(unknown)}.", field=field
                )
        in_role = {p.code for p in role.permissions}
        both = set(granted) & set(revoked)
        if both:
            raise AdminError(
                "validation",
                "Un permiso no puede concederse y revocarse a la vez.",
                field="granted_permissions",
            )
        redundant = [c for c in granted if c in in_role]
        if redundant:
            raise AdminError(
                "validation",
                f"El rol ya incluye: {', '.join(redundant)}.",
                field="granted_permissions",
            )
        stray = [c for c in revoked if c not in in_role]
        if stray:
            raise AdminError(
                "validation",
                f"El rol no incluye, no hay nada que revocar: {', '.join(stray)}.",
                field="revoked_permissions",
            )
        beyond = [c for c in granted if c not in self.principal.permissions]
        if beyond:
            raise AdminError(
                "privilege",
                f"No puedes conceder permisos que tú no tienes: {', '.join(beyond)}.",
                field="granted_permissions",
            )
        return [found[c] for c in dict.fromkeys(granted)], [
            found[c] for c in dict.fromkeys(revoked)
        ]

    async def _guard_last_superuser(self, user: AppUser, losing_access: bool) -> None:
        if (
            losing_access
            and user.role.code == SUPERUSER_ROLE_CODE
            and await self.repository.active_superuser_count(excluding=user.id) == 0
        ):
            raise AdminError(
                "protected", "Debe quedar al menos un superusuario activo en el sistema."
            )

    # ---- commands -------------------------------------------------------------------

    async def create(self, payload, check_fields=None) -> AppUser:
        data = payload.model_dump() if hasattr(payload, "model_dump") else dict(payload)
        email = str(data["email"]).strip().lower()
        if await self.repository.email_taken(email):
            raise AdminError("duplicate", "Ya existe un usuario con ese correo.", field="email")
        role = await self._active_role(data["role_id"])
        self._require_role_within_reach(role)
        await self._validate_catalog_code("aircrafts", data.get("aircraft_id"), "aircraft_id")
        await self._validate_catalog_code("locations", data.get("location"), "location")
        granted, revoked = await self._resolve_overrides(
            role, data.get("granted_permissions") or [], data.get("revoked_permissions") or []
        )
        user = await self.repository.create(
            {
                "email": email,
                "full_name": data["full_name"].strip(),
                "phone": data.get("phone"),
                "role_id": role.id,
                "aircraft_id": data.get("aircraft_id"),
                "location": data.get("location"),
                "avatar_url": data.get("avatar_url"),
                # Nobody knows this secret: the account is unusable until a reset is issued.
                "password_hash": hash_password(generate_unusable_password()),
                "must_change_password": True,
                "is_active": True,
            }
        )
        if granted or revoked:
            user = await self.repository.replace_overrides(user, granted, revoked)
        return user

    async def update(self, id, data) -> AppUser:  # noqa: A002
        payload = data.model_dump(exclude_unset=True) if hasattr(data, "model_dump") else dict(data)
        user = await self.repository.get_scoped(id)
        if user is None:
            raise NotFoundException(f"id={id} no encontrado")

        if payload.get("signature_url"):
            raise AdminError("validation", SIGNATURE_OWNER_MESSAGE, field="signature_url")
        payload.pop("signature_url", None)

        is_self = user.id == self.principal.user_id
        privileged = {"role_id", "is_active", "granted_permissions", "revoked_permissions"}
        if is_self and privileged & payload.keys():
            raise AdminError("privilege", SELF_CHANGE_MESSAGE)
        if payload.get("is_active") is None:
            payload.pop("is_active", None)
        if payload.get("full_name") is None:
            payload.pop("full_name", None)

        role_changed = False
        target_role = user.role
        if payload.get("role_id") is not None and payload["role_id"] != user.role_id:
            target_role = await self._active_role(payload["role_id"])
            self._require_role_within_reach(target_role)
            await self._guard_last_superuser(user, losing_access=True)
            role_changed = True
        if payload.get("is_active") is False:
            await self._guard_last_superuser(user, losing_access=True)

        if "aircraft_id" in payload:
            await self._validate_catalog_code("aircrafts", payload["aircraft_id"], "aircraft_id")
        if "location" in payload:
            await self._validate_catalog_code("locations", payload["location"], "location")

        explicit = "granted_permissions" in payload or "revoked_permissions" in payload
        new_overrides = None
        if explicit or role_changed:
            current_granted = [] if role_changed else self._codes(user, "grant")
            current_revoked = [] if role_changed else self._codes(user, "revoke")
            granted = payload.get("granted_permissions")
            revoked = payload.get("revoked_permissions")
            new_overrides = await self._resolve_overrides(
                target_role,
                current_granted if granted is None else granted,
                current_revoked if revoked is None else revoked,
            )

        scalars = {k: payload[k] for k in SCALAR_FIELDS if k in payload}
        updated = await super().update(id, scalars) if scalars else user
        if role_changed:
            updated = await self.repository.assign_role(updated, target_role)
        if new_overrides is not None:
            updated = await self.repository.replace_overrides(updated, *new_overrides)
        if payload.get("is_active") is False or role_changed:
            await self.repository.revoke_tokens(updated.id)
        return updated

    @staticmethod
    def _codes(user: AppUser, effect: str) -> list[str]:
        return sorted(o.permission.code for o in user.permission_overrides if o.effect == effect)

    async def reset_password(self, id) -> str:  # noqa: A002
        user = await self.repository.get_scoped(id)
        if user is None:
            raise NotFoundException(f"id={id} no encontrado")
        if user.id == self.principal.user_id:
            raise AdminError(
                "privilege", "Para cambiar tu propia contraseña usa tu perfil.", field="id"
            )
        temporary = generate_temporary_password()
        await self.repository.set_password(user, hash_password(temporary), must_change=True)
        await self.repository.revoke_tokens(user.id)
        await self.repository.emit_sensitive(
            user,
            f"Restableció la contraseña de {user.full_name}",
            "password",
            "Contraseña",
            "restablecida",
        )
        return temporary

    # ---- queries --------------------------------------------------------------------

    async def effective_permissions(self, id) -> list[EffectivePermission]:  # noqa: A002
        user = await self.repository.get_scoped(id)
        if user is None:
            raise NotFoundException(f"id={id} no encontrado")
        return effective_breakdown(user)
