"""Integración con Blackbox (gestión documental segura de Axiom)."""

from .client import BlackboxClient, DocumentRef, StoredDocument
from .errors import (
    BlackboxCredentialError,
    BlackboxError,
    BlackboxNetworkError,
    BlackboxNotFoundError,
    BlackboxValidationError,
)
from .factory import build_client, build_vault
from .service import AttachmentVault, RetryPolicy

__all__ = [
    "AttachmentVault",
    "BlackboxClient",
    "BlackboxCredentialError",
    "BlackboxError",
    "BlackboxNetworkError",
    "BlackboxNotFoundError",
    "BlackboxValidationError",
    "build_client",
    "build_vault",
    "DocumentRef",
    "RetryPolicy",
    "StoredDocument",
]
