"""Smoke test OPT-IN contra un Blackbox real. No corre en CI ni en pytest.

Uso:
    BLACKBOX_BASE_URL=https://... BLACKBOX_MAESTRO_TOKEN=... python scripts/blackbox_smoke.py
Contra producción (blackboxve.store) exige además --allow-production y
BLACKBOX_SMOKE_CONFIRM=yes.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.integrations.blackbox import BlackboxClient, BlackboxError  # noqa: E402

PRODUCTION_HOST = "blackboxve.store"
SMOKE_CLIENT_ID = "SMOKE-TEST"
# PNG 1x1 válido.
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)


def _fail(message: str) -> None:
    print(f"ABORTADO: {message}", file=sys.stderr)
    sys.exit(2)


async def run(base_url: str, token: str) -> int:
    key = f"smoke:{uuid.uuid4()}"
    async with BlackboxClient(base_url, token, timeout=20.0) as client:
        try:
            doc = await client.upload_document(TINY_PNG, "smoke.png", SMOKE_CLIENT_ID, "smoke", key)
            print(f"upload: id={doc.id} status={doc.status} size={doc.size}")
            refs = await client.list_documents(SMOKE_CLIENT_ID)
            print(f"list: {len(refs)} documento(s); presente={any(r.id == doc.id for r in refs)}")
            await client.delete_document(doc.id)
            print("delete: ok")
        except BlackboxError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)  # str(exc) no incluye el token
            return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-production", action="store_true")
    args = parser.parse_args()

    base_url = os.environ.get("BLACKBOX_BASE_URL", "").strip()
    token = os.environ.get("BLACKBOX_MAESTRO_TOKEN", "").strip()
    if not base_url or not token:
        _fail("faltan BLACKBOX_BASE_URL y/o BLACKBOX_MAESTRO_TOKEN en el entorno")

    host = (urlsplit(base_url).hostname or "").lower()
    if host == PRODUCTION_HOST or host.endswith("." + PRODUCTION_HOST):
        if not (args.allow_production and os.environ.get("BLACKBOX_SMOKE_CONFIRM") == "yes"):
            _fail("base de producción: requiere --allow-production y BLACKBOX_SMOKE_CONFIRM=yes")

    print(f"destino: {host}")
    sys.exit(asyncio.run(run(base_url, token)))


if __name__ == "__main__":
    main()
