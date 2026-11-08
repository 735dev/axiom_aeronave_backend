from fastapi import APIRouter

from app.api.v1.endpoints import (
    attachment,
    audit,
    auth,
    catalog,
    closing,
    module,
    notification,
    passenger,
    permission,
    profile,
    reconciliation,
    role,
    rule,
    ticket,
    ticket_totals,
    ticket_type,
    user,
)

api_router = APIRouter()
for module_router in (
    auth.router,
    profile.router,
    module.router,
    permission.router,
    role.router,
    user.router,
    catalog.router,
    audit.router,
    ticket_type.router,
    ticket_totals.router,
    ticket.router,
    closing.router,
    passenger.manifest_router,
    passenger.router,
    reconciliation.router,
    attachment.ticket_router,
    attachment.router,
    notification.router,
    rule.router,
):
    api_router.include_router(module_router)
