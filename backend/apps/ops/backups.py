"""
Database backups.

`pg_dump` piped through gzip, then encrypted at rest with AES-GCM. Three
decisions worth stating, because each is a place backup implementations usually
go wrong:

  * **Encryption is mandatory when a key is configured and refused when it is
    not.** A half-configured backup that silently writes plaintext is worse than
    one that fails: the operator believes the off-site copy is safe.

  * **The digest is computed over the file as written**, and a restore verifies
    it before touching the database. A truncated dump is the classic failure —
    disk filled at 2am — and it looks like a valid file until you read it.

  * **Restore is a management command, never an endpoint.** An HTTP route that
    replaces the database is a route somebody eventually calls by mistake, and
    the mistake is unrecoverable. The command demands an explicit flag.

Scope, stated honestly: this is a nightly full dump, sized for a cafe. There is
no WAL archiving here, so the RPO is "since the last run" — up to 24 hours. The
docs/09 target of 5 minutes needs continuous archiving, which is a Postgres
configuration task rather than an application one.
"""

from __future__ import annotations

import gzip
import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.utils import timezone

from apps.audit import services as audit
from apps.core.exceptions import AppError

from .models import BackupRecord, BackupStatus

logger = logging.getLogger(__name__)

#: Refuse to hold more than this in memory while encrypting. At cafe volume a
#: dump is tens of MB; a database that has outgrown this needs streaming
#: encryption and an operator who has read the runbook, not a silent OOM.
MAX_IN_MEMORY_BYTES = 512 * 1024 * 1024

SUFFIX_PLAIN = ".sql.gz"
SUFFIX_ENCRYPTED = ".sql.gz.enc"


class BackupFailed(AppError):
    code = "BACKUP_FAILED"


@dataclass(frozen=True)
class DatabaseTarget:
    name: str
    user: str
    password: str
    host: str
    port: str


def _target() -> DatabaseTarget:
    """Parse the connection out of DATABASE_URL rather than duplicating it."""
    url = urlparse(os.environ.get("DATABASE_URL", ""))
    if not url.path:
        config = settings.DATABASES["default"]
        return DatabaseTarget(
            name=config["NAME"],
            user=config.get("USER", ""),
            password=config.get("PASSWORD", ""),
            host=config.get("HOST", "localhost"),
            port=str(config.get("PORT") or "5432"),
        )
    return DatabaseTarget(
        name=url.path.lstrip("/"),
        user=url.username or "",
        password=url.password or "",
        host=url.hostname or "localhost",
        port=str(url.port or 5432),
    )


def backup_dir() -> Path:
    path = Path(getattr(settings, "BACKUP_DIR", "/backups"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _encryption_key() -> bytes | None:
    """
    The 32-byte key from `BACKUP_ENCRYPTION_KEY`, base64 or hex.

    Returns None when unset — which is allowed in development and refused in
    production by `assert_configured`.
    """
    raw = os.environ.get("BACKUP_ENCRYPTION_KEY", "").strip()
    if not raw:
        return None

    import base64
    import binascii

    for decode in (base64.b64decode, binascii.unhexlify):
        # Neither failure is logged, deliberately: the value being decoded IS the
        # secret, and "base64 decode failed on <value>" is exactly the log line
        # the redaction filter exists to prevent.
        try:
            key = decode(raw)
        except Exception:  # noqa: S112 — try the next encoding
            continue
        if len(key) == 32:
            return key

    raise BackupFailed(
        "BACKUP_ENCRYPTION_KEY must decode to 32 bytes. Generate one with: "
        'python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"',
        code="BACKUP_KEY_INVALID",
    )


def assert_configured() -> None:
    """
    Fail loudly in production if backups would write plaintext.

    Called at startup by the health check rather than at 2am by the scheduler:
    the operator should learn about this while deploying, not from an incident.
    """
    if settings.DEBUG:
        return
    if _encryption_key() is None:
        raise BackupFailed(
            "BACKUP_ENCRYPTION_KEY is not set. Refusing to write unencrypted "
            "backups in production — an off-site plaintext dump of every order, "
            "customer phone and staff record is a breach waiting for a lost disk.",
            code="BACKUP_NOT_ENCRYPTED",
        )


def _encrypt(data: bytes, key: bytes) -> bytes:
    """AES-256-GCM. The 12-byte nonce is prefixed to the ciphertext."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, data, None)


def _decrypt(blob: bytes, key: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce, ciphertext = blob[:12], blob[12:]
    return AESGCM(key).decrypt(nonce, ciphertext, None)


def create(*, user=None, label: str = "") -> BackupRecord:
    """
    Take one full backup. Returns the record, whose `status` is the outcome.

    Never raises on a dump failure — the record carries the error so a failed
    nightly run is visible in the API instead of only in a log nobody reads.
    """
    target = _target()
    key = _encryption_key()
    stamp = timezone.now().strftime("%Y%m%dT%H%M%SZ")
    slug = f"caesar-{label or target.name}-{stamp}"
    filename = slug + (SUFFIX_ENCRYPTED if key else SUFFIX_PLAIN)

    record = BackupRecord.objects.create(
        filename=filename, encrypted=key is not None, triggered_by=user
    )
    audit.record(
        "system.backup_triggered",
        actor=user,
        object_type="backup",
        object_id=filename,
        object_label=filename,
        detail={"encrypted": key is not None, "scheduled": user is None},
    )

    started = timezone.now()
    destination = backup_dir() / filename

    try:
        with tempfile.TemporaryDirectory() as workspace:
            dump = Path(workspace) / "dump.sql.gz"
            _pg_dump(target, dump)

            if key is None:
                shutil.move(str(dump), destination)
            else:
                size = dump.stat().st_size
                if size > MAX_IN_MEMORY_BYTES:
                    raise BackupFailed(
                        f"Dump is {size // 1_048_576} MB, over the "
                        f"{MAX_IN_MEMORY_BYTES // 1_048_576} MB in-memory encryption "
                        "limit. See docs/13 for the streaming procedure.",
                        code="BACKUP_TOO_LARGE",
                    )
                destination.write_bytes(_encrypt(dump.read_bytes(), key))

        record.size_bytes = destination.stat().st_size
        record.sha256 = _digest(destination)
        record.status = BackupStatus.COMPLETE

    except Exception as exc:
        record.status = BackupStatus.FAILED
        record.error = str(exc)[:2000]
        logger.exception("Backup failed", extra={"backup_file": filename})
        destination.unlink(missing_ok=True)

    record.finished_at = timezone.now()
    record.duration_seconds = int((record.finished_at - started).total_seconds())
    record.save()

    if record.status == BackupStatus.COMPLETE:
        logger.info(
            "Backup complete",
            extra={
                # NOT "filename": that is a reserved LogRecord attribute and
                # logging raises KeyError rather than shadowing it.
                "backup_file": filename,
                "size_mb": record.size_mb,
                "seconds": record.duration_seconds,
            },
        )
    return record


def _pg_dump(target: DatabaseTarget, destination: Path) -> None:
    """
    `pg_dump | gzip`, streamed — the dump never exists uncompressed on disk.

    `--no-owner` and `--no-privileges` are what make the dump restorable into a
    differently-named role, which is exactly the situation during a restore drill
    on a scratch host.
    """
    command = [
        "pg_dump",
        "--host",
        target.host,
        "--port",
        target.port,
        "--username",
        target.user,
        "--no-owner",
        "--no-privileges",
        "--format",
        "plain",
        target.name,
    ]
    environment = {**os.environ, "PGPASSWORD": target.password}

    with gzip.open(destination, "wb") as archive:
        process = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment
        )
        assert process.stdout is not None
        shutil.copyfileobj(process.stdout, archive)
        _, stderr = process.communicate()

    if process.returncode != 0:
        destination.unlink(missing_ok=True)
        raise BackupFailed(
            f"pg_dump exited {process.returncode}: {stderr.decode(errors='replace')[:500]}"
        )


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def verify(record: BackupRecord) -> bool:
    """
    Does the file still match what was written?

    A truncated dump — disk filled at 2am — looks like a valid file until you
    read it. This is the cheap check; the real one is the restore drill.
    """
    path = backup_dir() / record.filename
    if not path.exists():
        return False
    return bool(record.sha256) and _digest(path) == record.sha256


def prune(*, keep_daily: int = 30, keep_monthly: int = 12) -> list[str]:
    """
    Enforce the docs/09 retention policy: 30 daily, 12 monthly.

    Monthly is the FIRST successful backup of each calendar month, kept even when
    it falls outside the daily window — a corruption discovered in March needs a
    February copy, and thirty days does not reach it.
    """
    completed = list(
        BackupRecord.objects.filter(status=BackupStatus.COMPLETE).order_by("-started_at")
    )
    cutoff = timezone.now() - timedelta(days=keep_daily)

    keep: set[int] = {r.id for r in completed if r.started_at >= cutoff}

    monthly: dict[tuple[int, int], BackupRecord] = {}
    for record in sorted(completed, key=lambda r: r.started_at):
        monthly.setdefault((record.started_at.year, record.started_at.month), record)
    for record in sorted(monthly.values(), key=lambda r: r.started_at, reverse=True)[:keep_monthly]:
        keep.add(record.id)

    removed = []
    for record in completed:
        if record.id in keep:
            continue
        (backup_dir() / record.filename).unlink(missing_ok=True)
        removed.append(record.filename)
        record.delete()

    if removed:
        logger.info("Pruned backups", extra={"count": len(removed)})
    return removed


def restore(filename: str, *, user=None, confirmed: bool = False) -> None:
    """
    Replace the database from a backup file. **Destroys everything currently in it.**

    Deliberately has no HTTP caller. `confirmed` exists so the destructive step
    is a separate, explicit act rather than a consequence of calling a function
    with an innocuous name.
    """
    if not confirmed:
        raise BackupFailed(
            "restore() must be called with confirmed=True. This replaces the "
            "entire database and cannot be undone.",
            code="RESTORE_NOT_CONFIRMED",
        )

    path = backup_dir() / filename
    if not path.exists():
        raise BackupFailed(f"No such backup: {filename}", code="BACKUP_NOT_FOUND")

    record = BackupRecord.objects.filter(filename=filename).first()
    if record is not None and record.sha256 and _digest(path) != record.sha256:
        raise BackupFailed(
            "Digest mismatch — the file has changed since it was written. "
            "Refusing to restore from a backup that may be truncated.",
            code="BACKUP_CORRUPT",
        )

    audit.record(
        "system.restore_performed",
        actor=user,
        object_type="backup",
        object_id=filename,
        object_label=filename,
        detail={"verified_digest": bool(record and record.sha256)},
    )

    target = _target()
    key = _encryption_key()

    with tempfile.TemporaryDirectory() as workspace:
        archive = Path(workspace) / "restore.sql.gz"
        if filename.endswith(SUFFIX_ENCRYPTED):
            if key is None:
                raise BackupFailed(
                    "This backup is encrypted and BACKUP_ENCRYPTION_KEY is not set.",
                    code="BACKUP_KEY_MISSING",
                )
            archive.write_bytes(_decrypt(path.read_bytes(), key))
        else:
            shutil.copyfile(path, archive)

        command = [
            "psql",
            "--host",
            target.host,
            "--port",
            target.port,
            "--username",
            target.user,
            "--dbname",
            target.name,
            "--set",
            "ON_ERROR_STOP=on",
        ]
        environment = {**os.environ, "PGPASSWORD": target.password}

        with gzip.open(archive, "rb") as source:
            process = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
                command, stdin=subprocess.PIPE, stderr=subprocess.PIPE, env=environment
            )
            assert process.stdin is not None
            try:
                shutil.copyfileobj(source, process.stdin)
                process.stdin.close()
            except BrokenPipeError:
                # psql died before reading the dump — a wrong host, a refused
                # connection. Its stderr says why; a BrokenPipeError does not,
                # and it is what an operator would otherwise be handed at 2am.
                pass
            _, stderr = process.communicate()

    if process.returncode != 0:
        raise BackupFailed(
            f"psql exited {process.returncode}: {stderr.decode(errors='replace')[:1000]}"
        )

    logger.warning("Database restored", extra={"backup_file": filename})


def status() -> dict:
    """
    What the operator and the health check look at.

    `hours_since_last` is the number that matters: a backup system reporting
    "last run: COMPLETE" while the last run was in April is the failure mode this
    field exists to make impossible to miss.
    """
    last = BackupRecord.objects.filter(status=BackupStatus.COMPLETE).first()
    failures = BackupRecord.objects.filter(status=BackupStatus.FAILED).count()

    return {
        "configured": _encryption_key() is not None,
        "directory": str(backup_dir()),
        "total": BackupRecord.objects.count(),
        "failed": failures,
        "last_success": last.started_at.isoformat() if last else None,
        "last_filename": last.filename if last else None,
        "last_size_mb": str(last.size_mb) if last else None,
        "hours_since_last": (
            str(round(Decimal((timezone.now() - last.started_at).total_seconds()) / 3600, 1))
            if last
            else None
        ),
    }
