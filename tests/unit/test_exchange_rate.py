"""The rate reader trusts nothing it did not validate and never invents a value."""

import httpx
import pytest

from app.config.settings import Settings
from app.integrations.exchange_rate import ExchangeRateUnavailable, client, get_exchange_rate

REAL_CLIENT = httpx.AsyncClient
GOOD = {
    "moneda": "USD",
    "fuente": "oficial",
    "promedio": 899.2705,
    "fechaActualizacion": "2026-11-10T00:00:00-04:00",
}


def settings(**overrides) -> Settings:
    return Settings(JWT_SECRET="x" * 40, EXCHANGE_RATE_MODE="real", **overrides)


@pytest.fixture(autouse=True)
def fresh_cache():
    client.clear_cache()
    yield
    client.clear_cache()


def serve(monkeypatch, handler):
    def factory(*args, **kwargs):
        return REAL_CLIENT(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(client.httpx, "AsyncClient", factory)


async def test_reads_the_official_rate(monkeypatch):
    serve(monkeypatch, lambda request: httpx.Response(200, json=GOOD))
    found = await get_exchange_rate(settings())
    assert found.rate == 899.2705
    assert found.source == "oficial" and found.effective_date == "2026-11-10"


async def test_the_second_call_comes_from_the_cache(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=GOOD)

    serve(monkeypatch, handler)
    await get_exchange_rate(settings())
    await get_exchange_rate(settings())
    assert len(calls) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {**GOOD, "promedio": -5},
        {**GOOD, "promedio": 0},
        {**GOOD, "promedio": "899"},
        {**GOOD, "promedio": True},
        {**GOOD, "promedio": 10**9},
        {**GOOD, "promedio": None},
        {**GOOD, "fuente": "paralelo"},
        [GOOD],
        "oficial",
    ],
)
async def test_an_unexpected_answer_is_unavailable_not_guessed(monkeypatch, payload):
    serve(monkeypatch, lambda request: httpx.Response(200, json=payload))
    with pytest.raises(ExchangeRateUnavailable):
        await get_exchange_rate(settings())


@pytest.mark.parametrize("status", [404, 500, 503])
async def test_an_http_error_is_unavailable(monkeypatch, status):
    serve(monkeypatch, lambda request: httpx.Response(status))
    with pytest.raises(ExchangeRateUnavailable):
        await get_exchange_rate(settings())


async def test_a_dead_network_is_unavailable(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("down")

    serve(monkeypatch, boom)
    with pytest.raises(ExchangeRateUnavailable):
        await get_exchange_rate(settings())


async def test_garbage_is_unavailable(monkeypatch):
    serve(monkeypatch, lambda request: httpx.Response(200, content=b"<html>no</html>"))
    with pytest.raises(ExchangeRateUnavailable):
        await get_exchange_rate(settings())


async def test_a_failure_is_not_cached(monkeypatch):
    serve(monkeypatch, lambda request: httpx.Response(500))
    with pytest.raises(ExchangeRateUnavailable):
        await get_exchange_rate(settings())
    serve(monkeypatch, lambda request: httpx.Response(200, json=GOOD))
    assert (await get_exchange_rate(settings())).rate == 899.2705


async def test_off_never_calls_out(monkeypatch):
    serve(monkeypatch, lambda request: pytest.fail("must not call the network"))
    with pytest.raises(ExchangeRateUnavailable):
        await get_exchange_rate(Settings(JWT_SECRET="x" * 40, EXCHANGE_RATE_MODE="off"))


def test_fake_and_http_urls_are_refused_in_production_settings():
    bad = Settings(JWT_SECRET="x" * 40, EXCHANGE_RATE_URL="http://example.com")
    assert any("EXCHANGE_RATE_URL" in i for i in bad.security_issues())
