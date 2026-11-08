-- Runtime role of the API. Run once after `alembic upgrade head`, as the owner:
--   psql "$OWNER_URL" -v app_role=aero_app -v app_password=... -f docker/postgres/audit_grants.sql
-- Idempotent: re-run it after migrations that add tables.
--
-- The audit trail is append-only. The application enforces it in code (no update/delete
-- path, ORM listeners) and this file enforces it again in the database: the runtime role
-- may SELECT and INSERT on audit_event, never UPDATE, DELETE or TRUNCATE. Alembic keeps
-- running as the owner (MIGRATION_DATABASE_URL), so schema changes still work. A leaked
-- API credential therefore cannot rewrite or erase history.

SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'app_role', :'app_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'app_role')
\gexec

GRANT USAGE ON SCHEMA public TO :"app_role";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"app_role";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"app_role";

REVOKE ALL ON audit_event FROM :"app_role";
GRANT SELECT, INSERT ON audit_event TO :"app_role";
REVOKE ALL ON alembic_version FROM :"app_role";
