"""Per-account and per-IP budgets of the costly endpoints (own server, strict limits)."""

import asyncio
import uuid

import pytest

from tests.e2e.attachments_support import png_bytes
from tests.e2e.conftest import API
from tests.e2e.live_support import live_server  # noqa: F401  (fixture)

ACCOUNT, IP = 3, 5
WINDOW = 60
STRICT = {
    "RATE_LIMIT_WINDOW_SECONDS": str(WINDOW),
    "RATE_LIMIT_EXTRACT_PER_ACCOUNT": str(ACCOUNT),
    "RATE_LIMIT_EXTRACT_PER_IP": str(IP),
    "RATE_LIMIT_UPLOAD_PER_ACCOUNT": str(ACCOUNT),
    "RATE_LIMIT_UPLOAD_PER_IP": str(IP),
    "RATE_LIMIT_SEARCH_PER_ACCOUNT": str(ACCOUNT),
    "RATE_LIMIT_SEARCH_PER_IP": str(IP),
    "RATE_LIMIT_EXPORT_PER_ACCOUNT": str(ACCOUNT),
    "RATE_LIMIT_EXPORT_PER_IP": str(IP),
    "RATE_LIMIT_IMPORT_PER_ACCOUNT": str(ACCOUNT),
    "RATE_LIMIT_IMPORT_PER_IP": str(IP),
    "RATE_LIMIT_AUDIT_SEARCH_PER_ACCOUNT": str(ACCOUNT),
    "RATE_LIMIT_AUDIT_SEARCH_PER_IP": str(IP),
    "RATE_LIMIT_HEAVY_LIST_PER_ACCOUNT": str(ACCOUNT),
    "RATE_LIMIT_HEAVY_LIST_PER_IP": str(IP),
}
PERIOD = {"period": "2025-01"}
GHOST = "00000000-0000-0000-0000-000000000000"


async def hit_extract(api):
    return await api.post(
        f"{API}/attachments/extract/",
        files={"file": ("r.png", png_bytes(), "image/png")},
        data={"type_code": "no_such_type"},
    )


async def hit_upload(api):
    return await api.post(
        f"{API}/tickets/{GHOST}/attachments/", files={"file": ("r.png", png_bytes(), "image/png")}
    )


async def hit_search(api):
    return await api.get(f"{API}/passengers/search/", params={"q": "maria"})


async def hit_audit_export(api):
    return await api.get(f"{API}/audit/export/")


async def hit_audit_search(api):
    return await api.get(f"{API}/audit/", params={"search": "login"})


async def hit_reconciliation_export(api):
    return await api.get(f"{API}/reconciliation/export/", params=PERIOD)


async def hit_reconciliation_record(api):
    return await api.post(f"{API}/reconciliation/export/", json=PERIOD)


async def hit_reconciliation_report(api):
    return await api.get(f"{API}/reconciliation/", params=PERIOD)


async def hit_ticket_list(api):
    return await api.get(f"{API}/tickets/", params={"q": "yv"})


async def hit_import(api):
    return await api.post(f"{API}/users/import/", json={"rows": [], "dry_run": True})


async def hit_ticket_totals(api):
    return await api.get(f"{API}/tickets/totals/")


ACTIONS = pytest.mark.parametrize(
    "hit",
    [
        hit_extract,
        hit_upload,
        hit_search,
        hit_audit_export,
        hit_audit_search,
        hit_reconciliation_export,
        hit_reconciliation_record,
        hit_reconciliation_report,
        hit_ticket_list,
        hit_ticket_totals,
        hit_import,
    ],
    ids=[
        "extract",
        "upload",
        "search",
        "audit_export",
        "audit_search",
        "reconciliation_export",
        "reconciliation_record",
        "reconciliation_report",
        "ticket_list",
        "ticket_totals",
        "import",
    ],
)


@pytest.fixture(scope="module")
def strict(live_server):  # noqa: F811
    return live_server(**STRICT)


@pytest.fixture
def remote_user(new_account, strict):
    """A fresh account logged in on the strict server from its own IP."""

    async def make(ip: str | None = None):
        _, user, password = await new_account(
            [
                "tickets.view",
                "passengers.view",
                "tickets.create",
                "tickets.edit",
                "analytics.view",
                "audit.view",
                "audit.export",
                "users.manage",
            ]
        )
        api = strict(ip=ip)
        r = await api.login(user["email"], password)
        assert r.status_code == 200, r.text
        return api.as_token(r.json()["token"]), user

    return make


class TestPerAccount:
    @ACTIONS
    async def test_the_account_budget_ends_in_429_with_retry_after(self, remote_user, hit):
        api, _ = await remote_user()
        for _ in range(ACCOUNT):
            assert (await hit(api)).status_code != 429
        blocked = await hit(api)
        assert blocked.status_code == 429
        assert 1 <= int(blocked.headers["retry-after"]) <= WINDOW
        assert uuid.UUID(GHOST).hex not in blocked.text and "Traceback" not in blocked.text

    @ACTIONS
    async def test_changing_ip_does_not_reset_the_account(self, remote_user, strict, hit):
        api, _ = await remote_user()
        for _ in range(ACCOUNT + 1):
            await hit(api)
        elsewhere = strict(token=api.token, ip="198.51.100.200")
        assert (await hit(elsewhere)).status_code == 429

    @ACTIONS
    async def test_one_account_does_not_spend_another_ones_budget(self, remote_user, hit):
        first, _ = await remote_user()
        for _ in range(ACCOUNT + 1):
            await hit(first)
        second, _ = await remote_user()
        assert (await hit(second)).status_code != 429


class TestPerIp:
    @ACTIONS
    async def test_many_accounts_behind_one_ip_share_the_ip_budget(self, remote_user, hit):
        ip = f"203.0.113.{uuid.uuid4().int % 200 + 20}"
        statuses = []
        for _ in range(IP + 1):
            api, _ = await remote_user(ip=ip)
            statuses.append((await hit(api)).status_code)
        assert 429 not in statuses[:IP]
        assert statuses[IP] == 429


class TestEverythingElseIsUntouched:
    async def test_other_endpoints_have_no_action_budget(self, remote_user):
        api, _ = await remote_user()
        for _ in range(ACCOUNT * 3):
            assert (await api.get(f"{API}/auth/me/")).status_code == 200


class TestWindow:
    async def test_the_budget_comes_back_when_the_window_ends(
        self, live_server, new_account  # noqa: F811
    ):
        make = live_server(**{**STRICT, "RATE_LIMIT_WINDOW_SECONDS": "2"})
        _, user, password = await new_account(["tickets.view", "passengers.view"])
        api = make()
        token = (await api.login(user["email"], password)).json()["token"]
        api = api.as_token(token)
        for _ in range(ACCOUNT):
            await hit_search(api)
        assert (await hit_search(api)).status_code == 429
        await asyncio.sleep(2.5)
        assert (await hit_search(api)).status_code != 429
