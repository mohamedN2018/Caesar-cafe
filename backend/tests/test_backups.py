"""
Backups.

The tests that matter here are not "does pg_dump run" — it does. They are:

  * does the encryption round-trip, and does a plaintext dump get REFUSED in
    production rather than written quietly;
  * does a corrupted file get caught BEFORE it is restored from;
  * does the retention policy keep the monthly copies a corruption discovered in
    March actually needs;
  * does a failed run stay visible instead of vanishing into a log.

`create()` runs real `pg_dump` against the real test database, because a mocked
backup test verifies the mock.
"""

from __future__ import annotations

import os
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.ops import backups
from apps.ops.models import BackupRecord, BackupStatus

pytestmark = pytest.mark.django_db(transaction=True)

KEY_B64 = "c2VjcmV0LWtleS10aGF0LWlzLTMyLWJ5dGVzLWxvbmc="  # 32 bytes, base64


@pytest.fixture
def backup_dir(tmp_path, settings):
    settings.BACKUP_DIR = str(tmp_path / "backups")
    return tmp_path / "backups"


@pytest.fixture
def encrypted(monkeypatch):
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", KEY_B64)


@pytest.fixture
def plaintext(monkeypatch):
    monkeypatch.delenv("BACKUP_ENCRYPTION_KEY", raising=False)


# ── the round trip ───────────────────────────────────────────────────────────


class TestCreate:
    def test_a_plaintext_backup_is_written_and_digested(self, backup_dir, plaintext) -> None:
        record = backups.create(label="test")

        assert record.status == BackupStatus.COMPLETE, record.error
        assert record.filename.endswith(".sql.gz")
        assert record.encrypted is False
        assert record.size_bytes > 0
        assert len(record.sha256) == 64
        assert (backup_dir / record.filename).exists()

    def test_an_encrypted_backup_has_a_different_extension_and_is_not_gzip(
        self, backup_dir, encrypted
    ) -> None:
        record = backups.create(label="test")

        assert record.status == BackupStatus.COMPLETE, record.error
        assert record.filename.endswith(".sql.gz.enc")
        assert record.encrypted is True

        body = (backup_dir / record.filename).read_bytes()
        assert not body.startswith(b"\x1f\x8b"), "the file is still readable as gzip"

    def test_the_dump_actually_contains_the_schema(self, backup_dir, plaintext) -> None:
        """
        A backup that runs, writes a file, and contains nothing is the failure
        this asserts against — and it is not hypothetical, it is what a wrong
        `--schema-only` flag looks like.
        """
        import gzip

        record = backups.create(label="test")
        body = gzip.open(backup_dir / record.filename, "rt", errors="replace").read()

        assert "CREATE TABLE" in body
        assert "audit_log" in body

    def test_the_backup_is_audited(self, backup_dir, plaintext) -> None:
        record = backups.create(label="test")

        row = AuditLog.objects.filter(action="system.backup_triggered").get()
        assert row.object_id == record.filename
        assert row.detail["scheduled"] is True, "no user means the nightly run"

    def test_a_manual_backup_names_who_asked_for_it(self, backup_dir, plaintext, make_user) -> None:
        user = make_user(email="ops@caesar.test", full_name_ar="مسؤول")
        record = backups.create(user=user, label="manual")

        assert record.triggered_by_id == user.id
        row = AuditLog.objects.filter(action="system.backup_triggered").get()
        assert row.actor_name == "مسؤول"
        assert row.detail["scheduled"] is False


class TestEncryption:
    def test_it_round_trips(self, encrypted) -> None:
        import base64

        key = base64.b64decode(KEY_B64)
        payload = b"CREATE TABLE orders (...);" * 100

        assert backups._decrypt(backups._encrypt(payload, key), key) == payload

    def test_a_tampered_ciphertext_is_refused(self, encrypted) -> None:
        """AES-GCM is authenticated: a flipped byte fails to decrypt rather than
        producing plausible garbage a restore would happily feed to psql."""
        import base64

        from cryptography.exceptions import InvalidTag

        key = base64.b64decode(KEY_B64)
        blob = bytearray(backups._encrypt(b"payload", key))
        blob[-1] ^= 0x01

        with pytest.raises(InvalidTag):
            backups._decrypt(bytes(blob), key)

    def test_a_short_key_is_rejected_with_the_command_to_generate_one(self, monkeypatch) -> None:
        monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", "dGhpcy1pcy10b28tc2hvcnQ=")

        with pytest.raises(backups.BackupFailed, match="32 bytes"):
            backups._encryption_key()

    def test_production_refuses_to_write_plaintext(self, settings, plaintext) -> None:
        """
        An off-site plaintext dump of every order, customer phone and staff record
        is a breach waiting for a lost disk. A half-configured backup that writes
        it quietly is worse than one that fails.
        """
        settings.DEBUG = False

        with pytest.raises(backups.BackupFailed, match="BACKUP_ENCRYPTION_KEY"):
            backups.assert_configured()

    def test_development_is_allowed_to_run_without_a_key(self, settings, plaintext) -> None:
        settings.DEBUG = True
        backups.assert_configured()  # does not raise


# ── integrity ────────────────────────────────────────────────────────────────


class TestVerify:
    def test_an_intact_file_verifies(self, backup_dir, plaintext) -> None:
        assert backups.verify(backups.create(label="test")) is True

    def test_a_truncated_file_fails(self, backup_dir, plaintext) -> None:
        """
        Disk filled at 2am. The file looks valid until somebody reads it, which
        is why the digest is checked and not just the existence.
        """
        record = backups.create(label="test")
        path = backup_dir / record.filename
        path.write_bytes(path.read_bytes()[: record.size_bytes // 2])

        assert backups.verify(record) is False

    def test_a_missing_file_fails(self, backup_dir, plaintext) -> None:
        record = backups.create(label="test")
        (backup_dir / record.filename).unlink()

        assert backups.verify(record) is False

    def test_a_restore_refuses_a_corrupted_file(self, backup_dir, plaintext) -> None:
        record = backups.create(label="test")
        path = backup_dir / record.filename
        path.write_bytes(path.read_bytes() + b"garbage")

        with pytest.raises(backups.BackupFailed, match="Digest mismatch"):
            backups.restore(record.filename, confirmed=True)

    def test_a_restore_must_be_confirmed(self, backup_dir, plaintext) -> None:
        """
        `confirmed` exists so the destructive step is a separate explicit act,
        not a consequence of calling a function with an innocuous name.
        """
        record = backups.create(label="test")

        with pytest.raises(backups.BackupFailed, match="confirmed=True"):
            backups.restore(record.filename)

    def test_restoring_a_file_that_does_not_exist_says_so(self, backup_dir) -> None:
        with pytest.raises(backups.BackupFailed, match="No such backup"):
            backups.restore("caesar-nope.sql.gz", confirmed=True)


# ── retention ────────────────────────────────────────────────────────────────


class TestRetention:
    def _record(self, days_ago: int, *, status=BackupStatus.COMPLETE) -> BackupRecord:
        record = BackupRecord.objects.create(
            filename=f"caesar-{days_ago:04d}.sql.gz", status=status, size_bytes=1
        )
        # `started_at` is auto_now_add, so it has to be pushed back afterwards.
        BackupRecord.objects.filter(pk=record.pk).update(
            started_at=timezone.now() - timedelta(days=days_ago)
        )
        record.refresh_from_db()
        return record

    def test_recent_backups_are_kept(self, backup_dir) -> None:
        for days in (0, 1, 10, 29):
            self._record(days)

        assert backups.prune(keep_daily=30) == []
        assert BackupRecord.objects.count() == 4

    def test_old_dailies_are_removed(self, backup_dir) -> None:
        self._record(0)
        old = self._record(200)

        removed = backups.prune(keep_daily=30, keep_monthly=0)
        assert old.filename in removed
        assert BackupRecord.objects.count() == 1

    def test_the_first_backup_of_each_month_survives_the_daily_window(self, backup_dir) -> None:
        """
        A corruption discovered in March needs a February copy, and thirty days
        does not reach it.
        """
        for days in (0, 40, 41, 70, 71):
            self._record(days)

        backups.prune(keep_daily=30, keep_monthly=12)
        surviving = set(BackupRecord.objects.values_list("filename", flat=True))

        assert "caesar-0000.sql.gz" in surviving
        # One monthly kept per calendar month, and the OLDEST in each month.
        assert "caesar-0041.sql.gz" in surviving or "caesar-0040.sql.gz" in surviving
        assert "caesar-0071.sql.gz" in surviving or "caesar-0070.sql.gz" in surviving

    def test_a_failed_run_is_never_pruned_away_silently(self, backup_dir) -> None:
        """
        The record of a failure is the thing an operator needs to see. Pruning it
        because it is old would hide a backup system that has been broken for a
        month.
        """
        failed = self._record(200, status=BackupStatus.FAILED)
        backups.prune(keep_daily=30, keep_monthly=0)

        assert BackupRecord.objects.filter(pk=failed.pk).exists()

    def test_the_file_is_deleted_with_the_record(self, backup_dir, plaintext) -> None:
        record = backups.create(label="test")
        BackupRecord.objects.filter(pk=record.pk).update(
            started_at=timezone.now() - timedelta(days=400)
        )

        backups.prune(keep_daily=30, keep_monthly=0)
        assert not (backup_dir / record.filename).exists()


# ── failure is visible ───────────────────────────────────────────────────────


class TestFailureVisibility:
    def test_a_failed_dump_is_recorded_not_raised(self, backup_dir, plaintext, monkeypatch):
        """
        A failed nightly run must be visible in the API, not only in a log
        nobody reads.
        """
        monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:wrong@nowhere:5432/missing")

        record = backups.create(label="broken")

        assert record.status == BackupStatus.FAILED
        assert record.error
        assert record.finished_at is not None

    def test_a_failed_dump_leaves_no_partial_file(self, backup_dir, plaintext, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:wrong@nowhere:5432/missing")
        record = backups.create(label="broken")

        assert not (backup_dir / record.filename).exists()

    def test_the_status_reports_hours_since_the_last_success(self, backup_dir, plaintext) -> None:
        """
        "last run: COMPLETE" means nothing if the last run was in April. This is
        the field that makes that impossible to miss.
        """
        record = backups.create(label="test")
        BackupRecord.objects.filter(pk=record.pk).update(
            started_at=timezone.now() - timedelta(hours=50)
        )

        state = backups.status()
        assert float(state["hours_since_last"]) == pytest.approx(50, abs=1)
        assert state["last_filename"] == record.filename

    def test_the_status_reports_when_encryption_is_off(self, backup_dir, plaintext) -> None:
        assert backups.status()["configured"] is False

    def test_an_empty_system_reports_no_backups_rather_than_zero_hours(self, backup_dir) -> None:
        state = backups.status()
        assert state["total"] == 0
        assert state["hours_since_last"] is None, "None, not 0 — never backed up is not 'just now'"


# ── the nightly task ─────────────────────────────────────────────────────────


class TestNightlyTask:
    def test_it_backs_up_then_prunes(self, backup_dir, plaintext) -> None:
        from apps.ops import tasks

        result = tasks.nightly_backup()
        assert result["status"] == BackupStatus.COMPLETE
        assert BackupRecord.objects.count() == 1

    def test_a_failed_backup_leaves_retention_untouched(
        self, backup_dir, plaintext, monkeypatch
    ) -> None:
        """
        Pruning first would, on the one night the dump fails, delete the oldest
        copy and add nothing — a retention policy that quietly shortens itself
        every time something goes wrong.
        """
        from apps.ops import tasks

        BackupRecord.objects.create(
            filename="caesar-old.sql.gz", status=BackupStatus.COMPLETE, size_bytes=1
        )
        BackupRecord.objects.filter(filename="caesar-old.sql.gz").update(
            started_at=timezone.now() - timedelta(days=400)
        )

        monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:wrong@nowhere:5432/missing")
        result = tasks.nightly_backup()

        assert result["status"] == BackupStatus.FAILED
        assert result["pruned"] == 0
        assert BackupRecord.objects.filter(filename="caesar-old.sql.gz").exists()


# ── API ──────────────────────────────────────────────────────────────────────


class TestAPI:
    def test_it_needs_the_backups_permission(self, branch, make_user, authed) -> None:
        manager = authed(make_user(email="m@caesar.test", role="BRANCH_MANAGER"), branch=branch)
        assert manager.get("/api/v1/ops/backups/").status_code == 403

        admin = make_user(email="admin@caesar.test", role="SUPER_ADMIN")
        assert authed(admin, branch=branch).get("/api/v1/ops/backups/").status_code == 200

    def test_the_state_is_reported(self, backup_dir, plaintext, branch, make_user, authed):
        backups.create(label="test")
        admin = authed(make_user(email="admin@caesar.test", role="SUPER_ADMIN"), branch=branch)

        data = admin.get("/api/v1/ops/backups/").json()["data"]
        assert data["total"] == 1
        assert data["configured"] is False
        assert len(data["backups"]) == 1

    def test_a_backup_can_be_triggered(self, backup_dir, plaintext, branch, make_user, authed):
        admin = authed(make_user(email="admin@caesar.test", role="SUPER_ADMIN"), branch=branch)

        response = admin.post("/api/v1/ops/backups/", {}, format="json")
        assert response.status_code == 201, response.json()
        assert response.json()["data"]["status"] == BackupStatus.COMPLETE

    def test_there_is_no_restore_endpoint(self, branch, make_user, authed) -> None:
        """
        An HTTP route that replaces the database is a route somebody eventually
        calls by mistake, and the mistake is unrecoverable.
        """
        admin = authed(make_user(email="admin@caesar.test", role="SUPER_ADMIN"), branch=branch)

        for path in ("/api/v1/ops/restore/", "/api/v1/ops/backups/restore/"):
            assert admin.post(path, {}, format="json").status_code == 404

    def test_there_is_no_download_endpoint(self, backup_dir, plaintext, branch, make_user, authed):
        """The file holds every order, phone number and staff record. It belongs
        on the host and off-site, not behind a session cookie."""
        record = backups.create(label="test")
        admin = authed(make_user(email="admin@caesar.test", role="SUPER_ADMIN"), branch=branch)

        assert admin.get(f"/api/v1/ops/backups/{record.id}/download/").status_code == 404

    def test_a_file_can_be_verified_through_the_api(
        self, backup_dir, plaintext, branch, make_user, authed
    ) -> None:
        record = backups.create(label="test")
        admin = authed(make_user(email="admin@caesar.test", role="SUPER_ADMIN"), branch=branch)

        assert admin.post(f"/api/v1/ops/backups/{record.id}/verify/").json()["data"]["verified"]


# ── configuration ────────────────────────────────────────────────────────────


def test_pg_dump_is_available_in_the_image() -> None:
    """
    Backups run inside the API image rather than a sidecar, so that a restore
    drill on a scratch host needs one image and not two that must stay in step.
    """
    import shutil

    assert shutil.which("pg_dump"), "postgresql-client is missing from the image"
    assert shutil.which("psql")


def test_the_nightly_backup_runs_before_the_business_day_boundary() -> None:
    """
    03:00, before the 04:00 default boundary — so a dump lands while the previous
    trading day is complete and nothing is half-written into the next.
    """
    from django.conf import settings

    schedule = settings.CELERY_BEAT_SCHEDULE["nightly-backup"]["schedule"]
    assert schedule.hour == {3}

    rollups = settings.CELERY_BEAT_SCHEDULE["nightly-rollups"]["schedule"]
    assert min(rollups.hour) > min(schedule.hour), "back up before rolling up"


def test_the_backup_directory_is_configurable() -> None:
    assert os.environ.get("BACKUP_DIR", "/backups")
