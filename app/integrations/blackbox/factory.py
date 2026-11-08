"""Builds the Blackbox client the application uses, from settings only.

`BLACKBOX_MODE=fake` serves an in-process stand-in (tests, local work). `real` needs
`BLACKBOX_BASE_URL` and `BLACKBOX_MAESTRO_TOKEN`; if either is missing nothing is built and
the caller must fail closed: it never falls back to the fake.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from app.config.settings import Settings

from .client import BlackboxClient
from .fake import FakeBlackbox
from .service import AttachmentVault, RetryPolicy

logger = logging.getLogger(__name__)

FAKE_BASE_URL = "https://blackbox.fake.invalid"
FAKE_TOKEN = "bb_maestro_FAKE_NOT_A_SECRET"


@lru_cache
def fake_blackbox() -> FakeBlackbox:
    """The one in-process fake of this process (shared by every request)."""
    fake = FakeBlackbox()
    fake.add_token(FAKE_TOKEN, company="aeronaves")
    return fake


def build_client(settings: Settings) -> BlackboxClient | None:
    if settings.BLACKBOX_MODE.lower() == "fake":
        return BlackboxClient(
            FAKE_BASE_URL,
            FAKE_TOKEN,
            timeout=settings.BLACKBOX_TIMEOUT_SECONDS,
            transport=fake_blackbox().transport(),
        )
    if not settings.BLACKBOX_BASE_URL or not settings.BLACKBOX_MAESTRO_TOKEN:
        logger.error("blackbox_not_configured")
        return None
    try:
        return BlackboxClient(
            settings.BLACKBOX_BASE_URL,
            settings.BLACKBOX_MAESTRO_TOKEN,
            timeout=settings.BLACKBOX_TIMEOUT_SECONDS,
        )
    except ValueError:
        # The message never carries the token or the URL; log only that it is invalid.
        logger.error("blackbox_misconfigured")
        return None


def build_vault(client: BlackboxClient, settings: Settings) -> AttachmentVault:
    return AttachmentVault(client, RetryPolicy(base_delay=settings.BLACKBOX_RETRY_BASE_DELAY))
