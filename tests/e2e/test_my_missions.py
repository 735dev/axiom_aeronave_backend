"""'Mis misiones': ?mine=true and the one-person assignment (assigned_user_id)."""

import uuid

from tests.e2e.closing_support import me
from tests.e2e.conftest import API
from tests.e2e.tickets_support import make_operation, patch

ROLE_SCOPED = {"limit": 100, "open": "true"}


async def mine(api, token: str | None = None, **extra) -> list[dict]:
    """Open work that is mine, narrowed by the unique title token the test created."""
    narrow = {"q": token} if token else {}
    params = "&".join(
        f"{k}={v}" for k, v in {**ROLE_SCOPED, "mine": "true", **narrow, **extra}.items()
    )
    r = await api.get(f"{API}/tickets/?{params}")
    assert r.status_code == 200, r.text
    return r.json()["data"]


def ids(rows: list[dict]) -> set[str]:
    return {t["id"] for t in rows}


def token() -> str:
    return uuid.uuid4().hex[:12]


async def op_for(admin, title: str | None = None, **body):
    r = await admin.post(
        f"{API}/tickets/",
        json={
            "type_code": "operation",
            "title": title or f"Misión {token()}",
            "payload": {
                "date": "2026-08-19",
                "origin": "CCS",
                "destination": "PZO",
                "aircraft": "YV2323",
            },
            **body,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


class TestRoleAssignment:
    async def test_the_root_assigned_to_my_role_is_mine(self, admin, pilot):
        role = (await me(pilot))["role_id"]
        op = await op_for(admin, assigned_role_id=role)
        assert op["id"] in ids(await mine(pilot, op["title"]))

    async def test_what_is_assigned_to_another_role_is_not(self, admin, pilot, maintenance, ops):
        op = await op_for(admin, assigned_role_id=(await me(pilot))["role_id"])
        assert op["id"] not in ids(await mine(maintenance, op["title"]))
        assert op["id"] not in ids(await mine(ops, op["title"]))

    async def test_child_work_of_my_role_is_mine(self, admin, maintenance):
        op = await make_operation(admin)
        role = (await me(maintenance))["role_id"]
        r = await admin.post(
            f"{API}/tickets/",
            json={
                "type_code": "expense",
                "title": "Gasto del rol",
                "parent_id": op["id"],
                "assigned_role_id": role,
                "payload": {"concept": "fuel", "amount": 5, "currency": "USD"},
            },
        )
        assert r.status_code == 201
        assert r.json()["id"] in ids(await mine(maintenance, parent_id=op["id"]))

    async def test_the_auto_report_is_assigned_to_the_maintenance_or_pilot_role(
        self, admin, pilot, maintenance
    ):
        op = await make_operation(admin)
        mine_ids = ids(await mine(pilot, parent_id=op["id"])) | ids(
            await mine(maintenance, parent_id=op["id"])
        )
        reports = await admin.get(f"{API}/tickets/?parent_id={op['id']}&type_code=technical_report")
        assert reports.json()["data"][0]["id"] in mine_ids


class TestOnePersonAssignment:
    async def test_assigned_to_me_by_id_is_mine_even_for_another_role(self, admin, pilot):
        user = (await me(pilot))["id"]
        op = await op_for(admin, assigned_user_id=user)
        assert op["id"] in ids(await mine(pilot, op["title"]))

    async def test_the_named_person_wins_over_the_role(self, admin, pilot, maintenance):
        pilot_me, maint_me = await me(pilot), await me(maintenance)
        op = await op_for(
            admin, assigned_role_id=pilot_me["role_id"], assigned_user_id=maint_me["id"]
        )
        assert op["id"] in ids(await mine(maintenance, op["title"]))
        assert op["id"] not in ids(await mine(pilot, op["title"]))

    async def test_reassigning_moves_the_mission_between_people(self, admin, pilot, maintenance):
        pilot_id, maint_id = (await me(pilot))["id"], (await me(maintenance))["id"]
        op = await op_for(admin, assigned_user_id=pilot_id)
        assert op["id"] in ids(await mine(pilot, op["title"]))
        r = await patch(admin, op, assigned_user_id=maint_id)
        assert r.status_code == 200
        assert op["id"] not in ids(await mine(pilot, op["title"]))
        assert op["id"] in ids(await mine(maintenance, op["title"]))

    async def test_clearing_the_person_returns_the_mission_to_the_role(self, admin, pilot):
        me_pilot = await me(pilot)
        op = await op_for(
            admin, assigned_role_id=me_pilot["role_id"], assigned_user_id=me_pilot["id"]
        )
        await patch(admin, op, assigned_user_id=None)
        assert op["id"] in ids(await mine(pilot, op["title"]))

    async def test_the_response_names_the_assignee(self, admin, pilot):
        me_pilot = await me(pilot)
        op = await op_for(admin, assigned_user_id=me_pilot["id"])
        assert op["assigned_user_id"] == me_pilot["id"] and op["assigned_user_name"]

    async def test_an_unknown_person_is_422(self, admin):
        r = await admin.post(
            f"{API}/tickets/",
            json={
                "type_code": "operation",
                "title": "x",
                "assigned_user_id": str(uuid.uuid4()),
                "payload": {
                    "date": "2026-08-19",
                    "origin": "CCS",
                    "destination": "PZO",
                    "aircraft": "YV2323",
                },
            },
        )
        assert r.status_code == 422 and "assigned_user_id" in r.json().get(
            "errors", {"assigned_user_id": 1}
        )

    async def test_reassigning_to_an_unknown_person_is_422(self, admin):
        op = await make_operation(admin)
        assert (await patch(admin, op, assigned_user_id=str(uuid.uuid4()))).status_code == 422

    async def test_a_malformed_person_id_is_422(self, admin):
        op = await make_operation(admin)
        assert (await patch(admin, op, assigned_user_id="no-uuid")).status_code == 422


class TestWhatMineMeans:
    async def test_it_only_brings_open_work_when_asked_to(self, admin, pilot):
        role = (await me(pilot))["role_id"]
        op = await op_for(admin, assigned_role_id=role)
        assert (await patch(admin, op, status_code="cancelled")).status_code == 200
        assert op["id"] not in ids(await mine(pilot, op["title"]))
        assert op["id"] in ids(await mine(pilot, op["title"], open="false"))

    async def test_without_mine_the_listing_is_unchanged(self, admin, pilot):
        everything = (await pilot.get(f"{API}/tickets/?limit=1")).json()["total"]
        only_mine = (await pilot.get(f"{API}/tickets/?limit=1&mine=true")).json()["total"]
        assert only_mine <= everything

    async def test_mine_false_is_the_same_as_no_filter(self, pilot):
        a = (await pilot.get(f"{API}/tickets/?limit=1&mine=false")).json()["total"]
        b = (await pilot.get(f"{API}/tickets/?limit=1")).json()["total"]
        assert a == b

    async def test_a_garbage_mine_value_is_422(self, pilot):
        assert (await pilot.get(f"{API}/tickets/?mine=quizas")).status_code == 422

    async def test_it_combines_with_other_filters(self, admin, pilot):
        role = (await me(pilot))["role_id"]
        op = await op_for(admin, assigned_role_id=role)
        rows = await mine(pilot, op["title"], is_root="true")
        assert op["id"] in ids(rows) and all(t["type_is_root"] for t in rows)

    async def test_it_never_widens_the_visibility_scope(self, admin, new_account):
        viewer, user, _ = await new_account(["tickets.view.expense"])
        me_viewer = await me(viewer)
        op = await op_for(admin, assigned_user_id=me_viewer["id"])
        assert op["id"] not in ids(await mine(viewer, op["title"]))

    async def test_my_view_does_not_include_other_peoples_named_work(
        self, admin, pilot, maintenance
    ):
        op = await op_for(admin, assigned_user_id=(await me(maintenance))["id"])
        assert op["id"] not in ids(await mine(pilot, op["title"]))

    async def test_the_explicit_assignee_filter_still_works(self, admin, pilot):
        me_pilot = await me(pilot)
        op = await op_for(admin, assigned_user_id=me_pilot["id"])
        r = await admin.get(f"{API}/tickets/?assigned_user_id={me_pilot['id']}&limit=100")
        assert op["id"] in ids(r.json()["data"])

    async def test_mine_for_the_anonymous_is_401(self, anon):
        assert (await anon.get(f"{API}/tickets/?mine=true")).status_code == 401
