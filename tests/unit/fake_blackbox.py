"""The in-process Blackbox fake lives in the application (BLACKBOX_MODE=fake); unit tests reuse it."""

from app.integrations.blackbox.fake import (  # noqa: F401
    BASE,
    EXTENSIONS,
    MAX_BYTES,
    Credential,
    FakeBlackbox,
    StoredDoc,
    json_body,
)
