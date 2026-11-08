"""Import every model so Base.metadata is complete (Alembic autogenerate relies on it)."""

from app.models.access import (  # noqa: F401
    AppUser,
    Module,
    Permission,
    Role,
    UserPermissionOverride,
    role_permission,
)
from app.models.attachment import AttachmentUpload, TicketAttachment  # noqa: F401
from app.models.audit import AuditEvent  # noqa: F401
from app.models.base import Base  # noqa: F401
from app.models.catalog import CatalogItem  # noqa: F401
from app.models.notification import (  # noqa: F401
    Notification,
    NotificationAudience,
    NotificationReadMark,
)
from app.models.ticket import (  # noqa: F401
    BusinessRule,
    IdempotencyRecord,
    Ticket,
    TicketComment,
    TicketCounter,
    TicketDecision,
    TicketExtraction,
    TicketType,
    TicketTypeField,
    TicketTypeStatus,
    TicketTypeStep,
)
from app.models.closing import TicketClosing  # noqa: F401
from app.models.passenger import TicketPassenger  # noqa: F401
from app.models.reconciliation import (  # noqa: F401
    ReconciliationAcknowledgement,
    ReconciliationPeriod,
)
