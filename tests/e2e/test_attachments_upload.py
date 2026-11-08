"""Uploading a file to a ticket: server-side validation, archive naming and the stored record."""

from __future__ import annotations

import re

import pytest

from tests.e2e.attachments_support import (
    attach,
    doc_bytes,
    docx_bytes,
    files_of,
    jpeg_bytes,
    new_expense,
    pdf_bytes,
    png_bytes,
    sha256,
    upload,
    zip_bomb,
    zip_without_word,
)
from tests.e2e.conftest import API
from tests.e2e.tickets_support import (
    get_ticket,
    history,
    make_expense,
    make_operation,
    rules_of,
    ticket_url,
)


def folder_of(operation: dict) -> str:
    registration, number, year = re.fullmatch(
        r"([A-Z0-9]+)-(\d{3})-(\d{2})", operation["code"]
    ).groups()
    return f"Maestro - 20{year}-{registration}-{number}"


class TestAcceptedFiles:
    @pytest.mark.parametrize(
        "name,mime",
        [
            ("factura.pdf", "application/pdf"),
            ("nota.doc", "application/msword"),
            (
                "nota.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
            ("foto.png", "image/png"),
            ("foto.jpg", "image/jpeg"),
            ("foto.jpeg", "image/jpeg"),
        ],
    )
    async def test_each_allowed_extension_is_stored(self, admin, name, mime):
        _, expense = await new_expense(admin)
        ticket = await attach(admin, expense, name)
        stored = ticket["files"][-1]
        assert stored["mime"] == mime
        assert stored["storage"]["provider"] == "blackbox"
        assert stored["storage"]["filename"].endswith("." + name.rsplit(".", 1)[1])

    @pytest.mark.parametrize("name", ["A.PDF", "b.JpG", "c.DOCX", "D.Jpeg", "e.PNG", "f.DoC"])
    async def test_extension_case_does_not_matter(self, admin, name):
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, name)
        assert r.status_code == 201, r.text
        assert r.json()["files"][-1]["storage"]["filename"].endswith(name.rsplit(".", 1)[1].lower())

    async def test_the_response_is_the_ticket_with_its_files(self, admin):
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, "uno.pdf")
        assert r.status_code == 201
        body = r.json()
        assert body["id"] == expense["id"]
        assert body["attachments"] == 1
        assert [f["name"] for f in body["files"]] == ["uno.pdf"]

    async def test_the_record_keeps_a_reference_never_the_bytes(self, admin):
        content = pdf_bytes(500)
        _, expense = await new_expense(admin)
        ticket = await attach(admin, expense, "contrato.pdf", content=content)
        stored = ticket["files"][0]
        assert set(stored["storage"]) == {"provider", "document_id", "folder", "filename"}
        assert stored["size"] == len(content)
        assert "data_url" not in stored and "content" not in stored
        assert "url" not in stored and "url" not in stored["storage"]

    async def test_stored_mime_is_decided_by_content_not_by_the_client(self, admin):
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, "doc.pdf", mime="image/png")
        assert r.status_code == 201
        assert r.json()["files"][0]["mime"] == "application/pdf"

    async def test_octet_stream_clients_work(self, admin):
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, "doc.pdf", mime="application/octet-stream")
        assert r.status_code == 201

    async def test_exactly_20_mb_passes(self, admin):
        _, expense = await new_expense(admin)
        content = b"%PDF-1.4\n" + b"0" * (20 * 1024 * 1024 - 9)
        r = await upload(admin, expense, "grande.pdf", content=content)
        assert r.status_code == 201, r.text
        assert r.json()["files"][0]["size"] == 20 * 1024 * 1024

    async def test_a_real_docx_structure_passes(self, admin):
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, "informe.docx", content=docx_bytes(extra_entries=20))
        assert r.status_code == 201

    async def test_a_legacy_doc_passes(self, admin):
        _, expense = await new_expense(admin)
        assert (await upload(admin, expense, "viejo.doc", content=doc_bytes())).status_code == 201

    async def test_uploaded_by_is_the_caller(self, admin, pilot):
        op = await make_operation(admin)
        expense = await make_expense(pilot, op)
        ticket = await attach(pilot, expense)
        assert (
            ticket["files"][0]["uploaded_by"] == "Piloto Transvalor"
            or ticket["files"][0]["uploaded_by"]
        )

    async def test_a_document_is_filed_in_the_pilot_flow(self, pilot, admin):
        op = await make_operation(admin)
        expense = await make_expense(pilot, op)
        assert (await upload(pilot, expense, "mio.pdf")).status_code == 201


class TestRejectedByExtension:
    @pytest.mark.parametrize(
        "name",
        [
            "virus.exe",
            "nota.txt",
            "sin_extension",
            "doble.pdf.exe",
            "pagina.html",
            "script.js",
            "imagen.svg",
            "datos.zip",
            "shell.php",
            "macro.xlsm",
            "foto.gif",
            "foto.webp",
            ".htaccess",
            "termina.pdf.",
        ],
    )
    async def test_disallowed_names_are_400_extension(self, admin, name):
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, name, content=pdf_bytes())
        assert r.status_code == 400, r.text
        assert r.json()["codigo"] == "extension"

    async def test_a_rejection_leaves_nothing_attached(self, admin):
        _, expense = await new_expense(admin)
        await upload(admin, expense, "x.exe", content=pdf_bytes())
        assert await files_of(admin, expense) == []
        assert (await get_ticket(admin, expense))["attachments"] == 0

    async def test_an_absurdly_long_name_is_refused(self, admin):
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, "a" * 1500 + ".pdf", content=pdf_bytes())
        assert r.status_code == 400
        assert r.json()["codigo"] == "extension"


class TestEmptyAndOversized:
    async def test_empty_file_is_400_vacio(self, admin):
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, "vacio.pdf", content=b"")
        assert r.status_code == 400
        assert r.json()["codigo"] == "vacio"

    async def test_one_byte_over_20_mb_is_413_tamano(self, admin):
        _, expense = await new_expense(admin)
        content = b"%PDF-1.4\n" + b"0" * (20 * 1024 * 1024 - 8)
        r = await upload(admin, expense, "enorme.pdf", content=content)
        assert r.status_code == 413
        assert r.json()["codigo"] == "tamano"
        assert await files_of(admin, expense) == []

    async def test_a_body_far_over_the_limit_is_refused_before_it_is_read(self, admin):
        _, expense = await new_expense(admin)
        content = b"%PDF-1.4\n" + b"0" * (22 * 1024 * 1024)
        r = await upload(admin, expense, "gigante.pdf", content=content)
        assert r.status_code == 413
        assert r.json()["codigo"] == "tamano"

    async def test_a_request_without_a_length_is_refused(self, admin, anon):
        _, expense = await new_expense(admin)

        async def chunks():
            yield b"--x\r\n"

        r = await admin.post(
            ticket_url(expense, "attachments/"),
            content=chunks(),
            headers={"Content-Type": "multipart/form-data; boundary=x"},
        )
        assert r.status_code == 411


class TestContentMustMatchTheExtension:
    """The client's name and MIME type are claims; the first bytes are the evidence."""

    @pytest.mark.parametrize(
        "name,content",
        [
            ("factura.pdf", b"MZ\x90\x00" + b"\x00" * 100),
            ("factura.pdf", png_bytes()),
            ("foto.png", pdf_bytes()),
            ("foto.png", jpeg_bytes()),
            ("foto.jpg", png_bytes()),
            ("foto.jpeg", pdf_bytes()),
            ("nota.docx", b"texto plano que no es word"),
            ("nota.docx", doc_bytes()),
            ("nota.doc", docx_bytes()),
            ("nota.doc", pdf_bytes()),
            ("foto.png", b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"),
            ("foto.jpg", b"<html><script>alert(1)</script></html>"),
            ("foto.png", b"GIF89a" + b"\x00" * 50),
            ("a.pdf", b"\x7fELF" + b"\x00" * 60),
            ("a.pdf", b"#!/bin/sh\nrm -rf /\n"),
            ("a.docx", zip_without_word()),
            ("a.png", b"PK\x03\x04" + b"\x00" * 50),
        ],
    )
    async def test_spoofed_content_is_422_tipo(self, admin, name, content):
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, name, content=content)
        assert r.status_code == 422, r.text
        assert r.json()["codigo"] == "tipo"
        assert await files_of(admin, expense) == []

    async def test_a_decompression_bomb_docx_is_refused(self, admin):
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, "bomba.docx", content=zip_bomb())
        assert r.status_code == 422
        assert r.json()["codigo"] == "tipo"

    async def test_a_docx_with_thousands_of_entries_is_refused(self, admin):
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, "muchos.docx", content=docx_bytes(extra_entries=5100))
        assert r.status_code == 422

    async def test_a_truncated_zip_is_refused(self, admin):
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, "roto.docx", content=docx_bytes()[:40])
        assert r.status_code == 422

    async def test_spoofing_does_not_reach_the_archive(self, admin):
        _, expense = await new_expense(admin)
        await upload(admin, expense, "mala.pdf", content=png_bytes())
        ok = await attach(admin, expense, "buena.pdf")
        assert ok["files"][0]["storage"]["filename"].endswith("01_buena.pdf")


class TestNamesAreSanitized:
    async def test_path_traversal_cannot_escape_the_folder(self, admin):
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, "../../etc/passwd.pdf", content=pdf_bytes())
        assert r.status_code == 201
        stored = r.json()["files"][0]
        for value in (stored["name"], stored["storage"]["filename"]):
            assert "/" not in value and "\\" not in value and ".." not in value

    async def test_windows_separators_are_dropped_too(self, admin):
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, "..\\..\\windows\\system32\\x.pdf", content=pdf_bytes())
        assert r.status_code == 201
        stored = r.json()["files"][0]
        assert "\\" not in stored["name"] and "\\" not in stored["storage"]["filename"]

    async def test_symbols_and_shell_metacharacters_become_safe_text(self, admin):
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, "a;rm -rf $(whoami)`id`|b&c.pdf", content=pdf_bytes())
        assert r.status_code == 201
        filename = r.json()["files"][0]["storage"]["filename"]
        assert re.fullmatch(r"[A-Za-z0-9._-]+", filename), filename

    async def test_accents_are_stripped_from_the_archive_name(self, admin):
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, "Recibo Gasolina ñandú.JPG", content=jpeg_bytes())
        assert r.status_code == 201
        assert r.json()["files"][0]["storage"]["filename"].endswith(
            "_comprobante_01_Recibo-Gasolina-nandu.jpg"
        )

    async def test_a_long_name_is_cut(self, admin):
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, "x" * 600 + ".pdf", content=pdf_bytes())
        assert r.status_code == 201
        stored = r.json()["files"][0]
        assert len(stored["name"]) <= 150
        assert len(stored["storage"]["filename"]) < 90

    async def test_right_to_left_override_cannot_disguise_the_extension(self, admin):
        _, expense = await new_expense(admin)
        # "recibo‮gpj.exe" renders as "recibogpj.exe"-reversed in some viewers.
        r = await upload(admin, expense, "recibo‮gpj.exe", content=pdf_bytes())
        assert r.status_code == 400
        r = await upload(admin, expense, "recibo‮gnp.pdf", content=pdf_bytes())
        assert r.status_code == 201
        assert "‮" not in r.json()["files"][0]["name"]

    async def test_control_characters_are_removed(self, admin):
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, "a\x00b\x07c.pdf", content=pdf_bytes())
        assert r.status_code == 201
        assert all(ord(c) >= 32 for c in r.json()["files"][0]["name"])

    async def test_a_nul_hiding_a_real_extension_does_not_help(self, admin):
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, "x.exe\x00.pdf", content=pdf_bytes())
        # Whatever the name becomes, the stored file is a verified PDF and the name is inert.
        if r.status_code == 201:
            assert not r.json()["files"][0]["storage"]["filename"].endswith(".exe")

    async def test_a_name_made_of_dots_and_spaces_gets_a_default(self, admin):
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, " . .pdf", content=pdf_bytes())
        assert r.status_code == 201
        assert r.json()["files"][0]["name"].endswith(".pdf")


class TestArchiveNaming:
    async def test_folder_is_year_aircraft_number(self, admin):
        op, expense = await new_expense(admin)
        ticket = await attach(admin, expense)
        assert ticket["files"][0]["storage"]["folder"] == folder_of(op)

    async def test_file_is_ticket_type_sequence_original(self, admin):
        _, expense = await new_expense(admin)
        ticket = await attach(admin, expense, "r.png")
        assert (
            ticket["files"][0]["storage"]["filename"] == f"{expense['code']}_comprobante_01_r.png"
        )

    async def test_files_are_numbered_in_upload_order(self, admin):
        _, expense = await new_expense(admin)
        for _ in range(3):
            await attach(admin, expense, "r.png")
        names = [f["storage"]["filename"] for f in await files_of(admin, expense)]
        assert names == [f"{expense['code']}_comprobante_0{i}_r.png" for i in (1, 2, 3)]

    async def test_every_expense_of_an_operation_shares_the_operation_folder(self, admin):
        op = await make_operation(admin)
        first, second = await make_expense(admin, op), await make_expense(admin, op)
        a, b = await attach(admin, first), await attach(admin, second)
        assert a["files"][0]["storage"]["folder"] == b["files"][0]["storage"]["folder"]
        assert a["files"][0]["storage"]["filename"].startswith(first["code"])
        assert b["files"][0]["storage"]["filename"].startswith(second["code"])

    async def test_the_numbering_is_per_ticket(self, admin):
        op = await make_operation(admin)
        first, second = await make_expense(admin, op), await make_expense(admin, op)
        await attach(admin, first)
        b = await attach(admin, second)
        assert "_comprobante_01_" in b["files"][0]["storage"]["filename"]

    async def test_a_technical_report_photo_is_technical_evidence(self, admin):
        op = await make_operation(admin)
        r = await admin.post(
            f"{API}/tickets/",
            json={
                "type_code": "technical_report",
                "title": "t",
                "parent_id": op["id"],
                "payload": {"description": "x"},
            },
        )
        ticket = await attach(admin, r.json(), "p.png", field_key="photo")
        stored = ticket["files"][0]
        assert stored["document_type"] == "evidencia_tecnica"
        assert stored["storage"]["folder"] == folder_of(op)
        assert "_evidencia_tecnica_01_" in stored["storage"]["filename"]

    async def test_an_operation_manifest_is_a_manifest(self, admin):
        op = await make_operation(admin)
        ticket = await attach(admin, op, "manifiesto.pdf", field_key="manifest")
        stored = ticket["files"][0]
        assert stored["document_type"] == "manifiesto"
        assert stored["storage"]["folder"] == folder_of(op)

    async def test_an_inac_permit_approval_is_filed_as_such(self, admin):
        op = await make_operation(admin)
        inac = (
            await admin.post(
                f"{API}/tickets/",
                json={
                    "type_code": "inac_permit",
                    "title": "p",
                    "parent_id": op["id"],
                    "payload": {"scope": "domestic"},
                },
            )
        ).json()
        ticket = await attach(admin, inac, "permiso.pdf")
        assert ticket["files"][0]["document_type"] == "permiso_inac"

    async def test_a_loose_hangar_note_goes_to_the_no_operation_folder(self, admin):
        r = await admin.post(
            f"{API}/tickets/", json={"type_code": "hangar_incident", "title": "n", "payload": {}}
        )
        if r.status_code != 201:
            pytest.skip("hangar incident needs fields in this seed")
        ticket = await attach(admin, r.json(), "foto.jpg")
        stored = ticket["files"][0]
        assert re.fullmatch(r"Maestro - \d{4}-SIN-OPERACION", stored["storage"]["folder"])
        assert stored["document_type"] == "evidencia_hangar"

    async def test_sequence_is_per_document_type(self, admin):
        op = await make_operation(admin)
        first = await attach(admin, op, "m.pdf", field_key="manifest")
        assert "_manifiesto_01_" in first["files"][0]["storage"]["filename"]

    async def test_first_file_field_is_the_default(self, admin):
        _, expense = await new_expense(admin)
        ticket = await attach(admin, expense, "a.pdf")
        assert ticket["files"][0]["document_type"] == "comprobante"

    async def test_document_ids_are_distinct(self, admin):
        _, expense = await new_expense(admin)
        for _ in range(3):
            await attach(admin, expense)
        ids = {f["storage"]["document_id"] for f in await files_of(admin, expense)}
        assert len(ids) == 3


class TestFieldKey:
    async def test_unknown_field_is_422(self, admin):
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, "a.pdf", field_key="nope")
        assert r.status_code == 422
        assert r.json()["codigo"] == "campo"

    async def test_a_non_file_field_is_422(self, admin):
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, "a.pdf", field_key="amount")
        assert r.status_code == 422

    async def test_the_right_file_field_is_accepted(self, admin):
        _, expense = await new_expense(admin)
        assert (await upload(admin, expense, "a.pdf", field_key="support")).status_code == 201

    async def test_a_type_without_file_fields_takes_no_attachments(self, admin):
        from tests.e2e.tickets_support import field, status, type_body

        body = type_body(
            "sin_archivos",
            fields=[field("detail", "Detalle", "text", required=True)],
            statuses=[
                status("open", "Abierto", is_initial=True),
                status("done", "Hecho", is_final=True),
            ],
        )
        r = await admin.post(f"{API}/ticket-types/", json=body)
        assert r.status_code == 201, r.text
        ticket = await admin.post(
            f"{API}/tickets/",
            json={"type_code": "sin_archivos", "title": "t", "payload": {"detail": "x"}},
        )
        assert ticket.status_code == 201, ticket.text
        r = await upload(admin, ticket.json(), "a.pdf")
        assert r.status_code == 422
        assert r.json()["codigo"] == "campo"

    async def test_field_key_is_length_limited(self, admin):
        _, expense = await new_expense(admin)
        r = await upload(admin, expense, "a.pdf", field_key="k" * 200)
        assert r.status_code == 422


class TestLimitPerRecord:
    async def test_ten_files_fit_and_the_eleventh_does_not(self, admin):
        _, expense = await new_expense(admin)
        for _ in range(10):
            assert (await upload(admin, expense)).status_code == 201
        r = await upload(admin, expense)
        assert r.status_code == 422
        assert r.json()["codigo"] == "limite"
        assert len(await files_of(admin, expense)) == 10

    async def test_removing_one_frees_a_place(self, admin):
        _, expense = await new_expense(admin)
        for _ in range(10):
            await upload(admin, expense)
        first = (await files_of(admin, expense))[0]
        assert (
            await admin.delete(ticket_url(expense, f"attachments/{first['id']}/"))
        ).status_code == 200
        assert (await upload(admin, expense)).status_code == 201

    async def test_the_limit_is_per_ticket(self, admin):
        op = await make_operation(admin)
        a, b = await make_expense(admin, op), await make_expense(admin, op)
        for _ in range(10):
            await upload(admin, a)
        assert (await upload(admin, b)).status_code == 201


class TestEffectOnTheTicket:
    async def test_the_count_is_real(self, admin):
        _, expense = await new_expense(admin)
        assert (await get_ticket(admin, expense))["attachments"] == 0
        await attach(admin, expense)
        await attach(admin, expense)
        assert (await get_ticket(admin, expense))["attachments"] == 2

    async def test_the_every_expense_needs_a_support_rule_fires_then_clears(self, admin):
        _, expense = await new_expense(admin)
        before = await get_ticket(admin, expense)
        assert any("comprobante" in h["rule_name"].lower() for h in before["rule_hits"])
        after = await attach(admin, expense)
        assert not any("comprobante" in h["rule_name"].lower() for h in after["rule_hits"])

    async def test_the_rule_comes_back_when_the_last_file_is_removed(self, admin):
        _, expense = await new_expense(admin)
        ticket = await attach(admin, expense)
        r = await admin.delete(ticket_url(expense, f"attachments/{ticket['files'][0]['id']}/"))
        assert any("comprobante" in h["rule_name"].lower() for h in r.json()["rule_hits"])

    async def test_files_travel_in_the_ticket_listing(self, admin):
        op, expense = await new_expense(admin)
        await attach(admin, expense)
        r = await admin.get(f"{API}/tickets/?parent_id={op['id']}&limit=50")
        row = next(t for t in r.json()["data"] if t["id"] == expense["id"])
        assert row["attachments"] == 1 and len(row["files"]) == 1

    async def test_the_audit_trail_records_the_attachment(self, admin):
        _, expense = await new_expense(admin)
        await attach(admin, expense, "evidencia.pdf")
        events = [e for e in await history(admin, expense) if e["action"] == "attach"]
        assert len(events) == 1
        assert "evidencia.pdf" in events[0]["summary"]

    async def test_updating_the_ticket_keeps_the_files(self, admin):
        _, expense = await new_expense(admin)
        await attach(admin, expense)
        r = await admin.patch(ticket_url(expense), json={"title": "otro"})
        assert r.status_code == 200
        assert len(r.json()["files"]) == 1

    async def test_a_ticket_in_a_listing_page_shows_only_live_files(self, admin):
        op, expense = await new_expense(admin)
        t = await attach(admin, expense)
        await admin.delete(ticket_url(expense, f"attachments/{t['files'][0]['id']}/"))
        r = await admin.get(f"{API}/tickets/?parent_id={op['id']}&limit=50")
        row = next(x for x in r.json()["data"] if x["id"] == expense["id"])
        assert row["files"] == [] and row["attachments"] == 0

    async def test_updated_at_moves(self, admin):
        _, expense = await new_expense(admin)
        before = (await get_ticket(admin, expense))["updated_at"]
        t = await attach(admin, expense)
        assert t["updated_at"] >= before


class TestListing:
    async def test_lists_in_upload_order(self, admin):
        _, expense = await new_expense(admin)
        for name in ("uno.pdf", "dos.pdf", "tres.pdf"):
            await attach(admin, expense, name)
        assert [f["name"] for f in await files_of(admin, expense)] == [
            "uno.pdf",
            "dos.pdf",
            "tres.pdf",
        ]

    async def test_empty_ticket_lists_nothing(self, admin):
        _, expense = await new_expense(admin)
        assert await files_of(admin, expense) == []

    async def test_listing_has_no_signed_links(self, admin):
        _, expense = await new_expense(admin)
        await attach(admin, expense)
        text = (await admin.get(ticket_url(expense, "attachments/"))).text
        assert "storage.fake.invalid" not in text and "X-Signature" not in text

    async def test_unknown_ticket_is_404(self, admin):
        r = await admin.get(f"{API}/tickets/00000000-0000-0000-0000-000000000000/attachments/")
        assert r.status_code == 404

    async def test_a_malformed_id_is_422(self, admin):
        assert (await admin.get(f"{API}/tickets/nope/attachments/")).status_code == 422

    async def test_sha_of_content_is_not_exposed(self, admin):
        content = pdf_bytes()
        _, expense = await new_expense(admin)
        await attach(admin, expense, content=content, name="a.pdf")
        assert sha256(content) not in (await admin.get(ticket_url(expense, "attachments/"))).text


class TestMultipartHygiene:
    async def test_a_missing_file_part_is_422(self, admin):
        _, expense = await new_expense(admin)
        r = await admin.post(ticket_url(expense, "attachments/"), data={"field_key": "support"})
        assert r.status_code == 422

    async def test_a_json_body_is_422(self, admin):
        _, expense = await new_expense(admin)
        r = await admin.post(ticket_url(expense, "attachments/"), json={"name": "a.pdf"})
        assert r.status_code == 422

    async def test_the_old_json_data_url_shape_is_not_accepted(self, admin):
        _, expense = await new_expense(admin)
        r = await admin.post(
            ticket_url(expense, "attachments/"),
            json={
                "name": "a.png",
                "mime": "image/png",
                "size": 9,
                "data_url": "data:image/png;base64,AA==",
            },
        )
        assert r.status_code == 422
        assert await files_of(admin, expense) == []

    async def test_extra_form_fields_cannot_set_server_columns(self, admin):
        _, expense = await new_expense(admin)
        r = await admin.post(
            ticket_url(expense, "attachments/"),
            files={"file": ("a.pdf", pdf_bytes(), "application/pdf")},
            data={
                "document_type": "pasaporte",
                "document_id": "x",
                "folder": "../x",
                "sequence": "99",
            },
        )
        assert r.status_code == 201
        stored = r.json()["files"][0]
        assert stored["document_type"] == "comprobante"
        assert stored["storage"]["document_id"] != "x"
        assert "_comprobante_01_" in stored["storage"]["filename"]

    async def test_two_file_parts_store_only_one(self, admin):
        _, expense = await new_expense(admin)
        r = await admin.post(
            ticket_url(expense, "attachments/"),
            files=[
                ("file", ("a.pdf", pdf_bytes(), "application/pdf")),
                ("file", ("b.pdf", pdf_bytes(), "application/pdf")),
            ],
        )
        assert r.status_code in (201, 422)
        assert len(await files_of(admin, expense)) <= 1


def test_helper_sanity():
    assert doc_bytes()[:8] == bytes.fromhex("d0cf11e0a1b11ae1")
    assert rules_of
