import asyncio
import base64
import struct
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config.auth import TokenType
from app.utils.audit_diff import diff_values, jsonable
from app.utils.client_ip import client_ip
from app.utils.effective_permissions import has_permission
from app.utils.passwords import generate_temporary_password, password_problem
from app.utils.rate_limit import LoginThrottle, MemoryRateLimitStore
from app.utils.search import escape_like
from app.utils.signature import SignatureProblem, parse_signature
from app.utils.slug import slugify
from app.utils.tokens import TokenService


class TestAuditDiff:
    def test_reports_only_changes(self):
        out = diff_values({"a": 1, "b": 2}, {"a": 1, "b": 3}, {"b": "Be"})
        assert out == [{"field": "b", "label": "Be", "from": 2, "to": 3}]

    def test_none_to_empty_string_is_not_a_change(self):
        assert diff_values({"a": None}, {"a": ""}, {}) == []

    def test_absent_vs_value(self):
        assert diff_values({}, {"a": "x"}, {})[0]["from"] is None

    def test_jsonable_handles_uuid_and_sets(self):
        uid = uuid4()
        assert jsonable(uid) == str(uid)


class TestHasPermission:
    def test_exact(self):
        assert has_permission({"a.b"}, ["a.b"])

    def test_any_of(self):
        assert has_permission({"x"}, ["a", "x"])

    def test_none(self):
        assert not has_permission(set(), ["a"])

    def test_prefix_only_when_asked(self):
        assert not has_permission({"tickets.view.expense"}, ["tickets.view"])
        assert has_permission({"tickets.view.expense"}, ["tickets.view"], prefix_match=True)

    def test_prefix_requires_dot_boundary(self):
        assert not has_permission({"tickets.viewer"}, ["tickets.view"], prefix_match=True)


def png(width=10, height=10, extra=0):
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x00" * (5 + extra)
    )


def url(mime, data):
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


class TestSignature:
    def test_valid_png(self):
        assert parse_signature(url("image/png", png())).mime == "image/png"

    def test_valid_jpeg(self):
        assert parse_signature(url("image/jpeg", b"\xff\xd8\xff\xe0" + b"\x00" * 10)).size == 14

    @pytest.mark.parametrize(
        "raw,code",
        [
            ("hello", "format"),
            (url("image/gif", b"GIF89a"), "type"),
            ("data:image/png;base64,", "empty"),
            (url("image/png", b"not a png at all"), "content"),
            (url("image/jpeg", png()), "content"),
            (url("image/png", png(extra=300 * 1024)), "size"),
            (url("image/png", png(width=0)), "content"),
            (url("image/png", png(width=99999)), "content"),
        ],
    )
    def test_rejections(self, raw, code):
        with pytest.raises(SignatureProblem) as exc:
            parse_signature(raw)
        assert exc.value.code == code

    def test_boundary(self):
        data = png()
        data += b"\x00" * (300 * 1024 - len(data))
        assert parse_signature(url("image/png", data)).size == 300 * 1024

    def test_invalid_base64(self):
        with pytest.raises(SignatureProblem):
            parse_signature("data:image/png;base64,@@@")


class TestPasswords:
    def test_temporary_passwords_differ_and_pass_policy(self):
        a, b = generate_temporary_password(), generate_temporary_password()
        assert a != b and password_problem(a) is None

    def test_short_password_has_problem(self):
        assert password_problem("abc")

    def test_72_byte_limit(self):
        assert password_problem("a" * 80)


class TestSearch:
    def test_escapes_wildcards(self):
        assert escape_like("50%_x\\") == "50\\%\\_x\\\\"

    def test_slugify(self):
        assert slugify("Combustible Jet-A 1 ñandú") == "combustible_jet_a_1_nandu"


class TestTokens:
    def test_roundtrip(self):
        service, uid = TokenService(), uuid4()
        claims = service.read(service.issue(uid, TokenType.ACCESS), TokenType.ACCESS)
        assert claims.user_id == uid and claims.issued_at <= time.time() + 1

    def test_type_is_enforced(self):
        service = TokenService()
        assert service.read(service.issue(uuid4(), TokenType.REFRESH), TokenType.ACCESS) is None

    def test_garbage(self):
        assert TokenService().read("x.y.z", TokenType.ACCESS) is None

    def test_iat_has_sub_second_precision(self):
        service = TokenService()
        a = service.read(service.issue(uuid4(), TokenType.ACCESS), TokenType.ACCESS)
        assert a.issued_at != int(a.issued_at)


class FakeSettings:
    LOGIN_MAX_FAILS_PER_EMAIL = 3
    LOGIN_MAX_FAILS_PER_IP = 5
    LOGIN_LOCK_SECONDS = 60


class TestThrottle:
    def make(self):
        return LoginThrottle(MemoryRateLimitStore(), FakeSettings())

    async def test_locks_by_email(self):
        t = self.make()
        for _ in range(3):
            await t.register_failure("a@x.com", "1.1.1.1")
        assert await t.retry_after("a@x.com", "9.9.9.9") > 0
        assert await t.retry_after("b@x.com", "9.9.9.9") == 0

    async def test_locks_by_ip(self):
        t = self.make()
        for i in range(5):
            await t.register_failure(f"u{i}@x.com", "1.1.1.1")
        assert await t.retry_after("fresh@x.com", "1.1.1.1") > 0
        assert await t.retry_after("fresh@x.com", "2.2.2.2") == 0

    async def test_success_resets_email_counter(self):
        t = self.make()
        for _ in range(2):
            await t.register_failure("a@x.com", "1.1.1.1")
        await t.register_success("a@x.com")
        await t.register_failure("a@x.com", "1.1.1.1")
        assert await t.retry_after("a@x.com", "9.9.9.9") == 0

    async def test_email_is_case_insensitive(self):
        t = self.make()
        for _ in range(3):
            await t.register_failure("A@X.com", "1.1.1.1")
        assert await t.retry_after("a@x.COM", "2.2.2.2") > 0

    async def test_block_notice_once(self):
        t = self.make()
        for _ in range(3):
            await t.register_failure("a@x.com", "1.1.1.1")
        assert await t.first_block_notice("a@x.com", "1.1.1.1") is True
        assert await t.first_block_notice("a@x.com", "2.2.2.2") is False

    async def test_window_expires(self):
        store = MemoryRateLimitStore()
        await store.hit("k", 1)
        await asyncio.sleep(1.1)
        assert await store.count("k") == 0


class TestClientIp:
    def conn(self, host, forwarded=None):
        headers = {"x-forwarded-for": forwarded} if forwarded else {}
        return SimpleNamespace(headers=headers, client=SimpleNamespace(host=host))

    def test_peer_by_default(self, monkeypatch):
        from app.config.settings import get_settings

        monkeypatch.setattr(get_settings(), "TRUST_FORWARDED_FOR", False)
        assert client_ip(self.conn("1.2.3.4", "6.6.6.6")) == "1.2.3.4"

    def test_last_hop_when_trusted(self, monkeypatch):
        from app.config.settings import get_settings

        monkeypatch.setattr(get_settings(), "TRUST_FORWARDED_FOR", True)
        assert client_ip(self.conn("10.0.0.1", "6.6.6.6, 7.7.7.7")) == "7.7.7.7"
