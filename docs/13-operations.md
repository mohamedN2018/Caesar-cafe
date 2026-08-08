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

## Backup

**Not yet implemented.** Nightly `pg_dump` plus WAL archiving, the retention
policy, and the `system.backup_triggered` / `system.restore_performed` audit
actions all arrive in Phase 10. They are named here so the gap is visible rather
than assumed covered.

Targets from [09](09-security.md#backup--recovery), for whoever builds it:

| Property | Target |
|---|---|
| Frequency | Nightly full `pg_dump` + continuous WAL archiving |
| Retention | 30 daily, 12 monthly |
| Encryption | At rest, off-site |
| RPO | ≤ 24h from the dump, ≤ 5 min with WAL |
| RTO | ≤ 2h on a clean host |

Until it exists, the honest statement of risk is: **a host loss costs everything
since the last manual dump.** That is an accepted risk only for the days between
now and Phase 10, and it should be stated to the customer in those words.

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
