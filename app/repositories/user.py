from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.sql import Select

from app.config.access import OverrideEffect, SUPERUSER_ROLE_CODE
from app.config.audit import AuditAction, AuditEntity
from app.models.access import AppUser, Permission, Role, UserPermissionOverride
from app.repositories.audit_trail import AuditEntry
from app.repositories.base import AuditedRepository
from app.utils.effective_permissions import override_codes
from app.utils.permission_labels import GRANTED_LABEL, REVOKED_LABEL, permission_label


class UserRepository(AuditedRepository[AppUser]):
    model = AppUser
    default_ordering = ("full_name",)
    audit_entity = AuditEntity.USER
    audit_labels = {
        "full_name": "Nombre",
        "email": "Correo",
        "phone": "Teléfono",
        "location": "Sede",
        "aircraft_id": "Aeronave",
        "is_active": "Activo",
    }

    def build_list_queryset(self, **kwargs: Any) -> Select:
        return select(AppUser).order_by(AppUser.full_name, AppUser.id)

    def audit_label(self, obj: AppUser) -> str:
        return obj.email

    async def get_by_email(self, email: str) -> AppUser | None:
        query = select(AppUser).where(AppUser.email == email.strip().lower())
        return (await self.session.execute(query)).scalars().first()

    async def email_taken(self, email: str, exclude_id: Any = None) -> bool:
        query = select(AppUser.id).where(AppUser.email == email.strip().lower())
        if exclude_id is not None:
            query = query.where(AppUser.id != exclude_id)
        return (await self.session.execute(query.limit(1))).first() is not None

    async def existing_emails(self, emails: Iterable[str]) -> set[str]:
        wanted = {e.strip().lower() for e in emails}
        if not wanted:
            return set()
        query = select(AppUser.email).where(AppUser.email.in_(wanted))
        return set((await self.session.execute(query)).scalars().all())

    async def get_for_session(self, user_id: UUID) -> AppUser | None:
        """User with role, permissions and exceptions, for the access middleware."""
        return await self.session.get(AppUser, user_id)

    async def get_signature(self, user_id: UUID) -> str | None:
        query = select(AppUser.signature_url).where(AppUser.id == user_id)
        return (await self.session.execute(query)).scalar_one_or_none()

    async def active_superuser_count(self, excluding: Any = None) -> int:
        query = (
            select(func.count(AppUser.id))
            .join(Role, Role.id == AppUser.role_id)
            .where(
                Role.code == SUPERUSER_ROLE_CODE,
                AppUser.is_active.is_(True),
                Role.is_active.is_(True),
            )
        )
        if excluding is not None:
            query = query.where(AppUser.id != excluding)
        return int((await self.session.execute(query)).scalar_one())

    async def set_password(self, user: AppUser, password_hash: str, must_change: bool) -> None:
        user.password_hash = password_hash
        user.must_change_password = must_change
        await self.session.flush()

    async def record_login(self, user: AppUser) -> None:
        user.last_login_at = datetime.now(timezone.utc)
        await self.session.flush()

    async def revoke_tokens(self, user_id: UUID) -> datetime:
        """Invalidate every token issued so far for this user."""
        moment = datetime.now(timezone.utc)
        await self.session.execute(
            update(AppUser).where(AppUser.id == user_id).values(tokens_valid_after=moment)
        )
        await self.session.flush()
        return moment

    async def set_signature(self, user: AppUser, data_url: str | None) -> None:
        user.signature_url = data_url
        user.signature_updated_at = datetime.now(timezone.utc) if data_url else None
        await self.session.flush()

    async def assign_role(self, user: AppUser, role: Role) -> AppUser:
        if user.role_id == role.id:
            return user
        old_name = user.role.name
        user.role_id = role.id
        await self.session.flush()
        await self.session.refresh(user)
        await self.emit(
            AuditEntry(
                action=AuditAction.PERMISSION_CHANGE,
                entity=AuditEntity.USER,
                entity_id=str(user.id),
                entity_label=user.email,
                summary=f"Cambió el rol de {user.full_name}: {old_name} a {role.name}",
                changes=[{"field": "role", "label": "Rol", "from": old_name, "to": role.name}],
            )
        )
        return user

    async def replace_overrides(
        self,
        user: AppUser,
        granted: Iterable[Permission],
        revoked: Iterable[Permission],
    ) -> AppUser:
        new_granted = {p.code: p for p in granted}
        new_revoked = {p.code: p for p in revoked}
        old_granted = override_codes(user, OverrideEffect.GRANT)
        old_revoked = override_codes(user, OverrideEffect.REVOKE)
        if old_granted == set(new_granted) and old_revoked == set(new_revoked):
            return user

        known = {o.permission.code: o.permission for o in user.permission_overrides}
        user.permission_overrides.clear()
        await self.session.flush()
        for effect, group in (
            (OverrideEffect.GRANT, new_granted),
            (OverrideEffect.REVOKE, new_revoked),
        ):
            for permission in group.values():
                user.permission_overrides.append(
                    UserPermissionOverride(
                        user_id=user.id, permission_id=permission.id, effect=effect.value
                    )
                )
        await self.session.flush()
        await self.session.refresh(user)

        def label(code: str) -> str:
            permission = new_granted.get(code) or new_revoked.get(code) or known.get(code)
            return permission_label(permission) if permission else code

        changes: list[dict[str, Any]] = []
        for code in sorted(set(new_granted) - old_granted):
            changes.append(
                {"field": "granted", "label": label(code), "from": None, "to": GRANTED_LABEL}
            )
        for code in sorted(old_granted - set(new_granted)):
            changes.append(
                {"field": "granted", "label": label(code), "from": GRANTED_LABEL, "to": None}
            )
        for code in sorted(set(new_revoked) - old_revoked):
            changes.append(
                {"field": "revoked", "label": label(code), "from": None, "to": REVOKED_LABEL}
            )
        for code in sorted(old_revoked - set(new_revoked)):
            changes.append(
                {"field": "revoked", "label": label(code), "from": REVOKED_LABEL, "to": None}
            )
        await self.emit(
            AuditEntry(
                action=AuditAction.PERMISSION_CHANGE,
                entity=AuditEntity.USER,
                entity_id=str(user.id),
                entity_label=user.email,
                summary=f"Cambió las excepciones de permisos de {user.full_name}",
                changes=changes,
            )
        )
        return user

    async def emit_sensitive(
        self, user: AppUser, summary: str, field: str, label: str, to: Any, before: Any = None
    ) -> None:
        """Sensitive account fact (signature, password) recorded without its content."""
        await self.emit(
            AuditEntry(
                action=AuditAction.PERMISSION_CHANGE,
                entity=AuditEntity.USER,
                entity_id=str(user.id),
                entity_label=user.email,
                summary=summary,
                changes=[{"field": field, "label": label, "from": before, "to": to}],
            )
        )
