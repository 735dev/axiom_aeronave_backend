"""HTTP-only helpers for the closing, passengers, reconciliation and totals E2E files."""

from __future__ import annotations

from tests.e2e.attachments_support import attach, files_of, png_bytes, readable_png
from tests.e2e.conftest import API, Api, png_data_url
from tests.e2e.tickets_support import (
    children,
    get_ticket,
    make_expense,
    make_operation,
    patch,
    ticket_url,
)

OPERATION_STEPS = ("planning", "requirements", "ground", "technical")


def closing_url(ticket: dict | str) -> str:
    return ticket_url(ticket, "closing/")


async def closing(api: Api, ticket: dict | str) -> dict:
    r = await api.get(closing_url(ticket))
    assert r.status_code == 200, r.text
    return r.json()


async def set_signature(api: Api, value: str | None = "png") -> None:
    body = {"signature_url": png_data_url() if value == "png" else (value or "")}
    r = await api.patch(f"{API}/auth/me/signature/", json=body)
    assert r.status_code == 200, r.text


async def decide(api: Api, ticket: dict | str, step: str, decision: str = "done"):
    return await api.post(
        ticket_url(ticket, "decisions/"), json={"step_code": step, "decision": decision}
    )


async def sign(api: Api, ticket: dict | str):
    return await decide(api, ticket, "signature")


async def technical_reports(api: Api, op: dict) -> list[dict]:
    return await children(api, op, type_code="technical_report")


async def resolve_reports(api: Api, op: dict) -> None:
    """'Sin novedad' on the technical report(s) of the operation."""
    for report in await technical_reports(api, op):
        r = await patch(api, report, status_code="no_findings")
        assert r.status_code == 200, r.text


async def ready_operation(api: Api, **payload) -> dict:
    """Operation with every step but the signature done and its technical report resolved."""
    op = await make_operation(api, **payload)
    for step in OPERATION_STEPS:
        r = await decide(api, op, step)
        assert r.status_code == 201, r.text
    await resolve_reports(api, op)
    return op


async def close_operation(api: Api, op: dict | str):
    return await patch(api, op, status_code="closed")


async def closed_operation(api: Api) -> dict:
    await set_signature(api)
    op = await ready_operation(api)
    assert (await sign(api, op)).status_code == 201
    r = await close_operation(api, op)
    assert r.status_code == 200, r.text
    return r.json()


async def validated_expense(
    api: Api, op: dict, amount: float = 100, currency: str = "USD", **payload
) -> dict:
    """An expense with one receipt whose reading a person validated with these values."""
    expense = await make_expense(api, op, amount=amount, currency=currency, **payload)
    await validate_support(api, expense, amount, currency, payload.get("expense_date"))
    return await get_ticket(api, expense)


async def read_receipt(api: Api, expense: dict) -> dict:
    """Attach a readable receipt; returns the file (its reading waits for a person)."""
    await attach(api, expense, "recibo.png", content=readable_png())
    file = (await files_of(api, expense))[-1]
    assert file["extraction"] and file["extraction"]["status"] == "extracted", file
    return file


async def validate_support(
    api: Api, expense: dict, amount: float, currency: str = "USD", day: str | None = None
) -> dict:
    """Attach a readable receipt and validate its reading with the given values. The date
    and the concept are pinned to the expense's own so the reading cannot move it."""
    file = await read_receipt(api, expense)
    day = day or expense["accounting_date"]
    pinned = {"amount": amount, "currency": currency, "expense_date": day, "concept": "fuel"}
    fields = [
        {"key": f["key"], "value": pinned.get(f["key"], f["value"])}
        for f in file["extraction"]["fields"]
    ]
    body = {"fields": fields, "confirmed": [f["key"] for f in fields]}
    r = await api.post(ticket_url(expense, f"attachments/{file['id']}/validate/"), json=body)
    assert r.status_code == 200, r.text
    return file


async def supported_expense(api: Api, op: dict, **payload) -> dict:
    """An expense with a file loaded (its reading, if any, still unvalidated)."""
    expense = await make_expense(api, op, **payload)
    await attach(api, expense, "factura.pdf")
    return await get_ticket(api, expense)


async def totals(api: Api, **query) -> dict:
    params = "&".join(f"{k}={v}" for k, v in query.items())
    r = await api.get(f"{API}/tickets/totals/?{params}")
    assert r.status_code == 200, r.text
    return r.json()


__all__ = [
    "attach",
    "closing",
    "png_bytes",
    "ready_operation",
    "validated_expense",
]


async def audit_events(api: Api, **query) -> list[dict]:
    params = "&".join(f"{k}={v}" for k, v in {"limit": 100, **query}.items())
    r = await api.get(f"{API}/audit/?{params}")
    assert r.status_code == 200, r.text
    return r.json()["data"]


async def me(api: Api) -> dict:
    """The signed-in user (the session body wraps it in `user`)."""
    r = await api.get(f"{API}/auth/me/")
    assert r.status_code == 200, r.text
    return r.json()["user"]


_MONTHS = iter(range(0, 10_000))


def fresh_month() -> tuple[str, str]:
    """(period, day): a month nobody else uses, so closing it cannot lock another test.
    Counted from 2001-01 up; always in the past, never the current month."""
    n = next(_MONTHS)
    year, month = 2001 + n // 12, n % 12 + 1
    return f"{year}-{month:02d}", f"{year}-{month:02d}-10"


async def dated_expense(api: Api, op: dict, day: str, amount: float = 100, **payload) -> dict:
    return await make_expense(api, op, amount=amount, expense_date=day, **payload)


async def report(api: Api, period: str) -> dict:
    r = await api.get(f"{API}/reconciliation/?period={period}")
    assert r.status_code == 200, r.text
    return r.json()


async def acknowledge(api: Api, period: str, key: str, note: str = "Justificación válida"):
    return await api.post(
        f"{API}/reconciliation/acknowledgements/", json={"period": period, "key": key, "note": note}
    )


async def close_period(api: Api, period: str):
    return await api.post(f"{API}/reconciliation/close/", json={"period": period})


async def justify_all(api: Api, period: str) -> None:
    for d in (await report(api, period))["differences"]:
        r = await acknowledge(api, period, d["key"])
        assert r.status_code == 200, r.text
