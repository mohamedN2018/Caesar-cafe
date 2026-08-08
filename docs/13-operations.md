# 13 — Operations Runbook

Written in Phase 9 alongside the audit trail, because a control nobody has
rehearsed is a control nobody has. Every procedure here is meant to be followed
by someone who did not write the system, at 2am, under pressure.

---

## Secret Rotation

Six secrets exist. They rotate on different schedules and with different
consequences, so they are listed separately rather than as "rotate the secrets".

| Secret | Env var | Blast radius on rotation | Cadence |
|---|---|---|---|
| Django secret key | `DJANGO_SECRET_KEY` | Signed cookies and step-up approval tokens invalidate. Nobody is logged out (JWTs are signed separately); in-flight approvals fail and are retried by a human. | Annually, or immediately on suspicion |
| JWT signing key | `JWT_SIGNING_KEY` | **Every session ends.** Every terminal and browser re-authenticates. Devices recover on their own (they hold a credential); humans must log in again. | Annually, or immediately on suspicion |
| License pepper | `LICENSE_PEPPER` | **Catastrophic and irreversible.** Every stored key hash becomes unverifiable, so every existing licence key stops working and every customer needs a reissued key. Rotate only if the pepper itself leaked, and plan a reissue campaign first. | Never, by preference |
| Ed25519 licence signing key | `LICENSE_SIGNING_KEY` | Offline tokens already on devices stop verifying once clients ship the new public key. Devices must be online once during the changeover. | Only on compromise |
| Database password | `DATABASE_URL` | Restart required. No data effect. | Quarterly |
| Sentry DSN | `SENTRY_DSN` | None. | On project change |

### Procedure

1. Generate the new value **outside** the repository:
   `python -c "import secrets; print(secrets.token_urlsafe(64))"`
2. Put it in the deployment host's `.env`. Never in Git — `.env` is ignored and
   CI scans for secrets (docs/09, I3).
3. `docker compose -f docker-compose.prod.yml up -d --force-recreate api worker beat`
4. Confirm the app booted: `GET /api/v1/system/health/` returns 200. Production
   settings refuse to start on a placeholder secret, so a failed boot here means
   the value did not reach the container — check for shell quoting, not for a
   subtle bug.
5. Record it: the rotation itself is not audited (the app cannot see its own env),
   so write the date and the reason in the ops log. This is the one control in
   the system that depends on a human writing something down.

**Rotating the JWT key mid-service ends every session.** Do it at 05:00, after
the business day boundary and before the cafe opens.

---

## First Deployment

One host, one clone, one `.env`. The whole point of the exit criterion — *a
fresh-host deploy from a clean clone succeeds by following the runbook alone* —
is that these steps are complete. If you had to improvise, the runbook has a bug.

```bash
# 1. Prerequisites: Docker Engine 24+, a domain whose A record points here,
#    ports 80 and 443 reachable. Nothing else.
git clone <repo> caesar && cd caesar

# 2. Secrets. Never commit the result.
cp .env.example .env
python -c "import secrets; print('DJANGO_SECRET_KEY=' + secrets.token_urlsafe(64))"
python -c "import secrets; print('JWT_SIGNING_KEY=' + secrets.token_urlsafe(64))"
python -c "import secrets; print('LICENSE_PEPPER=' + secrets.token_urlsafe(64))"
python -c "import os,base64; print('BACKUP_ENCRYPTION_KEY=' + base64.b64encode(os.urandom(32)).decode())"
# …and DOMAIN, ACME_EMAIL, POSTGRES_PASSWORD, DATABASE_URL,
#    DJANGO_SETTINGS_MODULE=config.settings.prod, DJANGO_DEBUG=False
```

**Copy `LICENSE_PEPPER` and `BACKUP_ENCRYPTION_KEY` somewhere outside this host
before continuing.** The pepper cannot be rotated — every issued licence key
becomes unverifiable. The backup key cannot be recovered — every off-site backup
becomes unreadable. A host loss that takes both with it is unrecoverable in a way
nothing else here is.

```bash
# 3. The Ed25519 licence signing key
docker compose -f docker-compose.prod.yml run --rm api python manage.py generate_signing_key
#    → paste the output into .env as LICENSE_SIGNING_KEY

# 4. Build the SPA. Caddy serves the files directly; there is no Node process in
#    production, because a process that only hands out static files is a process
#    that can crash for no reason.
docker run --rm -v "$PWD/frontend:/app" -w /app -e VITE_API_BASE_URL=/api/v1 \
  node:22-alpine sh -c "npm ci && npm run build"

# 5. Up. `api` runs migrate and collectstatic on start.
docker compose -f docker-compose.prod.yml up -d

# 6. The first administrator
docker compose -f docker-compose.prod.yml exec api python manage.py createsuperuser
```

### Verify, in this order

Not the health endpoint alone — that returns 200 on a stack with an empty
database and no certificate.

1. `curl -I https://$DOMAIN/api/v1/system/health/` → **200 over HTTPS**, with a
   real certificate. Caddy obtains it on first boot; if this is a self-signed
   warning, DNS is not pointing here yet.
2. Log in to `https://$DOMAIN` as the superuser.
3. `docker compose -f docker-compose.prod.yml exec api python manage.py backup_database`
   → completes and says **(encrypted)**. If it says NOT ENCRYPTED, stop and fix
   `BACKUP_ENCRYPTION_KEY`; production settings will refuse the scheduled run.
4. Apply the audit grant — the one step that appears to work when skipped:
   ```bash
   docker compose -f docker-compose.prod.yml exec postgres \
     psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
     -c 'REVOKE DELETE, TRUNCATE, UPDATE ON audit_log FROM '"$POSTGRES_USER"';'
   ```
5. Issue a licence and activate one Desktop terminal end to end. The system is
   not deployed until a terminal has actually opened.

---

## Deploying a Change

```bash
git pull
docker compose -f docker-compose.prod.yml exec api python manage.py backup_database --label pre-deploy
docker compose -f docker-compose.prod.yml build api
docker compose -f docker-compose.prod.yml up -d api worker beat
```

The backup before the build is not ceremony. A migration that fails halfway is
the one deployment failure that a rollback of the images does not fix.

### Migration checklist

Before applying anything to production:

- [ ] `python manage.py makemigrations --check --dry-run` is clean on the branch.
- [ ] The migration is **reversible**, or the fact that it is not is written in
      its docstring. An irreversible migration is allowed; an undocumented one is
      not.
- [ ] It does not lock a large table for long. `ALTER TABLE … ADD COLUMN` with a
      default rewrites the table in older Postgres; add nullable, backfill in
      batches, then constrain.
- [ ] A pre-deploy backup exists and `--verify` passes on it.
- [ ] The rollback path is known **before** starting. For a column addition it is
      "leave it"; for a data migration it is the restore below.

### Rollback

Two different failures with two different answers:

**The code is wrong, the database is fine** — the common case. Roll the image
back; migrations that only added columns are harmless to leave in place:

```bash
git checkout <previous-tag>
docker compose -f docker-compose.prod.yml build api
docker compose -f docker-compose.prod.yml up -d api worker beat
```

**The migration corrupted or destroyed data** — restore, and accept the data loss
between the backup and now:

```bash
docker compose -f docker-compose.prod.yml stop api worker beat
docker compose -f docker-compose.prod.yml run --rm api \
  python manage.py restore_database <pre-deploy-file> --i-understand-this-destroys-data
docker compose -f docker-compose.prod.yml up -d
```

Terminals that were offline during this keep their outbox and push when they
reconnect, so orders taken during the outage are not lost — that is the whole
point of C1 and Phase 7. Orders taken *online* between the backup and the restore
are gone. Say so, out loud, before running it.

---

## Backup

Implemented in `apps/ops`. Nightly at 03:00 — before the 04:00 business-day
boundary, so a dump lands while the previous trading day is complete.

| Property | Actual |
|---|---|
| Frequency | Nightly full `pg_dump`, gzipped, AES-256-GCM encrypted |
| Retention | 30 daily + the first backup of each of the last 12 months |
| Encryption | Mandatory in production — the app **refuses to run** without a key |
| Integrity | SHA-256 recorded on write, re-checked nightly at 04:00 and before any restore |
| RPO | **≤ 24h.** No WAL archiving — see the gap below |
| RTO | ~15 min for the restore itself, plus the verification below |

```bash
# manual
python manage.py backup_database --label before-something-risky
python manage.py backup_database --list
python manage.py backup_database --verify
```

Or `POST /api/v1/ops/backups/` with `backups.manage`. There is deliberately **no
download endpoint and no restore endpoint**: the file holds every order, phone
number and staff record, and a route that replaces the database is a route
somebody eventually calls by mistake.

### The remaining gap, stated plainly

**There is no WAL archiving, so the RPO is up to 24 hours, not the 5 minutes
docs/09 targets.** A host loss at 22:00 costs the whole trading day. Closing it
is a Postgres configuration task — `archive_mode = on` shipping WAL segments to
the same off-site bucket — and it is not done. Until it is, that is the number to
give the customer.

### Off-site copy

The backups volume is on the same host as the database, which means it survives
a container failure and not a host failure. Ship it somewhere else:

```bash
# In root's crontab, 04:30 — after the nightly backup and its verification.
30 4 * * * docker run --rm -v caesar_backups:/b:ro -v /root/.aws:/root/.aws:ro \
  amazon/aws-cli s3 sync /b s3://caesar-backups/ --storage-class STANDARD_IA
```

The files are already encrypted, so the bucket does not need to be trusted — only
the key, which lives in `.env` and in whatever you copied it to in step 2.

---

## The Restore Drill

A backup that has never been restored is a hypothesis. The drill is the
experiment, and it is scheduled — quarterly — rather than run when it is needed.

```bash
# 1. A clean host. Not the production one. The point is to discover what the
#    production host has that the runbook forgot to mention.
git clone <repo> && cd caesar-cafe
cp .env.example .env          # then fill in real secrets from the vault

# 2. Restore into a fresh volume
docker compose -f docker-compose.prod.yml up -d postgres
gunzip -c caesar-2026-08-08.sql.gz | \
  docker compose -f docker-compose.prod.yml exec -T postgres \
  psql -U caesar -d caesar

# 3. Bring the app up and check the numbers, not the health endpoint
docker compose -f docker-compose.prod.yml up -d
```

**What to verify, in this order.** Anything that passes step 1 and fails step 4
is a restore that looks fine and is not.

1. `GET /system/health/` → 200.
2. Log in as a real admin account. Password hashes must have survived.
3. `GET /reports/sales/summary/?date_from=…&date_to=…` for a known past week,
   and compare against a figure written down BEFORE the drill. A restore that
   loses rows still answers this endpoint — with a smaller number.
4. `GET /audit/?action=order.voided` returns rows. The audit table is the one a
   partial restore is most likely to drop, because it is the largest and the
   least referenced by foreign keys.
5. Rebuild one day's rollups (`POST /reports/rollups/rebuild/`) and confirm the
   figure is unchanged. This proves the transactional tables came back, not just
   the caches derived from them.

**Record the measured RPO and RTO each time**, with the date and who ran it. A
target nobody has measured is a wish.

---

## The Audit Trail

### What it holds

Every action in [09](09-security.md#audited-actions), catalogued in
`apps/audit/actions.py`. `tests/test_audit.py::TestCatalogueCoverage` asserts
that every catalogued action is produced by real code — so the table in the
security document cannot quietly drift from what the system does.

### Append-only, at three levels

1. `AuditLog.delete()` and `AuditLog.objects.all().delete()` raise, as does
   `save()` on an existing row.
2. There is no write endpoint. `/audit/` serves GET only.
3. **The production DB role must lack `DELETE` on `audit_log`.** Levels 1 and 2
   are application guards and do not survive `manage.py shell`; the grant does.

```sql
-- Run once, as the DB owner, after the first migrate.
REVOKE DELETE, TRUNCATE ON audit_log FROM caesar_app;
REVOKE UPDATE ON audit_log FROM caesar_app;
```

This is the step most likely to be skipped, because everything appears to work
without it. It is also the only one of the three that stops an insider with
application credentials.

### Reading it during an incident

```
GET /audit/?action=order.voided&since=2026-08-01
GET /audit/?actor=<user_id>            # everything one person did
GET /audit/?object_id=<order_id>       # everything that happened to one order
GET /audit/?severity=WARNING           # the loss-prevention and security subset
```

`request_id` on every row ties it back to the log lines for that request. When a
cashier reports a problem, that one string retrieves the whole story.

### Retention

No pruning is implemented, deliberately. At this volume the table grows by a few
MB a month, and the D4 storage-exhaustion risk is smaller than the risk of a
retention job deleting the row somebody needed. When it does become necessary,
prune by exporting to cold storage first — never by `DELETE`, which the grant
above forbids anyway.

---

## Incident Checklist

**A terminal has stopped syncing.** `/sync/status/` shows the last-seen time and
the pending count per device. A device unseen for more than
`sync.offline_alert_minutes` during business hours is the alert; the sales are on
that machine's disk and arrive when it reconnects. Do not reinstall the client —
that discards the outbox.

**A total looks wrong.** `/audit/?object_id=<order_id>` gives the void, discount
and refund history; the order's event stream gives the rest. Between them, "why
is this bill 204.29?" is answerable by reading rather than inferring.

**Someone is being locked out repeatedly.** `/audit/?domain=auth` shows the
failures and the lockouts with their IPs. A run of `auth.login_failed` across
several accounts from one IP is credential stuffing; the same account from one IP
is usually somebody who changed their password on their phone.

**A licence stopped working.** `/audit/?domain=licensing` for the licence's
history, and `/licensing/…/events/` for the full trail including the heartbeat
denials the audit log deliberately does not mirror.

---

**Back to:** [Index](README.md)
