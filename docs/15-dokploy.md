# 15 — Deploying on Dokploy

For `caesar.deplois.net`. The bare-host procedure is [13 — Operations](13-operations.md); this is the
same stack with Dokploy's Traefik in front of it instead of our own Caddy holding the certificate.

---

## What is different from `docker-compose.prod.yml`

Three things, and each one is a decision rather than a port:

**Dokploy's Traefik terminates TLS.** `deploy/Caddyfile.dokploy` listens on `:80` with `auto_https
off`. Two processes trying to obtain a certificate for one hostname is how you get an ACME rate-limit
and an outage. Caddy still does everything else — the security headers, the SPA fallback, the cache
rules, the `/api/*` proxy — because a deployment that quietly relaxed its headers by going through a
different proxy would be the worst kind of difference: invisible until somebody audits it.

**The SPA is built inside its image.** `deploy/Dockerfile.web` runs `npm ci && npm run build` in a Node
stage and copies `dist` into a Caddy stage that carries no Node at all. The bare-host runbook builds
the bundle with a one-off `docker run node` and mounts it; Dokploy clones the repo and brings the
compose up, so there is no host step to do that in. `npm run build` is `vue-tsc --noEmit && vite
build`, so a type error fails the image rather than shipping a bundle nobody type-checked.

**`sync_roles` runs on every start.** Added to the api command beside `migrate` and `collectstatic`.
A release that introduces a permission code otherwise lands with the code in the catalogue, the routes
enforcing it, and no role holding it — see the entry in [08](08-roadmap.md).

Unchanged: Postgres and Redis publish no ports and sit on an `internal: true` network, the statement
timeouts, the log rotation, the memory limits, the unprivileged container user.

---

## First deploy

1. **DNS.** Point `caesar.deplois.net` at the Dokploy host — an `A` record to its IPv4. Do this first:
   Traefik cannot obtain a certificate for a name that does not resolve to it, and the failure looks
   like a browser TLS warning rather than like a DNS problem.

2. **In Dokploy** → *Create Application* → *Docker Compose*.
   - Repository: this repo, branch `main`
   - Compose file: `docker-compose.dokploy.yml`
   - Domain: `caesar.deplois.net`, HTTPS on, certificate provider Let's Encrypt

3. **Environment.** Paste `.env.dokploy.example` into Dokploy's Environment panel and replace every
   `CHANGE-ME`. Generate each secret with `openssl rand -base64 36`.

   `LICENSE_SIGNING_KEY` is the exception — it is an Ed25519 key and has to come from the app:

   ```bash
   # after the first deploy, from Dokploy's terminal on the api container
   python manage.py generate_signing_key
   ```

   Paste the output into the environment and redeploy. Until it is set, licence activation fails and
   no terminal can be enrolled.

4. **Deploy.** Dokploy builds both images and starts the stack. The api container runs migrations,
   collects static files and syncs roles before gunicorn binds.

5. **The administrator.**

   ```bash
   python manage.py demo_admin
   ```

   Prints the credentials and a warning. See *The demo login* below — read it before leaving the host
   running.

6. **Data to look at.**

   ```bash
   python manage.py seed_demo --days 14
   ```

   A fortnight of trading through the real services: ~2,500 orders, a seated room, kitchen tickets at
   every state, children mid-visit, closed shifts with cash variances, ten staff with PINs, and a
   licence key printed at the end so a till can be activated. It refuses to run against a database
   that already holds orders unless forced, because demo trading mixed into a real ledger cannot be
   separated afterwards.

---

## Verify, in this order

Not the health endpoint alone — that returns 200 on a stack with an empty database.

1. `curl -I https://caesar.deplois.net/api/v1/system/health/` → **200 over HTTPS with a real
   certificate.** A self-signed warning means DNS is not pointing at this host yet.
2. `curl -I http://caesar.deplois.net` → **301 to https**. A POS reachable over plain HTTP is a POS
   whose session token can be read off the wire.
3. Load `https://caesar.deplois.net` and sign in. The dashboard should show a fortnight of takings,
   not zeros.
4. Open `/pos`, activate the till with the licence key `seed_demo` printed, and sign in with PIN
   `3333`. **Open a shift first** — the server refuses a sale without one, and the till now says so.
5. Check a response header: `curl -sI https://caesar.deplois.net | grep -i strict-transport` →
   HSTS present. If it is missing, Caddy is not in the request path and Traefik is reaching the api
   directly.
6. `python manage.py backup_database` → completes and says **(encrypted)**. If it says NOT ENCRYPTED,
   `BACKUP_ENCRYPTION_KEY` is unset and production will refuse the scheduled run.

---

## The demo login

`demo_admin` creates:

```
email     admin@caesar.deplois.net
password  admin
```

**The identifier is an email, not the bare word `admin`.** `accounts.User` uses email as its username
field, and making a one-word login work would mean changing the authentication path of an
internet-facing system. That is a much larger and much worse change than typing a longer string once,
so it was not made.

**This password is guessable and this host is on the public internet.** Anyone who finds the domain
owns the cafe's money, its staff records and its audit trail. That is an acceptable state while
somebody is looking at a demo and an unacceptable one the moment it is left running unattended.

Close it with one command:

```bash
python manage.py demo_admin --rotate
```

It replaces the password with a strong random one and prints it once. Do this before the demo stops
being a demo.

Two further things worth doing on a host that will live:

- **Turn MFA back on.** `seed_demo` relaxes `security.require_mfa_for_roles` for the demo
  organisation through the settings registry. Set it back to `["SUPER_ADMIN", "BRANCH_MANAGER"]` on
  the settings screen — those accounts are reachable from the internet, which is the whole reason
  C11 required it.
- **Apply the audit grant.** `REVOKE DELETE ON audit_log` — the one step that appears to work when
  skipped. The procedure is in [13](13-operations.md).

---

## Deploying a change

Dokploy redeploys on push, or on the button. The api container re-runs `migrate`, `collectstatic` and
`sync_roles` on every start, so there is no separate step for any of them.

**Take a backup before a deploy that carries a migration.** A migration that fails halfway is the one
deployment failure that rolling the images back does not fix:

```bash
python manage.py backup_database --label pre-deploy
```

The migration checklist and the rollback procedure are unchanged — [13](13-operations.md).

---

## What this deployment does not have

Stated rather than assumed, because a gap somebody discovers during an incident is worse than one
they were told about:

- **No WAL archiving.** The RPO is the nightly backup, so up to 24 hours — not the five minutes
  docs/09 targets. In the words to use with the customer: *a host loss at 22:00 costs the whole
  trading day.*
- **No off-site copy by default.** Backups are encrypted and they are on the same host as the
  database. A host loss takes both. The off-site step is in [13](13-operations.md).
- **The restore drill has not been rehearsed here.** A backup nobody has restored is a hypothesis.

---

**Next:** [13 — Operations Runbook](13-operations.md)
