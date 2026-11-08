"""Idempotent seed: `python -m app.scripts.init`.

Loads app/fixtures/seeds.json in contract order (modules, permissions, roles, users,
catalogs) and then runs the registered hooks. Existing rows are never overwritten, so
what an administrator changed survives a re-run. The only thing re-synchronised on every
run is the superuser role, which must always hold the whole permission catalog.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import database
from app.config.access import SEED_ALL_PERMISSIONS, SUPERUSER_ROLE_CODE
from app.config.audit import AuditAction, AuditEntity
from app.config.auth import PASSWORD_MIN_LENGTH
from app.config.security import hash_password
from app.config.settings import get_settings
from app.repositories.audit_trail import AuditEntry, AuditEventRepository
from app.repositories.catalog import CatalogItemRepository
from app.repositories.module import ModuleRepository
from app.repositories.permission import PermissionRepository
from app.repositories.role import RoleRepository
from app.repositories.user import UserRepository
from app.scripts.seed_hooks import SEED_HOOKS
from app.utils.audit_context import SYSTEM_ACTOR, without_audit
from app.utils.seed_loader import SeedData, load_seeds

logger = logging.getLogger(__name__)

DEMO_PASSWORD = "demo1234"


@dataclass
class SeedReport:
    created: dict[str, int] = field(default_factory=dict)

    def add(self, kind: str, count: int = 1) -> None:
        self.created[kind] = self.created.get(kind, 0) + count

    @property
    def total(self) -> int:
        return sum(self.created.values())


def seed_password() -> tuple[str, bool]:
    """(password, must_change). Demo credentials are never created in production."""
    settings = get_settings()
    if settings.SEED_USER_PASSWORD:
        if len(settings.SEED_USER_PASSWORD) < PASSWORD_MIN_LENGTH and settings.is_production:
            raise RuntimeError(
                f"SEED_USER_PASSWORD needs at least {PASSWORD_MIN_LENGTH} characters"
            )
        return settings.SEED_USER_PASSWORD, settings.is_production
    if settings.is_production:
        raise RuntimeError("Set SEED_USER_PASSWORD to seed users in production")
    return DEMO_PASSWORD, False


async def _seed_modules(session: AsyncSession, data: SeedData, report: SeedReport) -> None:
    repo = ModuleRepository(session)
    for item in data.modules:
        if await repo.get_by_code(item["code"]):
            continue
        await repo.create(
            {
                "code": item["code"],
                "name": item["name"],
                "description": item.get("description"),
                "route": item.get("route"),
                "icon": item.get("icon"),
                "menu_permission": item.get("menu_permission"),
                "show_in_menu": item.get("show_in_menu", False),
                "sort_order": item.get("sort_order", 0),
                "is_system": item.get("is_system", False),
                "is_active": item.get("is_active", True),
            }
        )
        report.add("modules")


async def _seed_permissions(session: AsyncSession, data: SeedData, report: SeedReport) -> None:
    modules = ModuleRepository(session)
    permissions = PermissionRepository(session)
    for item in data.permissions:
        if await permissions.get_by_code(item["code"]):
            continue
        module = await modules.get_by_code(item["module_code"])
        if module is None:
            raise RuntimeError(f"Permission {item['code']} points to unknown module")
        await permissions.create_seeded(
            item["code"],
            item["name"],
            module,
            item.get("description"),
            item.get("is_system", False),
        )
        report.add("permissions")


async def _seed_roles(
    session: AsyncSession, data: SeedData, report: SeedReport, created: set[str]
) -> None:
    roles = RoleRepository(session)
    permissions = PermissionRepository(session)
    for item in data.roles:
        if await roles.get_by_code(item["code"]):
            continue
        wanted = item["permissions"]
        if wanted == SEED_ALL_PERMISSIONS:
            granted = await permissions.all_permissions()
        else:
            found = await permissions.by_codes(wanted)
            missing = [c for c in wanted if c not in found]
            if missing:
                raise RuntimeError(f"Role {item['code']} lists unknown permissions: {missing}")
            granted = [found[c] for c in wanted]
        await roles.create_with_permissions(
            {
                "code": item["code"],
                "name": item["name"],
                "description": item.get("description"),
                "is_system": item.get("is_system", False),
                "is_active": True,
                "has_all_permissions": wanted == SEED_ALL_PERMISSIONS
                or item["code"] == SUPERUSER_ROLE_CODE,
            },
            granted,
        )
        report.add("roles")
        created.add(item["code"])
    added = await permissions.sync_superuser_roles()
    if added:
        report.add("superuser_links", added)


async def _seed_role_type_grants(
    session: AsyncSession, data: SeedData, report: SeedReport, created: set[str]
) -> None:
    """Per-type permissions for the roles created in THIS run (after the types exist).

    A role that already existed is left alone, so what an administrator removed from it is
    not granted again on the next boot."""
    roles, permissions = RoleRepository(session), PermissionRepository(session)
    for role_code, wanted in data.role_type_grants.items():
        if role_code not in created:
            continue
        role = await roles.get_by_code(role_code)
        found = await permissions.by_codes(wanted)
        missing = [c for c in wanted if c not in found]
        if role is None or missing:
            raise RuntimeError(f"Role {role_code} lists unknown type permissions: {missing}")
        await roles.replace_permissions(role, [*role.permissions, *found.values()])
        report.add("role_type_grants", len(found))


async def _seed_users(session: AsyncSession, data: SeedData, report: SeedReport) -> None:
    users = UserRepository(session)
    roles = RoleRepository(session)
    pending = [u for u in data.users if not await users.get_by_email(u["email"])]
    if not pending:
        return
    password, must_change = seed_password()
    for item in pending:
        role = await roles.get_by_code(item["role_code"])
        if role is None:
            raise RuntimeError(f"User {item['email']} points to unknown role {item['role_code']}")
        await users.create(
            {
                "email": item["email"].lower(),
                "full_name": item["full_name"],
                "role_id": role.id,
                "location": item.get("location"),
                "aircraft_id": item.get("aircraft_code"),
                "avatar_url": item.get("avatar_url"),
                "password_hash": hash_password(password),
                "must_change_password": must_change,
                "is_active": True,
            }
        )
        report.add("users")


async def _seed_catalogs(session: AsyncSession, data: SeedData, report: SeedReport) -> None:
    catalogs = CatalogItemRepository(session)
    for key, items in data.catalogs.items():
        for item in items:
            if await catalogs.get_by_catalog_code(key, item["code"]):
                continue
            await catalogs.create(
                {
                    "catalog": key,
                    "code": item["code"],
                    "name": item["name"],
                    "extra": item.get("extra"),
                    "is_system": item.get("is_system", False),
                    "is_active": True,
                    "sort_order": item.get("sort_order", 0),
                }
            )
            report.add("catalog_items")


async def run_seed(session: AsyncSession) -> SeedReport:
    data = load_seeds()
    report = SeedReport()
    with without_audit():
        await _seed_modules(session, data, report)
        await _seed_permissions(session, data, report)
        created_roles: set[str] = set()
        await _seed_roles(session, data, report, created_roles)
        await _seed_users(session, data, report)
        await _seed_catalogs(session, data, report)
        for hook in SEED_HOOKS:
            created = await hook(session)
            if created:
                report.add(hook.__name__, created)
        await _seed_role_type_grants(session, data, report, created_roles)
    if report.total:
        # One summary event instead of one per seeded row.
        detail = ", ".join(f"{count} {kind}" for kind, count in sorted(report.created.items()))
        await AuditEventRepository(session).append(
            AuditEntry(
                action=AuditAction.CREATE,
                entity=AuditEntity.CATALOG,
                entity_id="seed",
                entity_label="Carga inicial",
                summary=f"Carga inicial de datos: {detail}",
                actor=SYSTEM_ACTOR,
            )
        )
    return report


async def main() -> SeedReport:
    async with database.AsyncSessionFactory() as session:
        report = await run_seed(session)
        await session.commit()
    await database.close_db()
    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = asyncio.run(main())
    print(f"seed done: {result.created or 'nothing to create'}")
