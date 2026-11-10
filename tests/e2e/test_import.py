"""Bulk import of catalog items and users: contract, all-or-nothing and permissions."""

from tests.e2e.conftest import API, create_role, role_id

CATALOG_IMPORT = f"{API}/catalog-items/import/"
USERS_IMPORT = f"{API}/users/import/"


def item(code, name, extra=None):
    return {"code": code, "name": name, "extra": extra}


async def items_of(api, catalog):
    return (await api.get(f"{API}/catalog-items/?catalog={catalog}&limit=100")).json()["data"]


async def import_items(api, rows, catalog="units", dry_run=False):
    return await api.post(
        CATALOG_IMPORT, json={"catalog": catalog, "rows": rows, "dry_run": dry_run}
    )


class TestCatalogImport:
    async def test_dry_run_reports_without_writing(self, admin, uniq):
        code = uniq("dry")
        before = len(await items_of(admin, "units"))
        r = await import_items(admin, [item(code, "Seco")], dry_run=True)
        assert r.status_code == 200, r.text
        assert r.json() == {
            "dry_run": True,
            "total": 1,
            "created": 1,
            "updated": 0,
            "skipped": 0,
            "errors": [],
        }
        assert len(await items_of(admin, "units")) == before

    async def test_creates_updates_and_skips(self, admin, uniq):
        a, b = uniq("a"), uniq("b")
        r = await import_items(admin, [item(a, "Alfa"), item(b, "Beta", "x")])
        assert r.json()["created"] == 2 and r.json()["errors"] == []
        created = {i["code"]: i for i in await items_of(admin, "units")}
        assert created[a]["name"] == "Alfa" and created[b]["extra"] == "x"
        assert created[a]["is_active"] and not created[a]["is_system"]

        r = await import_items(admin, [item(a, "Alfa"), item(b, "Beta nueva", "x")])
        assert (r.json()["created"], r.json()["updated"], r.json()["skipped"]) == (0, 1, 1)
        after = {i["code"]: i for i in await items_of(admin, "units")}
        assert (
            after[b]["name"] == "Beta nueva" and after[a]["sort_order"] == created[a]["sort_order"]
        )

    async def test_sort_order_follows_file_order(self, admin, uniq):
        first, second = uniq("o"), uniq("p")
        await import_items(admin, [item(first, "Primero"), item(second, "Segundo")])
        by_code = {i["code"]: i for i in await items_of(admin, "units")}
        assert by_code[first]["sort_order"] + 1 == by_code[second]["sort_order"]

    async def test_blank_code_is_derived_from_the_name(self, admin, uniq):
        name = f"Tonelada {uniq()}"
        r = await import_items(admin, [{"code": "", "name": name}])
        assert r.json()["created"] == 1
        assert any(i["name"] == name for i in await items_of(admin, "units"))

    async def test_one_bad_row_writes_nothing(self, admin, uniq):
        good = uniq("g")
        rows = [item(good, "Valida"), item(uniq("e"), "x"), item("no valido!", "Nombre ok")]
        r = await import_items(admin, rows)
        body = r.json()
        assert r.status_code == 200 and body["created"] == 0
        assert [(e["row"], e["field"]) for e in body["errors"]] == [(2, "name"), (3, "code")]
        assert all(i["code"] != good for i in await items_of(admin, "units"))

    async def test_duplicate_code_inside_the_file(self, admin, uniq):
        code = uniq("d")
        r = await import_items(admin, [item(code, "Uno"), item(code, "Dos")])
        assert r.json()["errors"] == [
            {"row": 2, "field": "code", "message": "El código se repite en el archivo."}
        ]

    async def test_unknown_catalog_422(self, admin):
        assert (await import_items(admin, [item("a1", "Algo")], catalog="nope")).status_code == 422

    async def test_row_cap_422(self, admin):
        rows = [item(f"c{i}", f"Nombre {i}") for i in range(1001)]
        assert (await import_items(admin, rows)).status_code == 422

    async def test_import_is_audited(self, admin, uniq):
        code = uniq("aud")
        await import_items(admin, [item(code, "Auditada")])
        r = await admin.get(f"{API}/audit/?entity=catalog&search={code}")
        assert r.status_code == 200 and r.json()["total"] >= 1

    async def test_pilot_forbidden_and_anon_unauthorized(self, pilot, anon):
        assert (await import_items(pilot, [item("a1", "Algo")])).status_code == 403
        assert (await import_items(anon, [item("a1", "Algo")])).status_code == 401

    async def test_view_permission_is_not_enough(self, new_account):
        api, _, _ = await new_account(["catalogs.view"])
        assert (await import_items(api, [item("a1", "Algo")])).status_code == 403

    async def test_manage_permission_is_enough(self, new_account, uniq):
        api, _, _ = await new_account(["catalogs.manage"])
        r = await import_items(api, [item(uniq("m"), "Gestor")])
        assert r.status_code == 200 and r.json()["created"] == 1

    async def test_extra_keys_are_ignored(self, admin, uniq):
        code = uniq("k")
        row = {**item(code, "Con extras"), "is_system": True, "id": "x"}
        await import_items(admin, [row])
        created = next(i for i in await items_of(admin, "units") if i["code"] == code)
        assert created["is_system"] is False


def user_row(email, role="pilot", **extra):
    return {"full_name": "Importado Prueba", "email": email, "role_code": role, **extra}


async def import_users(api, rows, dry_run=False):
    return await api.post(USERS_IMPORT, json={"rows": rows, "dry_run": dry_run})


async def user_total(api):
    return (await api.get(f"{API}/users/?limit=1")).json()["total"]


class TestUserImport:
    async def test_creates_users_without_exposing_passwords(self, admin, uniq):
        emails = [f"{uniq('imp')}@example.com" for _ in range(2)]
        before = await user_total(admin)
        r = await import_users(admin, [user_row(emails[0]), user_row(emails[1], "ground")])
        assert r.status_code == 200, r.text
        assert r.json()["created"] == 2 and r.json()["errors"] == []
        assert "password" not in r.text
        assert await user_total(admin) == before + 2
        listed = (await admin.get(f"{API}/users/?q={emails[1]}")).json()["data"][0]
        assert listed["role_code"] == "ground" and listed["must_change_password"] is True
        assert listed["is_active"] is True

    async def test_imported_account_cannot_log_in_until_reset(self, admin, anon, uniq):
        email = f"{uniq('imp')}@example.com"
        await import_users(admin, [user_row(email)])
        assert (await anon.login(email, "demo1234")).status_code == 401

    async def test_dry_run_writes_nothing(self, admin, uniq):
        before = await user_total(admin)
        r = await import_users(admin, [user_row(f"{uniq('dry')}@example.com")], dry_run=True)
        assert r.json()["created"] == 1 and r.json()["dry_run"] is True
        assert await user_total(admin) == before

    async def test_one_bad_row_writes_nothing(self, admin, uniq):
        good = f"{uniq('good')}@example.com"
        r = await import_users(admin, [user_row(good), user_row("no-es-correo")])
        assert r.json()["created"] == 0
        assert [(e["row"], e["field"]) for e in r.json()["errors"]] == [(2, "email")]
        assert (await admin.get(f"{API}/users/?q={good}")).json()["total"] == 0

    async def test_row_errors_by_cause(self, admin, uniq):
        taken = "piloto@transvalor.com"
        repeated = f"{uniq('rep')}@example.com"
        rows = [
            user_row(taken),
            user_row(repeated),
            user_row(repeated.upper()),
            user_row(f"{uniq('r')}@example.com", "superuser"),
            user_row(f"{uniq('r')}@example.com", "no_such_role"),
            user_row(f"{uniq('r')}@example.com", location="atlantida"),
            user_row(f"{uniq('r')}@example.com", aircraft_id="NOPE"),
            {**user_row(f"{uniq('r')}@example.com"), "full_name": ""},
        ]
        errors = (await import_users(admin, rows)).json()["errors"]
        assert [(e["row"], e["field"]) for e in errors] == [
            (1, "email"),
            (3, "email"),
            (4, "role_code"),
            (5, "role_code"),
            (6, "location"),
            (7, "aircraft_id"),
            (8, "full_name"),
        ]

    async def test_valid_catalog_values_are_accepted(self, admin, uniq):
        email = f"{uniq('cat')}@example.com"
        r = await import_users(
            admin, [user_row(email, location="caracas", aircraft_id="YV2323", phone="0414")]
        )
        assert r.json()["created"] == 1, r.text
        created = (await admin.get(f"{API}/users/?q={email}")).json()["data"][0]
        assert (created["location"], created["aircraft_id"]) == ("caracas", "YV2323")

    async def test_cannot_assign_a_role_beyond_own_permissions(self, new_account, uniq):
        api, _, _ = await new_account(["users.manage", "users.view"])
        rows = [user_row(f"{uniq('p')}@example.com", "operations")]
        r = await import_users(api, rows)
        assert r.status_code == 200
        assert [(e["row"], e["field"]) for e in r.json()["errors"]] == [(1, "role_code")]

    async def test_role_within_reach_is_accepted(self, admin, new_account, uniq):
        role = await create_role(admin, f"Rol {uniq()}", ["tickets.view"])
        api, _, _ = await new_account(["users.manage", "tickets.view"])
        r = await import_users(api, [user_row(f"{uniq('ok')}@example.com", role["code"])])
        assert r.json()["created"] == 1, r.text
        assert await role_id(admin, role["code"]) == role["id"]

    async def test_row_cap_422(self, admin):
        rows = [user_row(f"u{i}@example.com") for i in range(1001)]
        assert (await import_users(admin, rows)).status_code == 422

    async def test_import_is_audited(self, admin, uniq):
        email = f"{uniq('aud')}@example.com"
        await import_users(admin, [user_row(email)])
        r = await admin.get(f"{API}/audit/?entity=user&search={email}")
        assert r.status_code == 200 and r.json()["total"] >= 1

    async def test_pilot_forbidden_and_anon_unauthorized(self, pilot, anon):
        assert (await import_users(pilot, [user_row("a@example.com")])).status_code == 403
        assert (await import_users(anon, [user_row("a@example.com")])).status_code == 401

    async def test_users_view_is_not_enough(self, new_account):
        api, _, _ = await new_account(["users.view"])
        assert (await import_users(api, [user_row("a@example.com")])).status_code == 403
