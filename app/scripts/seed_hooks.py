"""Extension point of the seed script, for modules built on top of this foundation.

Each hook is `async def hook(session: AsyncSession) -> int` returning how many rows it
created. It runs inside the seed transaction, after modules, permissions, roles, users
and catalogs exist, with auditing suppressed. Hooks must be idempotent (create only what
is missing).

Registered here: `seed_ticket_types` (tickets module).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

SeedHook = Callable[[AsyncSession], Awaitable[int]]


async def seed_ticket_types(session: AsyncSession) -> int:
    """Initial ticket types, their derived permissions and the starting business rules.

    Existing types and rules are left alone: what an administrator edited survives a re-run.
    """
    from app.repositories.business_rule import BusinessRuleRepository
    from app.repositories.permission import PermissionRepository
    from app.repositories.role import RoleRepository
    from app.repositories.ticket_type import TicketTypeRepository
    from app.services.permission_service import PermissionService
    from app.utils.seed_loader import load_seeds
    from app.utils.type_schema import normalize_fields, normalize_statuses, normalize_steps

    data = load_seeds()
    roles, types = RoleRepository(session), TicketTypeRepository(session)
    permissions = PermissionService(PermissionRepository(session), request=None)
    created = 0
    for seed in data.ticket_types:
        await permissions.register_type_permissions(seed["code"], seed["name"])
        if await types.code_taken(seed["code"]):
            continue

        async def role_id(code: str | None):
            if not code:
                return None
            role = await roles.get_by_code(code)
            if role is None:
                raise RuntimeError(f"Ticket type {seed['code']} points to unknown role {code}")
            return role.id

        steps = [{**s, "role_id": await role_id(s.get("role_code"))} for s in seed.get("steps", [])]
        await types.create_type(
            {
                "code": seed["code"],
                "name": seed["name"],
                "description": seed.get("description"),
                "is_root": seed["is_root"],
                "child_type_codes": seed.get("child_type_codes", []),
                "default_role_id": await role_id(seed.get("default_role_code")),
                "blocks_parent_close": seed.get("blocks_parent_close", False),
                "relation_kinds": seed.get("relation_kinds", []),
                "is_system": seed.get("is_system", False),
                "is_active": True,
            },
            normalize_fields(seed["fields"]),
            normalize_statuses(seed["statuses"]),
            normalize_steps(steps),
        )
        created += 1

    rules = BusinessRuleRepository(session)
    for seed in data.business_rules:
        ticket_type = await types.get_by_code(seed["type_code"])
        if ticket_type is None or await rules.exists(ticket_type.id, seed["name"]):
            continue
        await rules.create(
            {
                "type_id": ticket_type.id,
                "name": seed["name"],
                "field": seed["field"],
                "operator": seed["operator"],
                "value": seed.get("value"),
                "action": seed["action"],
                "message": seed["message"],
                "route_limits": seed.get("route_limits"),
                "is_active": True,
            }
        )
        created += 1
    await PermissionRepository(session).sync_superuser_roles()
    return created


SEED_HOOKS: list[SeedHook] = [seed_ticket_types]
