"""FastAPI entry point."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi_basekit.exceptions.api_exceptions import APIException
from fastapi_basekit.exceptions.domain import DomainError
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.endpoints import health
from app.api.v1.routers import api_router
from app.config.database import lifespan
from app.config.settings import get_settings
from app.middleware.access import AccessMiddleware
from app.middleware.idempotency import IdempotencyMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.middleware.upload_limit import UploadLimitMiddleware
from app.utils import exception_handlers
from app.utils.observability import init_sentry, init_stack_dump

settings = get_settings()

TAGS_METADATA = [
    {"name": "Health", "description": "Liveness and readiness probes (public)."},
    {"name": "Auth", "description": "Login, refresh, logout, own profile, password and signature."},
    {"name": "Modules", "description": "Modules that drive the side menu (seed-only)."},
    {"name": "Permissions", "description": "Closed permission catalog (read-only)."},
    {"name": "Roles", "description": "Roles and their permissions."},
    {
        "name": "Users",
        "description": "Users, per-user permission exceptions and effective permissions.",
    },
    {"name": "Catalogs", "description": "Administrable lists that feed the forms."},
    {"name": "Audit", "description": "Append-only audit trail."},
    {"name": "Ticket types", "description": "Dynamic ticket definitions."},
    {"name": "Tickets", "description": "Operations, expenses, reports: all the work."},
    {"name": "Rules", "description": "Business rules applied to tickets."},
    {"name": "Attachments", "description": "Files archived in Blackbox and receipt reading."},
    {"name": "Notifications", "description": "Event notices addressed by role or user."},
]

# Headers the browser client may send / read. Explicit: no wildcard with credentials.
CORS_ALLOW_HEADERS = ["Authorization", "Content-Type", "Accept-Language", "Idempotency-Key"]
CORS_EXPOSE_HEADERS = ["Retry-After"]
CORS_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


def create_application() -> FastAPI:
    init_sentry()
    init_stack_dump()
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        description=settings.DESCRIPTION,
        openapi_tags=TAGS_METADATA,
        lifespan=lifespan,
        debug=False,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )

    app.add_exception_handler(StarletteHTTPException, exception_handlers.http_exception_handler)
    app.add_exception_handler(RequestValidationError, exception_handlers.request_validation_handler)
    app.add_exception_handler(APIException, exception_handlers.api_exception_handler)
    app.add_exception_handler(DomainError, exception_handlers.domain_error_handler)
    app.add_exception_handler(IntegrityError, exception_handlers.integrity_error_handler)

    # add_middleware puts the newest in front. CORS goes last so it wraps everything,
    # including the 401/403/500 produced by the layers inside it.
    # First added is innermost: it runs after the access middleware has set the principal.
    app.add_middleware(UploadLimitMiddleware)
    app.add_middleware(IdempotencyMiddleware)
    app.add_middleware(AccessMiddleware, fastapi_app=app)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=CORS_METHODS,
        allow_headers=CORS_ALLOW_HEADERS,
        expose_headers=CORS_EXPOSE_HEADERS,
        max_age=600,
    )

    app.include_router(health.router)
    app.include_router(api_router, prefix=settings.API_V1_STR)
    return app


app = create_application()
