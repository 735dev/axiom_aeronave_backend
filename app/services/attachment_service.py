"""Attachments of tickets: validated upload, archive in Blackbox, reading, link and removal.

The ticket keeps only the reference ({provider, document_id, folder, filename}); the bytes
live in Blackbox and are fetched through a signed link that is never stored. Everything the
client says about a file (name, MIME type) is a claim: the server decides from the content.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi_basekit.exceptions.api_exceptions import NotFoundException

from app.config.attachments import (
    LINK_TTL_SECONDS,
    MAX_FILES_PER_TICKET,
    PROVIDER_BLACKBOX,
    READABLE_KINDS,
    AttachmentCode,
)
from app.config.settings import get_settings
from app.config.tickets import (
    IDEMPOTENCY_KEY_PATTERN,
    ExtractionSource,
    ExtractionStatus,
    FieldType,
)
from app.exceptions.attachments import AttachmentError
from app.exceptions.tickets import TicketError
from app.integrations.blackbox import BlackboxError, build_client, build_vault, naming
from app.integrations.receipt_reader import ReceiptReadingService
from app.models.attachment import TicketAttachment
from app.models.ticket import Ticket, TicketType
from app.repositories.attachment import AttachmentRepository
from app.repositories.ticket_extraction import TicketExtractionRepository
from app.services.base import ProjectService
from app.services.ticket_hooks import TicketEvent
from app.services.ticket_service import TicketService
from app.utils.uploads import UploadInfo, inspect_upload

logger = logging.getLogger(__name__)

STORAGE_DOWN = "El archivo documental no está disponible ahora. Inténtalo de nuevo en unos minutos."
STORAGE_REFUSED = "No se pudo archivar el archivo. Inténtalo de nuevo o avisa a soporte."


@dataclass
class UploadResult:
    ticket: Ticket
    #: True when the Idempotency-Key had already stored this very file.
    replayed: bool = False


class AttachmentService(ProjectService[TicketAttachment]):
    repository: AttachmentRepository
    tickets: TicketService
    extractions: TicketExtractionRepository
    reader: ReceiptReadingService | None

    filter_whitelist = frozenset({"ticket_id"})

    # ------------------------------------------------------------------ helpers

    @asynccontextmanager
    async def _vault(self):
        settings = get_settings()
        client = build_client(settings)
        if client is None:
            raise AttachmentError(AttachmentCode.STORAGE, STORAGE_DOWN)
        try:
            yield build_vault(client, settings)
        finally:
            await client.aclose()

    @staticmethod
    def _operation_code(ticket: Ticket) -> str | None:
        return ticket.code if ticket.type.is_root else ticket.parent_code

    @staticmethod
    def _fallback_year(ticket: Ticket) -> int:
        return ticket.created_at.astimezone(ZoneInfo(get_settings().BUSINESS_TIMEZONE)).year

    @staticmethod
    def _file_field(ticket_type: TicketType, field_key: str | None):
        """The file field that justifies the upload: the one asked for, else the first."""
        fields = [f for f in ticket_type.fields if f.type == FieldType.FILE.value]
        if not fields:
            raise AttachmentError(
                AttachmentCode.FIELD, f"{ticket_type.name} no admite archivos adjuntos."
            )
        if field_key is None:
            return fields[0]
        chosen = next((f for f in fields if f.key == field_key), None)
        if chosen is None:
            raise AttachmentError(
                AttachmentCode.FIELD, "Ese campo no admite archivos.", field="field_key"
            )
        return chosen

    @staticmethod
    def _checked_key(key: str | None) -> str | None:
        if key is None:
            return None
        if not IDEMPOTENCY_KEY_PATTERN.match(key):
            raise AttachmentError(
                AttachmentCode.IDEMPOTENCY, "La clave de idempotencia no es válida."
            )
        return key

    # ------------------------------------------------------------------ reads

    async def list_for_ticket(self, id) -> list[TicketAttachment]:  # noqa: A002
        ticket = await self.tickets.visible_ticket(id)
        return await self.repository.live_of_ticket(ticket.id)

    async def _attachment(self, ticket_id, attachment_id) -> tuple[Ticket, TicketAttachment]:
        ticket = await self.tickets.visible_ticket(ticket_id)
        attachment = await self.repository.get_of_ticket(ticket.id, attachment_id)
        if attachment is None:
            raise NotFoundException("archivo no encontrado")
        return ticket, attachment

    async def download_link(self, id, attachment_id) -> dict[str, Any]:  # noqa: A002
        """A signed link to the original, valid ~5 minutes and never stored."""
        ticket, attachment = await self._attachment(id, attachment_id)
        try:
            async with self._vault() as vault:
                documents = await vault.folder_links(
                    self._operation_code(ticket), self._fallback_year(ticket)
                )
        except BlackboxError as exc:
            logger.warning("blackbox_link_failed codigo=%s", exc.codigo)
            raise AttachmentError(AttachmentCode.STORAGE, STORAGE_DOWN) from None
        except ValueError:
            logger.error("blackbox_folder_invalid ticket=%s", ticket.code)
            raise AttachmentError(AttachmentCode.STORAGE, STORAGE_DOWN) from None
        found = next((d for d in documents if d.id == attachment.document_id), None)
        if found is None:
            raise NotFoundException("archivo no encontrado")
        return {
            "url": found.url,
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=LINK_TTL_SECONDS),
        }

    # ------------------------------------------------------------------ upload

    async def upload(
        self,
        id,  # noqa: A002
        raw_name: str | None,
        content: bytes,
        field_key: str | None,
        idempotency_key: str | None,
    ) -> UploadResult:
        """Three steps, so the call to Blackbox never runs with the ticket row locked:

        1. under the lock: validate, replay a known key, reserve a slot (ordinal of the
           archived name) and COMMIT, which releases the lock;
        2. no lock, no open transaction work: archive in Blackbox with a deterministic key;
        3. lock again, re-validate (the ticket may have closed meanwhile), link the document
           and drop the slot in one transaction.

        A failure in step 2 drops the slot (nothing half-written). A crash between 2 and 3
        leaves the slot: the same Idempotency-Key finds it, archives again (Blackbox answers
        `already_stored`) and links. See docs/ADJUNTOS.md.
        """
        await self.throttle("upload")
        key = self._checked_key(idempotency_key)
        ticket = await self.tickets.open_for_change(id, "adjuntar un archivo a")
        info = inspect_upload(raw_name, content)
        field = self._file_field(ticket.type, field_key)
        actor = self.principal
        digest = hashlib.sha256(content).hexdigest()

        replay = await self._known_upload(ticket, actor.user_id, key, digest, field.key)
        if replay is not None:
            return replay
        if len(ticket.attachments) + await self.repository.slots_of_ticket(ticket.id) >= (
            MAX_FILES_PER_TICKET
        ):
            raise AttachmentError(
                AttachmentCode.LIMIT,
                f"Un registro admite como máximo {MAX_FILES_PER_TICKET} archivos.",
            )

        document_type = naming.document_type_for(ticket.type.code, field.key)
        slot = await self._slot(ticket, actor.user_id, key, digest, field.key, document_type)
        ticket_id, ticket_code = ticket.id, ticket.code
        await self.repository.checkpoint()

        try:
            stored = await self._archive(
                ticket, field.key, info, content, slot.sequence, slot.client_key
            )
        except AttachmentError:
            await self._compensate(slot.id)
            raise

        try:
            ticket = await self.tickets.open_for_change(
                ticket_id, "adjuntar un archivo a", fresh=True
            )
        except Exception:
            # Closed (or hidden) while the file was travelling: the archived copy has no
            # reference, which is the lesser evil; the slot must not linger.
            await self._compensate(slot.id)
            raise
        late = await self._known_upload(ticket, actor.user_id, key, digest, field.key)
        if late is not None:
            # A twin request with the same key linked it while this one was archiving.
            return late
        attachment = await self.repository.add(
            ticket,
            {
                "name": info.name,
                "mime": info.mime,
                "size": info.size,
                "document_type": document_type,
                "field_key": field.key,
                "sequence": slot.sequence,
                "provider": PROVIDER_BLACKBOX,
                "document_id": stored.id,
                "folder": stored.external_client_id,
                "filename": stored.filename,
                "sha256": digest,
                "idempotency_key": key,
                "uploaded_by_id": actor.user_id,
                "uploaded_by": actor.full_name,
            },
        )
        await self.repository.drop_slot(slot.id)
        logger.info("attachment_linked ticket=%s sequence=%s", ticket_code, slot.sequence)
        reading = await self._read_receipt(ticket, attachment, info, content)
        await self.repository.record_attached(ticket, info.name, self._reading_label(reading))
        await self.tickets.reevaluate_rules(ticket)
        await self.tickets.fire(
            TicketEvent.ATTACHMENT_ADDED, ticket, reading=reading, file_name=info.name
        )
        return UploadResult(ticket)

    async def _compensate(self, slot_id: UUID) -> None:
        try:
            await self.repository.release_slot(slot_id)
        except Exception:  # the original failure is what the caller must see
            logger.exception("attachment_slot_release_failed slot=%s", slot_id)

    async def _known_upload(
        self, ticket: Ticket, uploader_id: UUID, key: str | None, digest: str, field_key: str
    ) -> UploadResult | None:
        """The upload this key already completed (a replay), or None. The same key with other
        content is a client bug."""
        if key is None:
            return None
        existing = await self.repository.find_by_key(ticket.id, uploader_id, key)
        if existing is None:
            return None
        if existing.sha256 != digest or existing.field_key != field_key:
            raise AttachmentError(
                AttachmentCode.IDEMPOTENCY,
                "Esa clave de idempotencia ya se usó con otro contenido.",
            )
        return UploadResult(ticket, replayed=True)

    async def _slot(
        self,
        ticket: Ticket,
        uploader_id: UUID,
        key: str | None,
        digest: str,
        field_key: str,
        document_type: str,
    ):
        """The slot of this upload: resumed when a previous attempt with the same key crashed
        after reserving it, otherwise a new one."""
        if key is not None:
            previous = await self.repository.find_slot(ticket.id, uploader_id, key)
            if previous is not None:
                if previous.sha256 != digest or previous.field_key != field_key:
                    raise AttachmentError(
                        AttachmentCode.IDEMPOTENCY,
                        "Esa clave de idempotencia ya se usó con otro contenido.",
                    )
                return previous
        # Blackbox deduplicates on this: deterministic with a key, so a retry after a lost
        # answer finds the same document instead of storing a second one.
        seed = f"{uploader_id}|{ticket.id}|{key}" if key else uuid.uuid4().hex
        return await self.repository.reserve_slot(
            {
                "ticket_id": ticket.id,
                "uploader_id": uploader_id,
                "idempotency_key": key,
                "client_key": hashlib.sha256(seed.encode()).hexdigest()[:40],
                "document_type": document_type,
                "field_key": field_key,
                "sequence": await self.repository.next_sequence(ticket.id, document_type),
                "sha256": digest,
            }
        )

    async def _archive(
        self,
        ticket: Ticket,
        field_key: str,
        info: UploadInfo,
        content: bytes,
        sequence: int,
        client_key: str,
    ):
        """Send the file to Blackbox. No database lock is held here."""
        try:
            async with self._vault() as vault:
                return await vault.archive_ordered(
                    operation_code=self._operation_code(ticket),
                    fallback_year=self._fallback_year(ticket),
                    ticket_code=ticket.code,
                    ticket_type_code=ticket.type.code,
                    field_key=field_key,
                    original_name=info.name,
                    sequence=sequence,
                    content=content,
                    client_key=client_key,
                    index=0,
                )
        except BlackboxError as exc:
            # The token and the server's wording never reach the client; only the log.
            logger.warning(
                "blackbox_upload_failed ticket=%s status=%s codigo=%s transient=%s",
                ticket.code,
                exc.status,
                exc.codigo,
                exc.transient,
            )
            if exc.transient:
                raise AttachmentError(AttachmentCode.STORAGE, STORAGE_DOWN) from None
            raise AttachmentError(AttachmentCode.STORAGE, STORAGE_REFUSED) from None
        except ValueError:
            logger.error("blackbox_naming_invalid ticket=%s", ticket.code)
            raise AttachmentError(AttachmentCode.STORAGE, STORAGE_REFUSED) from None

    # ------------------------------------------------------------------ reading

    @staticmethod
    def _reading_label(reading) -> str | None:
        if reading is None:
            return None
        return (
            "ilegible"
            if reading.status == ExtractionStatus.UNREADABLE.value
            else "datos propuestos"
        )

    def _reads(self, info: UploadInfo, ticket_type: TicketType) -> bool:
        return (
            self.reader is not None
            and info.kind in READABLE_KINDS
            and ticket_type.field_by_role("amount") is not None
        )

    async def _read_receipt(
        self, ticket: Ticket, attachment: TicketAttachment, info: UploadInfo, content: bytes
    ):
        """Propose the receipt's data. Never blocks or undoes the upload: a provider failure is
        an 'unreadable' reading and the person types it in."""
        if not self._reads(info, ticket.type):
            return None
        hints = await self.tickets.reading_hints(ticket.type)
        if hints is None:
            return None
        outcome = await self.reader.read(content, info.mime, hints)
        fields = await self.tickets.checked_reading(
            ticket.type, [(f.key, f.value, f.confidence) for f in outcome.fields]
        )
        status = ExtractionStatus.EXTRACTED if fields else ExtractionStatus.UNREADABLE
        try:
            reading = await self.tickets.register_reading(
                ticket,
                fields,
                ExtractionSource.READER,
                self.principal.user_id,
                attachment.id,
                status=status,
            )
        except TicketError:
            logger.warning("reading_rejected ticket=%s", ticket.code)
            return None
        await self.repository.reload_extraction(attachment)
        return reading

    async def extract(self, type_code: str, raw_name: str | None, content: bytes) -> dict[str, Any]:
        """Read a receipt BEFORE the record exists. Saves nothing."""
        await self.throttle("extract")
        ticket_type = await self.tickets.types.get_by_code(type_code)
        # A type the caller cannot use answers exactly like one that does not exist: a 403 would
        # confirm which types exist (the type is the scope of the resource).
        if (
            ticket_type is None
            or not ticket_type.is_active
            or not self.tickets.may_edit_type(ticket_type)
        ):
            raise AttachmentError("not_found", "El tipo de registro no existe.")
        info = inspect_upload(raw_name, content)
        hints = await self.tickets.reading_hints(ticket_type)
        if hints is None:
            raise AttachmentError("validation", "Este tipo de registro no lee comprobantes.")
        if info.kind not in READABLE_KINDS:
            raise AttachmentError("validation", "Solo se leen imágenes PNG o JPG.")
        if self.reader is None:
            return {"status": "unreadable", "fields": [], "corrections": [], "review_required": []}
        outcome = await self.reader.read(content, info.mime, hints)
        fields = await self.tickets.checked_reading(
            ticket_type, [(f.key, f.value, f.confidence) for f in outcome.fields]
        )
        labels = ticket_type.field_labels()
        return {
            "status": "extracted" if fields else "unreadable",
            "fields": [{**f, "label": labels.get(f["key"], f["key"])} for f in fields],
            "corrections": [],
            "review_required": [f["key"] for f in fields if f["confidence"] != "high"],
        }

    async def validate_reading(self, id, attachment_id, data) -> Ticket:  # noqa: A002
        """Validate the reading of one file; the review gate is the ticket service's."""
        ticket, attachment = await self._attachment(id, attachment_id)
        reading = await self.extractions.of_attachment(attachment.id)
        if reading is None:
            raise NotFoundException("lectura no encontrada")
        return await self.tickets.validate_reading(ticket.id, reading.id, data)

    # ------------------------------------------------------------------ removal

    async def remove(self, id, attachment_id) -> Ticket:  # noqa: A002
        """Detach a file from an open ticket. The archived original stays in Blackbox (the
        archive is not ours to rewrite); the reference and its pending reading go."""
        ticket = await self.tickets.open_for_change(id, "quitar un archivo de")
        attachment = await self.repository.get_of_ticket(ticket.id, attachment_id)
        if attachment is None:
            raise NotFoundException("archivo no encontrado")
        await self.extractions.discard_unvalidated_of_attachment(attachment.id)
        await self.repository.mark_removed(ticket, attachment, self.principal.full_name)
        await self.repository.record_removed(ticket, attachment)
        await self.tickets.reevaluate_rules(ticket)
        return ticket
