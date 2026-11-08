"""Archive-then-purge of the audit trail:
`python -m app.scripts.audit_archive [--dry-run] [--older-than-days N]`.

The trail is append-only for the application: the runtime role (`aero_app`) has no DELETE
on `audit_event`, and neither the repository nor the ORM can remove a row. Retention is the
one legitimate way a row leaves, and it goes through THIS script with a different, privileged
connection (MAINTENANCE_DATABASE_URL, role of docker/postgres/audit_maintenance.sql: SELECT,
INSERT, DELETE on audit_event and nothing else). Order of work, so nothing is lost:

1. refuse to run if the line is younger than AUDIT_RETENTION_FLOOR_DAYS, or if the
   connection has no DELETE privilege (checked BEFORE writing anything);
2. stream the rows older than the retention line into a gzip JSON-lines file in
   AUDIT_ARCHIVE_DIR (lossless: every column), plus a `.sha256` next to it;
3. re-read the file and check row count and checksum;
4. only then delete exactly the archived rows and append one summary event (file name,
   checksum, count, cutoff) in the same transaction. A failure leaves the rows in place.

Exit codes: 0 done (or nothing to do), 2 refused by policy, 3 refused by privileges,
4 archive verification failed (nothing deleted).
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config.audit import SYSTEM_ACTOR_ID, SYSTEM_ACTOR_NAME, SYSTEM_ACTOR_ROLE
from app.config.audit import AuditAction, AuditEntity
from app.config.settings import get_settings
from app.models.audit import AuditEvent

logger = logging.getLogger(__name__)

TABLE = AuditEvent.__table__
COLUMNS = [c.name for c in TABLE.columns]
DELETE_CHUNK = 5000

EXIT_OK, EXIT_POLICY, EXIT_PRIVILEGE, EXIT_VERIFY = 0, 2, 3, 4


@dataclass
class ArchiveResult:
    code: int
    archived: int = 0
    deleted: int = 0
    path: Path | None = None
    sha256: str | None = None
    message: str = ""


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if isinstance(value, UUID) else value


def _write_archive(rows: list[dict[str, Any]], directory: Path, cutoff: datetime) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    first, last = rows[0]["seq"], rows[-1]["seq"]
    name = f"audit-{cutoff:%Y%m%d}-seq{first}-{last}.jsonl.gz"
    final = directory / name
    partial = directory / (name + ".part")
    with open(partial, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as packed:
            for row in rows:
                line = json.dumps(
                    {k: _jsonable(v) for k, v in row.items()}, ensure_ascii=False, sort_keys=True
                )
                packed.write(line.encode("utf-8") + b"\n")
        raw.flush()
        os.fsync(raw.fileno())
    os.replace(partial, final)
    digest = hashlib.sha256(final.read_bytes()).hexdigest()
    (directory / (name + ".sha256")).write_text(f"{digest}  {name}\n", encoding="utf-8")
    return final


def verify_archive(path: Path, expected_rows: int) -> str | None:
    """sha256 of the archive when it matches its checksum file and holds `expected_rows`
    lines; None otherwise."""
    checksum_file = path.with_name(path.name + ".sha256")
    if not path.exists() or not checksum_file.exists():
        return None
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if checksum_file.read_text(encoding="utf-8").split()[0] != digest:
        return None
    with gzip.open(path, "rb") as packed:
        lines = sum(1 for line in packed if line.strip())
    return digest if lines == expected_rows else None


async def run(
    *, dry_run: bool = False, older_than_days: int | None = None, now: datetime | None = None
) -> ArchiveResult:
    settings = get_settings()
    days = settings.AUDIT_RETENTION_DAYS if older_than_days is None else older_than_days
    if days <= 0:
        return ArchiveResult(EXIT_OK, message="retention disabled (AUDIT_RETENTION_DAYS=0)")
    if days < settings.AUDIT_RETENTION_FLOOR_DAYS:
        return ArchiveResult(
            EXIT_POLICY,
            message=f"{days} days is below the floor of {settings.AUDIT_RETENTION_FLOOR_DAYS}",
        )
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    engine = create_async_engine(settings.maintenance_database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            allowed = (
                await conn.execute(
                    text("SELECT has_table_privilege(current_user, 'audit_event', 'DELETE')")
                )
            ).scalar_one()
            if not allowed:
                return ArchiveResult(
                    EXIT_PRIVILEGE,
                    message="this connection cannot DELETE on audit_event; use the maintenance "
                    "role (MAINTENANCE_DATABASE_URL), not the runtime one",
                )
            result = await conn.execute(
                select(*TABLE.columns).where(TABLE.c.at < cutoff).order_by(TABLE.c.seq)
            )
            rows = [dict(r._mapping) for r in result]
            await conn.rollback()
            if not rows:
                return ArchiveResult(EXIT_OK, message="nothing older than the retention line")
            if dry_run:
                return ArchiveResult(EXIT_OK, archived=len(rows), message="dry run: nothing done")

            path = _write_archive(rows, Path(settings.AUDIT_ARCHIVE_DIR), cutoff)
            digest = verify_archive(path, len(rows))
            if digest is None:
                return ArchiveResult(
                    EXIT_VERIFY, path=path, message="archive verification failed; nothing deleted"
                )

            seqs = [r["seq"] for r in rows]
            deleted = 0
            async with conn.begin():
                for start in range(0, len(seqs), DELETE_CHUNK):
                    chunk = seqs[start : start + DELETE_CHUNK]
                    done = await conn.execute(delete(TABLE).where(TABLE.c.seq.in_(chunk)))
                    deleted += int(done.rowcount or 0)
                if deleted != len(seqs):
                    raise RuntimeError(f"expected to delete {len(seqs)} rows, deleted {deleted}")
                await conn.execute(
                    insert(TABLE).values(
                        actor_id=SYSTEM_ACTOR_ID,
                        actor_name=SYSTEM_ACTOR_NAME,
                        actor_role=SYSTEM_ACTOR_ROLE,
                        action=AuditAction.DELETE.value,
                        entity=AuditEntity.AUDIT.value,
                        entity_id="retention",
                        entity_label="Retención de bitácora",
                        summary=(
                            f"Archivó y purgó {deleted} evento(s) anteriores a "
                            f"{cutoff:%Y-%m-%d} (archivo {path.name}, sha256 {digest[:16]})"
                        ),
                        changes=[
                            {"field": "archive", "label": "Archivo", "from": None, "to": path.name},
                            {"field": "sha256", "label": "sha256", "from": None, "to": digest},
                            {"field": "rows", "label": "Eventos", "from": None, "to": deleted},
                        ],
                    )
                )
            return ArchiveResult(
                EXIT_OK, archived=len(rows), deleted=deleted, path=path, sha256=digest
            )
    finally:
        await engine.dispose()


async def count_events() -> int:
    """Rows in the trail (maintenance connection); handy for operators and tests."""
    engine = create_async_engine(get_settings().maintenance_database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            return int((await conn.execute(select(func.count()).select_from(TABLE))).scalar_one())
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="count, write and delete nothing")
    parser.add_argument("--older-than-days", type=int, default=None)
    args = parser.parse_args()
    outcome = asyncio.run(run(dry_run=args.dry_run, older_than_days=args.older_than_days))
    print(
        f"audit_archive code={outcome.code} archived={outcome.archived} deleted={outcome.deleted} "
        f"file={outcome.path} sha256={outcome.sha256} {outcome.message}".strip()
    )
    sys.exit(outcome.code)
