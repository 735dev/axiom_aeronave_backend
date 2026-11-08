"""Selects the receipt reader from settings (`RECEIPT_READER=fake|anthropic|off`).

Production never serves the fake: a made-up reading of a real receipt is worse than none.
With no usable provider the result is None and reading is simply off (manual entry).
"""

from __future__ import annotations

import logging

from app.config.settings import Settings

from .anthropic_reader import AnthropicReceiptReader
from .fake_reader import FakeReceiptReader
from .service import ReaderRetry, ReceiptReadingService

logger = logging.getLogger(__name__)


def build_reader(settings: Settings) -> ReceiptReadingService | None:
    mode = settings.RECEIPT_READER.lower()
    if mode == "fake":
        if settings.is_production:
            logger.warning("receipt_reader_fake_refused_in_production")
            return None
        return ReceiptReadingService(FakeReceiptReader(), ReaderRetry(base_delay=0.0))
    if mode == "anthropic":
        if not settings.ANTHROPIC_API_KEY:
            logger.error("receipt_reader_no_api_key")
            return None
        try:
            reader = AnthropicReceiptReader(
                settings.ANTHROPIC_API_KEY,
                model=settings.RECEIPT_READER_MODEL,
                timeout=settings.RECEIPT_READER_TIMEOUT_SECONDS,
            )
        except ValueError:
            logger.error("receipt_reader_misconfigured")
            return None
        return ReceiptReadingService(reader)
    return None
