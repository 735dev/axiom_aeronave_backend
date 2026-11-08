"""Service factories: the only place where repositories are wired into services."""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.config.settings import get_settings
from app.integrations.receipt_reader import ReceiptReadingService, build_reader
from app.repositories.attachment import AttachmentRepository
from app.repositories.audit_trail import AuditEventRepository
from app.repositories.business_rule import BusinessRuleRepository
from app.repositories.catalog import CatalogItemRepository
from app.repositories.health import HealthRepository
from app.repositories.module import ModuleRepository
from app.repositories.notification import NotificationRepository
from app.repositories.permission import PermissionRepository
from app.repositories.closing import TicketClosingRepository
from app.repositories.passenger import TicketPassengerRepository
from app.repositories.reconciliation import (
    ReconciliationPeriodRepository,
    ReconciliationRepository,
)
from app.repositories.role import RoleRepository
from app.repositories.ticket import TicketRepository
from app.repositories.ticket_analytics import TicketAnalyticsRepository
from app.repositories.ticket_extraction import TicketExtractionRepository
from app.repositories.ticket_support import TicketSupportRepository
from app.repositories.ticket_type import TicketTypeRepository
from app.repositories.user import UserRepository
from app.services import closing_hooks, notification_hooks  # noqa: F401
from app.services.attachment_service import AttachmentService
from app.services.audit_service import AuditService
from app.services.closing_service import ClosingService
from app.services.auth_service import AuthService
from app.services.catalog_service import CatalogService
from app.services.health_service import HealthService
from app.services.module_service import ModuleService
from app.services.notification_service import NotificationService
from app.services.passenger_service import PassengerService
from app.services.permission_service import PermissionService
from app.services.reconciliation_service import ReconciliationService
from app.services.role_service import RoleService
from app.services.rule_service import RuleService
from app.services.signature_service import SignatureService
from app.services.ticket_service import TicketService
from app.services.ticket_totals_service import TicketTotalsService
from app.services.ticket_type_service import TicketTypeService
from app.services.user_service import UserService

closing_hooks.register()


def get_auth_service(request: Request, db: AsyncSession = Depends(get_db)) -> AuthService:
    return AuthService(UserRepository(db), request=request, events=AuditEventRepository(db))


def get_signature_service(request: Request, db: AsyncSession = Depends(get_db)) -> SignatureService:
    return SignatureService(UserRepository(db), request=request)


def get_module_service(request: Request, db: AsyncSession = Depends(get_db)) -> ModuleService:
    return ModuleService(
        ModuleRepository(db), request=request, permissions=PermissionRepository(db)
    )


def get_permission_service(
    request: Request, db: AsyncSession = Depends(get_db)
) -> PermissionService:
    return PermissionService(PermissionRepository(db), request=request)


def get_role_service(request: Request, db: AsyncSession = Depends(get_db)) -> RoleService:
    return RoleService(RoleRepository(db), request=request, permissions=PermissionRepository(db))


def get_user_service(request: Request, db: AsyncSession = Depends(get_db)) -> UserService:
    return UserService(
        UserRepository(db),
        request=request,
        roles=RoleRepository(db),
        permissions=PermissionRepository(db),
        catalog=CatalogItemRepository(db),
    )


def get_catalog_service(request: Request, db: AsyncSession = Depends(get_db)) -> CatalogService:
    return CatalogService(CatalogItemRepository(db), request=request)


def get_audit_service(request: Request, db: AsyncSession = Depends(get_db)) -> AuditService:
    return AuditService(AuditEventRepository(db), request=request)


def get_health_service(request: Request, db: AsyncSession = Depends(get_db)) -> HealthService:
    return HealthService(HealthRepository(db), request=request)


def get_ticket_type_service(
    request: Request, db: AsyncSession = Depends(get_db)
) -> TicketTypeService:
    return TicketTypeService(
        TicketTypeRepository(db),
        request=request,
        roles=RoleRepository(db),
        permissions=PermissionRepository(db),
    )


def get_rule_service(request: Request, db: AsyncSession = Depends(get_db)) -> RuleService:
    return RuleService(BusinessRuleRepository(db), request=request, types=TicketTypeRepository(db))


def _ticket_collaborators(db: AsyncSession) -> dict:
    """Repositories every ticket-flavoured service works with."""
    return {
        "types": TicketTypeRepository(db),
        "catalog": CatalogItemRepository(db),
        "roles": RoleRepository(db),
        "users": UserRepository(db),
        "rules": BusinessRuleRepository(db),
        "extractions": TicketExtractionRepository(db),
        "events": AuditEventRepository(db),
        "notifications": NotificationRepository(db),
        "periods": ReconciliationPeriodRepository(db),
        "closings": TicketClosingRepository(db),
        "supports": TicketSupportRepository(db),
    }


def get_ticket_service(request: Request, db: AsyncSession = Depends(get_db)) -> TicketService:
    return TicketService(TicketRepository(db), request=request, **_ticket_collaborators(db))


def get_ticket_totals_service(
    request: Request, db: AsyncSession = Depends(get_db)
) -> TicketTotalsService:
    return TicketTotalsService(
        TicketAnalyticsRepository(db), request=request, **_ticket_collaborators(db)
    )


def get_closing_service(request: Request, db: AsyncSession = Depends(get_db)) -> ClosingService:
    return ClosingService(
        TicketClosingRepository(db), request=request, supports=TicketSupportRepository(db)
    )


def get_passenger_service(request: Request, db: AsyncSession = Depends(get_db)) -> PassengerService:
    return PassengerService(
        TicketPassengerRepository(db), request=request, supports=TicketSupportRepository(db)
    )


def get_reconciliation_service(
    request: Request, db: AsyncSession = Depends(get_db)
) -> ReconciliationService:
    return ReconciliationService(
        ReconciliationRepository(db),
        request=request,
        periods=ReconciliationPeriodRepository(db),
    )


@lru_cache
def get_receipt_reader() -> ReceiptReadingService | None:
    """One reader per process, chosen by `RECEIPT_READER`; None means reading is off."""
    return build_reader(get_settings())


def get_attachment_service(
    request: Request, db: AsyncSession = Depends(get_db)
) -> AttachmentService:
    return AttachmentService(
        AttachmentRepository(db),
        request=request,
        tickets=get_ticket_service(request, db),
        extractions=TicketExtractionRepository(db),
        reader=get_receipt_reader(),
    )


def get_notification_service(
    request: Request, db: AsyncSession = Depends(get_db)
) -> NotificationService:
    return NotificationService(
        NotificationRepository(db), request=request, roles=RoleRepository(db)
    )
