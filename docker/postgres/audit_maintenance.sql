-- Maintenance role of the audit trail. Run once, as the owner:
--   psql "$OWNER_URL" -v maint_role=aero_maint -v maint_password=... -f docker/postgres/audit_maintenance.sql
--
-- The runtime role (aero_app, docker/postgres/audit_grants.sql) can only SELECT and INSERT on
-- audit_event: a leaked API credential cannot erase history. Retention still needs rows to
-- leave, and it happens ONLY through `python -m app.scripts.audit_archive`, which connects with
-- THIS role (MAINTENANCE_DATABASE_URL). The role is not used by the API, is not reachable from
-- the internet, and holds the minimum: it can read the trail, append its summary event and delete
-- archived rows. No UPDATE (history cannot be rewritten) and no TRUNCATE; nothing on any other
-- table.

SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'maint_role', :'maint_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'maint_role')
\gexec

GRANT USAGE ON SCHEMA public TO :"maint_role";
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM :"maint_role";
GRANT SELECT, INSERT, DELETE ON audit_event TO :"maint_role";
