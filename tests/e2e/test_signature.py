import base64

from tests.e2e.conftest import API, jpeg_data_url, png_data_url

SIG = f"{API}/auth/me/signature/"
ME = f"{API}/auth/me/"


class TestSignature:
    async def test_requires_token(self, anon):
        assert (await anon.get(SIG)).status_code == 401
        assert (await anon.patch(SIG, json={"signature_url": png_data_url()})).status_code == 401

    async def test_empty_by_default(self, new_account):
        api, _, _ = await new_account(["tickets.view"])
        r = await api.get(SIG)
        assert r.status_code == 200 and r.json() == {"signature_url": None}

    async def test_upload_png_and_read_back(self, new_account):
        api, _, _ = await new_account(["tickets.view"])
        url = png_data_url()
        r = await api.patch(SIG, json={"signature_url": url})
        assert r.status_code == 200 and r.json()["signature_url"] == url
        assert (await api.get(SIG)).json()["signature_url"] == url

    async def test_upload_jpeg(self, new_account):
        api, _, _ = await new_account(["tickets.view"])
        assert (await api.patch(SIG, json={"signature_url": jpeg_data_url()})).status_code == 200

    async def test_me_reports_has_signature(self, new_account):
        api, _, _ = await new_account(["tickets.view"])
        assert (await api.get(ME)).json()["user"]["has_signature"] is False
        await api.patch(SIG, json={"signature_url": png_data_url()})
        body = (await api.get(ME)).json()["user"]
        assert body["has_signature"] is True and "signature_url" not in body

    async def test_replace(self, new_account):
        api, _, _ = await new_account(["tickets.view"])
        await api.patch(SIG, json={"signature_url": png_data_url(size=100)})
        second = png_data_url(size=500)
        assert (await api.patch(SIG, json={"signature_url": second})).json()[
            "signature_url"
        ] == second

    async def test_remove_with_null_and_empty_string(self, new_account):
        api, _, _ = await new_account(["tickets.view"])
        await api.patch(SIG, json={"signature_url": png_data_url()})
        assert (await api.patch(SIG, json={"signature_url": None})).json()["signature_url"] is None
        await api.patch(SIG, json={"signature_url": png_data_url()})
        assert (await api.patch(SIG, json={"signature_url": ""})).json()["signature_url"] is None

    async def test_exactly_300kb_accepted(self, new_account):
        api, _, _ = await new_account(["tickets.view"])
        r = await api.patch(SIG, json={"signature_url": png_data_url(size=300 * 1024)})
        assert r.status_code == 200, r.text

    async def test_over_300kb_rejected(self, new_account):
        api, _, _ = await new_account(["tickets.view"])
        r = await api.patch(SIG, json={"signature_url": png_data_url(size=300 * 1024 + 1)})
        assert r.status_code == 422
        assert (await api.get(SIG)).json()["signature_url"] is None

    async def test_gif_and_svg_and_pdf_rejected(self, new_account):
        api, _, _ = await new_account(["tickets.view"])
        body = base64.b64encode(b"GIF89a" + b"\x00" * 50).decode()
        for mime in ("image/gif", "image/svg+xml", "application/pdf", "text/html"):
            r = await api.patch(SIG, json={"signature_url": f"data:{mime};base64,{body}"})
            assert r.status_code == 422, mime

    async def test_declared_png_with_other_bytes_rejected(self, new_account):
        api, _, _ = await new_account(["tickets.view"])
        fake = base64.b64encode(b"<svg onload=alert(1)>" + b"\x00" * 40).decode()
        assert (
            await api.patch(SIG, json={"signature_url": f"data:image/png;base64,{fake}"})
        ).status_code == 422

    async def test_declared_jpeg_with_png_bytes_rejected(self, new_account):
        api, _, _ = await new_account(["tickets.view"])
        png_bytes = png_data_url().split(",", 1)[1]
        assert (
            await api.patch(SIG, json={"signature_url": f"data:image/jpeg;base64,{png_bytes}"})
        ).status_code == 422

    async def test_not_a_data_url_rejected(self, new_account):
        api, _, _ = await new_account(["tickets.view"])
        for value in (
            "https://evil.example.com/sig.png",
            "javascript:alert(1)",
            "hello",
            "data:image/png;base64,@@@@",
        ):
            assert (await api.patch(SIG, json={"signature_url": value})).status_code == 422, value

    async def test_empty_payload_after_prefix_rejected(self, new_account):
        api, _, _ = await new_account(["tickets.view"])
        assert (
            await api.patch(SIG, json={"signature_url": "data:image/png;base64,"})
        ).status_code == 422

    async def test_truncated_png_header_rejected(self, new_account):
        api, _, _ = await new_account(["tickets.view"])
        body = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4).decode()
        assert (
            await api.patch(SIG, json={"signature_url": f"data:image/png;base64,{body}"})
        ).status_code == 422

    async def test_absurd_dimensions_rejected(self, new_account):
        api, _, _ = await new_account(["tickets.view"])
        assert (
            await api.patch(SIG, json={"signature_url": png_data_url(width=90000, height=90000)})
        ).status_code == 422

    async def test_owner_comes_from_token_not_from_body(self, new_account):
        a, _, _ = await new_account(["tickets.view"])
        b, b_user, _ = await new_account(["tickets.view"])
        r = await a.patch(SIG, json={"signature_url": png_data_url(), "user_id": b_user["id"]})
        assert r.status_code in (200, 422)
        assert (await b.get(SIG)).json()["signature_url"] is None

    async def test_users_do_not_see_each_others_signature(self, new_account):
        a, _, _ = await new_account(["tickets.view"])
        b, _, _ = await new_account(["tickets.view"])
        await a.patch(SIG, json={"signature_url": png_data_url()})
        assert (await b.get(SIG)).json()["signature_url"] is None

    async def test_no_route_exposes_a_signature_by_user_id(self, admin, new_account):
        _, user, _ = await new_account(["tickets.view"])
        for path in (
            f"{API}/users/{user['id']}/signature/",
            f"{API}/auth/me/{user['id']}/signature/",
        ):
            assert (await admin.get(path)).status_code in (404, 405)

    async def test_admin_user_detail_never_contains_the_image(self, admin, new_account):
        api, user, _ = await new_account(["tickets.view"])
        await api.patch(SIG, json={"signature_url": png_data_url()})
        r = await admin.get(f"{API}/users/{user['id']}/")
        assert "data:image" not in r.text and r.json()["has_signature"] is True

    async def test_session_revoked_user_cannot_touch_signature(self, new_account):
        api, _, _ = await new_account(["tickets.view"])
        await api.post(f"{API}/auth/logout/")
        assert (await api.get(SIG)).status_code == 401

    async def test_unknown_field_in_body_ignored_or_refused(self, new_account):
        api, _, _ = await new_account(["tickets.view"])
        assert (await api.patch(SIG, json={})).status_code in (200, 422)

    async def test_upload_is_audited_without_the_image(self, admin, new_account):
        api, user, _ = await new_account(["tickets.view"])
        url = png_data_url(size=400)
        await api.patch(SIG, json={"signature_url": url})
        events = (await admin.get(f"{API}/audit/?entity=user&entity_id={user['id']}")).json()[
            "data"
        ]
        sig_events = [e for e in events if "firma" in e["summary"].lower()]
        assert sig_events
        assert url.split(",", 1)[1] not in str(events)
        assert "data:image" not in str(events)
        assert sig_events[0]["actor_id"] == user["id"]

    async def test_removal_is_audited(self, admin, new_account):
        api, user, _ = await new_account(["tickets.view"])
        await api.patch(SIG, json={"signature_url": png_data_url()})
        await api.patch(SIG, json={"signature_url": None})
        events = (
            await admin.get(f"{API}/audit/?entity=user&entity_id={user['id']}&search=firma")
        ).json()["data"]
        assert any("Eliminó" in e["summary"] for e in events)

    async def test_unchanged_upload_writes_no_second_event(self, admin, new_account):
        api, user, _ = await new_account(["tickets.view"])
        url = png_data_url()
        await api.patch(SIG, json={"signature_url": url})
        await api.patch(SIG, json={"signature_url": url})
        events = (
            await admin.get(f"{API}/audit/?entity=user&entity_id={user['id']}&search=firma")
        ).json()["data"]
        assert len(events) == 1
