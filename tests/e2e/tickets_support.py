"""HTTP-only helpers shared by the ticket E2E files."""

from __future__ import annotations

import uuid

from tests.e2e.conftest import API, Api

OPERATION = {
    "date": "2026-08-19",
    "origin": "CCS",
    "destination": "PZO",
    "aircraft": "YV2323",
}
EXPENSE = {"concept": "fuel", "amount": 100, "currency": "USD"}
TICKETS = f"{API}/tickets/"


def ticket_url(ticket: dict | str, suffix: str = "") -> str:
    ticket_id = ticket if isinstance(ticket, str) else ticket["id"]
    return f"{TICKETS}{ticket_id}/{suffix}"


async def make_operation(api: Api, title: str = "Vuelo de prueba", **payload) -> dict:
    body = {"type_code": "operation", "title": title, "payload": {**OPERATION, **payload}}
    r = await api.post(TICKETS, json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def make_expense(api: Api, parent: dict | str, **payload) -> dict:
    r = await post_expense(api, parent, **payload)
    assert r.status_code == 201, r.text
    return r.json()


async def post_expense(api: Api, parent: dict | str, title: str = "Gasto de prueba", **payload):
    parent_id = parent if isinstance(parent, str) else parent["id"]
    return await api.post(
        TICKETS,
        json={
            "type_code": "expense",
            "title": title,
            "parent_id": parent_id,
            "payload": {**EXPENSE, **payload},
        },
    )


async def get_ticket(api: Api, ticket: dict | str) -> dict:
    r = await api.get(ticket_url(ticket))
    assert r.status_code == 200, r.text
    return r.json()


async def children(api: Api, parent: dict | str, **query) -> list[dict]:
    parent_id = parent if isinstance(parent, str) else parent["id"]
    params = "&".join(
        f"{k}={v}" for k, v in {"parent_id": parent_id, "limit": 100, **query}.items()
    )
    r = await api.get(f"{TICKETS}?{params}")
    assert r.status_code == 200, r.text
    return r.json()["data"]


async def history(api: Api, ticket: dict | str) -> list[dict]:
    r = await api.get(ticket_url(ticket, "history/?limit=100"))
    assert r.status_code == 200, r.text
    return r.json()["data"]


async def patch(api: Api, ticket: dict | str, **body):
    return await api.patch(ticket_url(ticket), json=body)


def new_key() -> str:
    return f"key-{uuid.uuid4().hex}"


async def type_by_code(api: Api, code: str) -> dict:
    r = await api.get(f"{API}/ticket-types/")
    assert r.status_code == 200, r.text
    return next(t for t in r.json() if t["code"] == code)


async def rules_of(api: Api, type_code: str = "expense") -> list[dict]:
    r = await api.get(f"{API}/rules/?type_code={type_code}")
    assert r.status_code == 200, r.text
    return r.json()


async def rule_named(api: Api, fragment: str) -> dict:
    return next(r for r in await rules_of(api) if fragment in r["name"])


def field(key: str, label: str | None = None, type_: str = "text", **extra) -> dict:
    return {"key": key, "label": label or key.title(), "type": type_, **extra}


def status(code: str, name: str | None = None, **extra) -> dict:
    return {"code": code, "name": name or code.title(), **extra}


def type_body(code: str | None = None, **over) -> dict:
    suffix = uuid.uuid4().hex[:6]
    body = {
        "name": f"Tipo {suffix}",
        "code": code or f"tipo_{suffix}",
        "is_root": False,
        "fields": [field("detail", "Detalle", "text", required=True)],
        "statuses": [
            status("open", "Abierto", is_initial=True),
            status("done", "Hecho", is_final=True),
        ],
    }
    body.update(over)
    return body
