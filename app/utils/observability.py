"""Optional Sentry/GlitchTip wiring. A no-op without SENTRY_DSN or without the library."""

from __future__ import annotations

import faulthandler
import logging
import signal
import sys

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


def init_sentry() -> None:
    settings = get_settings()
    if not settings.SENTRY_DSN:
        return
    try:
        import sentry_sdk
    except ImportError:
        logger.warning("SENTRY_DSN is set but sentry-sdk is not installed")
        return
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.SENTRY_ENVIRONMENT or settings.ENVIRONMENT,
        release=settings.SENTRY_RELEASE,
        send_default_pii=False,
    )


def init_stack_dump() -> None:
    """`kill -USR1 <pid>` prints every thread's stack to stderr.

    It is a C-level handler, so it answers even when the event loop is spinning and no Python
    code gets to run: the way to see WHERE a worker sits at 100% CPU. (Under gunicorn the
    master forwards USR1 to its workers.) `PYTHONFAULTHANDLER=1` in the entrypoints adds the
    dump on fatal signals.
    """
    if not hasattr(signal, "SIGUSR1"):
        return
    try:
        faulthandler.register(signal.SIGUSR1, file=sys.stderr, all_threads=True)
    except (OSError, ValueError, RuntimeError, AttributeError):
        logger.warning("could not register the USR1 stack dump", exc_info=True)
