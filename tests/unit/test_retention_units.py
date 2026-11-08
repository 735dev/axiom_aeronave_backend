"""Pure pieces of the retention and throttling work."""

import gzip

import pytest

from app.config.settings import Settings
from app.exceptions.rate_limit import RateLimitError
from app.scripts.audit_archive import verify_archive
from app.utils.rate_limit import ActionThrottle, MemoryRateLimitStore, ResilientStore


def settings(**over) -> Settings:
    return Settings(JWT_SECRET="x" * 40, **over)


class TestSettingsDefaults:
    def test_idempotency_window_is_seven_days_and_configurable(self):
        assert settings().IDEMPOTENCY_TTL_SECONDS == 7 * 24 * 3600
        assert settings(IDEMPOTENCY_TTL_SECONDS=2).IDEMPOTENCY_TTL_SECONDS == 2

    def test_retention_defaults_are_set_and_the_audit_floor_exists(self):
        s = settings()
        assert s.NOTIFICATION_RETENTION_DAYS == 90 and s.PASSENGER_RETENTION_DAYS == 1095
        assert s.AUDIT_RETENTION_DAYS >= s.AUDIT_RETENTION_FLOOR_DAYS >= 1

    def test_maintenance_connection_falls_back_to_the_owner_one(self):
        s = settings(MIGRATION_DATABASE_URL="postgresql+asyncpg://o@h/db")
        assert s.maintenance_database_url == "postgresql+asyncpg://o@h/db"
        assert (
            settings(
                MAINTENANCE_DATABASE_URL="postgresql+asyncpg://m@h/db"
            ).maintenance_database_url
            == "postgresql+asyncpg://m@h/db"
        )


class TestActionThrottle:
    def throttle(self, store=None, **over):
        base = dict(
            RATE_LIMIT_SEARCH_PER_ACCOUNT=2,
            RATE_LIMIT_SEARCH_PER_IP=3,
            RATE_LIMIT_WINDOW_SECONDS=30,
        )
        return ActionThrottle(store or MemoryRateLimitStore(), settings(**{**base, **over}))

    async def test_account_budget(self):
        t = self.throttle()
        for _ in range(2):
            await t.check("search", "u1", "1.1.1.1")
        with pytest.raises(RateLimitError) as caught:
            await t.check("search", "u1", "2.2.2.2")
        assert 1 <= caught.value.retry_after <= 30

    async def test_ip_budget_spans_accounts(self):
        t = self.throttle()
        for user in ("a", "b", "c"):
            await t.check("search", user, "9.9.9.9")
        with pytest.raises(RateLimitError):
            await t.check("search", "d", "9.9.9.9")

    async def test_actions_have_separate_budgets(self):
        t = self.throttle()
        for _ in range(2):
            await t.check("search", "u", "1.1.1.1")
        await t.check("extract", "u", "1.1.1.1")

    async def test_disabled_never_blocks(self):
        t = self.throttle(RATE_LIMIT_ENABLED=False)
        for _ in range(50):
            await t.check("search", "u", "1.1.1.1")

    async def test_a_dead_shared_store_degrades_to_memory_instead_of_opening_the_door(self):
        class Dead:
            async def hit(self, *a):
                raise ConnectionError("redis down")

            ttl = count = reset = hit

            async def close(self):
                return None

        t = self.throttle(ResilientStore(Dead(), MemoryRateLimitStore()))
        for _ in range(2):
            await t.check("search", "u", "1.1.1.1")
        with pytest.raises(RateLimitError):
            await t.check("search", "u", "1.1.1.1")


class TestVerifyArchive:
    def make(self, tmp_path, lines=3):
        import hashlib

        path = tmp_path / "a.jsonl.gz"
        with gzip.open(path, "wb") as packed:
            for i in range(lines):
                packed.write(b'{"seq": %d}\n' % i)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        path.with_name(path.name + ".sha256").write_text(f"{digest}  a.jsonl.gz\n")
        return path, digest

    def test_a_good_archive_verifies(self, tmp_path):
        path, digest = self.make(tmp_path)
        assert verify_archive(path, 3) == digest

    def test_wrong_row_count_fails(self, tmp_path):
        path, _ = self.make(tmp_path)
        assert verify_archive(path, 4) is None

    def test_a_flipped_byte_fails(self, tmp_path):
        path, _ = self.make(tmp_path)
        data = bytearray(path.read_bytes())
        data[-5] ^= 0xFF
        path.write_bytes(bytes(data))
        assert verify_archive(path, 3) is None

    def test_a_missing_checksum_or_file_fails(self, tmp_path):
        path, _ = self.make(tmp_path)
        path.with_name(path.name + ".sha256").unlink()
        assert verify_archive(path, 3) is None
        assert verify_archive(tmp_path / "nope.jsonl.gz", 1) is None
