"""Guards against the worker-hang class of bugs: busy loops on a hung-up client and
user-controlled text that could make a pattern or a loop superlinear.

A real incident motivates the first block: after the client disconnects, an ASGI server
answers `receive()` with `http.disconnect` immediately and forever. A body reader that
`continue`s on unknown messages then spins without ever yielding (100% CPU, the event loop
serves nobody). Every test here is time-boxed so a regression fails instead of hanging.
"""

import asyncio
import base64
import json
import os
import time

import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret-0123456789")
os.environ.setdefault("ENVIRONMENT", "test")

from app.config.settings import get_settings  # noqa: E402
from app.integrations.receipt_reader.parsing import extract_json_object  # noqa: E402
from app.middleware.idempotency import CLIENT_GONE, IdempotencyMiddleware, canonical  # noqa: E402
from app.utils.passengers import (
    clean_query,
    document_query,
    fold_text,
    normalize_document,
)  # noqa: E402
from app.utils.signature import SignatureProblem, parse_signature  # noqa: E402
from app.utils.uploads import display_name, split_name  # noqa: E402

BUDGET_SECONDS = 1.0


def timed(fn, *args):
    start = time.perf_counter()
    result = fn(*args)
    elapsed = time.perf_counter() - start
    assert elapsed < BUDGET_SECONDS, f"{fn.__name__} took {elapsed:.2f}s"
    return result


class Spin(Exception):
    """The reader asked `receive()` too many times: it is looping on a dead connection."""


def dead_connection(limit: int = 50):
    calls = 0

    async def receive():
        nonlocal calls
        calls += 1
        if calls > limit:
            raise Spin
        return {"type": "http.disconnect"}

    return receive


class TestDisconnect:
    async def test_the_body_reader_stops_when_the_client_hangs_up(self):
        assert await IdempotencyMiddleware._read_body(dead_connection()) is CLIENT_GONE

    async def test_a_hangup_after_part_of_the_body_also_stops(self):
        script = iter(
            [
                {"type": "http.request", "body": b'{"a":', "more_body": True},
                {"type": "http.disconnect"},
            ]
        )

        async def receive():
            return next(script)

        assert await IdempotencyMiddleware._read_body(receive) is CLIENT_GONE

    async def test_the_middleware_ends_silently_and_never_calls_the_app(self):
        called = []

        async def app(scope, receive, send):
            called.append(True)

        middleware = IdempotencyMiddleware(app)
        sent = []

        async def send(message):
            sent.append(message)

        path = f"{get_settings().API_V1_STR}/tickets/"
        scope = {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [(b"idempotency-key", b"abc-123")],
            "state": {"principal": type("P", (), {"user_id": "u"})()},
        }
        await asyncio.wait_for(middleware(scope, dead_connection(), send), 2)
        assert called == [] and sent == []

    async def test_a_complete_body_is_still_read(self):
        script = iter(
            [
                {"type": "http.request", "body": b"ab", "more_body": True},
                {"type": "http.request", "body": b"cd", "more_body": False},
            ]
        )

        async def receive():
            return next(script)

        assert await IdempotencyMiddleware._read_body(receive) == b"abcd"


class TestHostileBodies:
    def test_a_deeply_nested_body_is_not_a_500(self):
        body = b"[" * 200_000 + b"]" * 200_000
        assert timed(canonical, body) == body

    def test_a_wide_body_normalises_in_bounded_time(self):
        body = json.dumps({f"k{i}": i for i in range(50_000)}).encode()
        assert timed(canonical, body)


HOSTILE = [
    "a" * 200_000,
    " " * 200_000,
    "́" * 100_000,
    "á" * 50_000,
    ("-" * 9 + ".") * 20_000,
    "\x00" * 100_000,
    "(" * 100_000,
    "‮" * 100_000,
    "9" * 200_000,
    "{" * 100_000,
    "[" * 100_000,
    "```json" + " " * 100_000,
]


class TestUserTextIsLinear:
    @pytest.mark.parametrize("text", HOSTILE, ids=lambda t: f"{len(t)}:{t[:2]!r}")
    def test_passenger_normalisers(self, text):
        timed(fold_text, text)
        timed(normalize_document, text)
        timed(document_query, text)
        timed(clean_query, text)

    @pytest.mark.parametrize("text", HOSTILE, ids=lambda t: f"{len(t)}:{t[:2]!r}")
    def test_file_names(self, text):
        stem, extension = timed(split_name, text + ".png")
        timed(display_name, stem, extension)

    @pytest.mark.parametrize("text", HOSTILE, ids=lambda t: f"{len(t)}:{t[:2]!r}")
    def test_signature_data_url(self, text):
        for candidate in (
            text,
            "data:image/png;base64," + text,
            "data:" + text + ";base64,AAAA",
            "data:image/png;base64," + "A" * 400_000 + "=" * 5,
        ):
            with pytest.raises(SignatureProblem):
                timed(parse_signature, candidate)

    def test_a_valid_looking_signature_with_a_huge_payload_is_refused_fast(self):
        payload = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 2_000_000).decode()
        with pytest.raises(SignatureProblem):
            timed(parse_signature, f"data:image/png;base64,{payload}")

    @pytest.mark.parametrize("text", HOSTILE, ids=lambda t: f"{len(t)}:{t[:2]!r}")
    def test_model_output_parsing_does_not_go_quadratic(self, text):
        assert timed(extract_json_object, text) is None
        assert timed(extract_json_object, "```json " + text) is None
        assert timed(extract_json_object, "```" + text + "```") is None
        assert timed(extract_json_object, '{"a": 1} ' + text) == {"a": 1}

    def test_many_fences_do_not_go_quadratic(self):
        assert timed(extract_json_object, "``` " * 60_000) is None


class TestPatternsAreAnchoredAndFlat:
    """Patterns that run on user text: ReDoS shapes (nested quantifiers / overlapping
    alternations) at the length the API actually lets through."""

    @pytest.mark.parametrize(
        "pattern_text",
        [
            "a" * 100_000 + "!",
            "1" * 100_000 + "x",
            "V" + "-" * 100_000,
            "a." * 50_000 + "!",
            "https://" + "a" * 100_000 + " ",
            "data:image/png;base64," + "A" * 100_000 + "!",
        ],
    )
    def test_schema_and_config_patterns(self, pattern_text):
        from app.config.closing import PERIOD_PATTERN
        from app.config.tickets import (
            IDEMPOTENCY_KEY_PATTERN,
            KEY_PATTERN,
            REGISTRATION_PATTERN,
            ROUTE_PATTERN,
            TYPE_CODE_PATTERN,
        )
        from app.schemas.user import _AVATAR_PATTERN
        from app.services.catalog_service import _CODE_PATTERN
        from app.utils.ticket_payload import _TIME_PATTERN

        for pattern in (
            PERIOD_PATTERN,
            IDEMPOTENCY_KEY_PATTERN,
            KEY_PATTERN,
            REGISTRATION_PATTERN,
            ROUTE_PATTERN,
            TYPE_CODE_PATTERN,
            _AVATAR_PATTERN,
            _CODE_PATTERN,
            _TIME_PATTERN,
        ):
            timed(pattern.match, pattern_text)
