# 15 — Deploying on Dokploy

`caesar.deplois.net`. Read this once before the first deploy; it is short.

## One compose file

`docker-compose.yml` — and that is the only one. There were three (dev, prod, dokploy) and they
drifted, which is what parallel copies do: a header tightened in one, a flag added to another, and
the thing you tested is not the thing you deployed.

**The difference between your laptop and the server is `.env`, not YAML.** One variable does it:

| `DJANGO_ENV` | build stage | settings | server |
|---|---|---|---|
| `prod` | `prod` — no dev extras, unprivileged user | `config.settings.prod` | gunicorn |
| `dev` | `dev` — dev extras, root | `config.settings.dev` | `runserver`, autoreload |

The Dockerfile's stages are named `dev` and `prod` on purpose, so one variable selects the image, the
settings module and the process. Three variables that have to agree is three chances to disagree.

## What does not differ between local and production

This matters more than the parts that do. A local stack shaped differently from production is not a
rehearsal.

- **Postgres and Redis publish no ports, in either mode.** They are on an `internal: true` network
  the host cannot reach. Use `make shell-db`, not a client on localhost — a stack that works locally
  only because a port is exposed fails in production, long after you stopped looking for that.
- **The app publishes no host port at all.** This took two failed deploys to get right, and the
  lesson is worth the space.

  The first attempt used `127.0.0.1:8080:80`. It died on
  `failed to bind port 127.0.0.1:8080/tcp: address already in use` — a server runs other things.

  The second made the host port an empty-by-default variable, so Docker would pick a free one. It
  died the same way, because **this file's contents get pasted into Dokploy's environment panel and
  live on there after the file changes**. A stale `HTTP_PORT=8080` in that panel still reached the
  compose file. A variable is a value somebody else can set.

  The only binding that cannot collide is the one that does not exist. Nothing needs it: Traefik
  routes to the `web` container over the proxy network, and `docker compose exec api …` needs no
  port. To browse the production build locally, `docker compose --profile local up -d` starts a
  second container from the same image that does publish one — and a profile cannot be switched on
  by a leftover entry in a deployment panel.

  The loopback rule still applies to everything that does publish (the dev Vite server, the `local`
  profile), and `scripts/check_compose_ports.py` enforces it in CI: written as `8080:80` the same
  line publishes an unencrypted app to the world, and it behaves identically in every local test.

- **Everything is reached through Caddy**, so `/api/*` is same-origin in both modes. That is what
  makes `connect-src 'self'` in the CSP a real constraint rather than a decoration.
- The security headers, the memory limits, the log rotation, the statement timeouts and the
  unprivileged user are the same in both.

## TLS: Traefik holds the certificate, Caddy does not

`deploy/Caddyfile` listens on `:80` with `auto_https off`. Dokploy's Traefik terminates TLS and
speaks plain HTTP to the `web` container over the internal network. Two things trying to obtain a
certificate for one hostname is how you get an ACME rate-limit and an outage.

Caddy still sets HSTS itself, because it is the app that knows it is only ever served over TLS.
Traefik setting it would also work; both setting it produces a duplicate header, and neither setting
it is a downgrade nobody notices.

The `proxy` network is declared `external` with an interpolated name:

```yaml
proxy:
  external: true
  name: ${PROXY_NETWORK:-bridge}
```

In production that is `dokploy-network`, which Dokploy created and Traefik is already attached to —
`external` because a second definition would make a separate network with the same name and nothing
would route. Locally the variable is unset and it falls back to `bridge`, which always exists, so
`docker compose up` needs no `docker network create` first.

## First deploy

1. **DNS.** An `A` record for `caesar.deplois.net` at the server's IP. Do this first: Traefik cannot
   obtain a certificate for a name that does not resolve, and a failed ACME attempt is rate-limited.
2. **New application** in Dokploy → *Docker Compose*.
   - Repository: this repo, branch `main`
   - Compose file: `docker-compose.yml`
3. **Domain**, in the app's Domains panel:

   | field | value | why |
   |---|---|---|
   | Service | `web` | Caddy. Not `api` — that serves no SPA, no `/static`, no `/media` |
   | Host | `caesar.deplois.net` | |
   | Path | `/` | |
   | **Port** | **`80`** | **the port `web` listens on.** `8000` is gunicorn, and pointing the domain there gets you a 502 or a redirect loop |
   | HTTPS | on, `letsencrypt` | Traefik obtains and holds the certificate |

   The compose file carries **no Traefik labels**. Dokploy writes the router from
   this panel, and labels as well would mean two routers matching one hostname —
   which is worse than either being wrong alone, because they can disagree and the
   winner is not obvious.

   **Behind Cloudflare**: only tick it if the DNS record is actually proxied (the
   orange cloud). If it is, Cloudflare's SSL mode must be **Full** or **Full
   (strict)** — never *Flexible*. Flexible talks plain HTTP to the origin while
   telling the browser it is HTTPS, and this stack redirects HTTP to HTTPS at two
   layers, so the request bounces between Cloudflare and the origin until the
   browser gives up: `ERR_TOO_MANY_REDIRECTS`, on a certificate that looks fine.
4. **Environment.** Paste `.env` into Dokploy's Environment panel. There is one env file and it
   works unchanged in both places — two env files is how a variable gets fixed in one and stays
   broken in the other. The secrets in it are real and generated. It is gitignored, so it is the
   only copy: keep it somewhere safe. Rotating `JWT_SIGNING_KEY` logs everyone out; rotating
   `LICENSE_PEPPER` bricks every activated terminal, because the stored licence hashes stop
   matching.
5. **Deploy.** The api container migrates, runs `sync_roles`, collects static files and starts
   gunicorn. `sync_roles` is in the start command rather than a runbook step because a release that
   adds a permission code otherwise lands with the code in the catalogue, the routes enforcing it,
   and no role holding it — deployed, and unreachable.
6. **Check.** `https://caesar.deplois.net/api/v1/system/health/` should answer `ok`.

## When a deploy fails on a dependency

```
dependency failed to start: container ...-redis-1 is unhealthy
```

This is the deploy working as intended, not a new bug. `web` waits for `api` to be healthy, and
`api` waits for postgres and redis. A dependency that never becomes healthy stops the deploy **and
leaves the previous deployment serving** — which is the correct outcome. The alternative is what
happened for three sessions: Caddy came up, Dokploy reported success, and every `/api/*` returned
502 while the logs everyone read were Caddy's, working perfectly, faithfully describing a failure
one container over.

Read the failure by name:

| the message says | look at | usual cause |
|---|---|---|
| `redis is unhealthy` | `docker compose logs redis` | replaying a large or truncated append-only file |
| `postgres is unhealthy` | `docker compose logs postgres` | WAL recovery after an unclean shutdown, or a password that no longer matches the volume |
| `api is unhealthy` | `docker compose logs api` | the `[boot]` markers name the step it stopped on |

Both data services get a 60s `start_period` and 10 retries, so a slow start is not mistaken for a
broken one — **a container that is still starting is not a container that is broken**, and leaving
that distinction out is what turned a slow redis into a permanent outage.

If redis is *still* unhealthy after that, its AOF is unreadable — a truncated write from an earlier
crash. Clearing it costs a cache and a task queue, never an order:

```sh
docker compose stop redis
docker volume ls | grep redisdata          # find the exact name
docker volume rm <project>_redisdata
docker compose up -d
```

The same move on postgres would destroy the database. Do not reach for it there — restore from a
backup instead (see [13 — Operations](13-operations.md)).

## Demo data, and the admin login

Three switches, all in `.env`, all deliberately separate. They answer different questions
and they stop being true at different times.

| | what it does | when to turn it off |
|---|---|---|
| `DEMO_SEED=1` | Seeds the café on first boot: one branch — catalogue, stock, suppliers, floor plan, kitchen stations, kids area, licence and two weeks of trading | once you have your own data |
| `DEMO_ADMIN=1` | Creates/refreshes the administrator on **every** boot, MFA off. Address and password come from `DEMO_ADMIN_EMAIL` / `DEMO_ADMIN_PASSWORD` | after `--rotate`, or it undoes the rotation |
| `DEMO_MODE=1` | Puts the ten demo staff logins **on the sign-in screen** as buttons | anywhere with real staff or real takings |

`DEMO_ADMIN` is separate from `DEMO_SEED` because seeding is a first-boot job: tying the admin to it
meant the account appeared once and then stopped being maintained the moment you set `DEMO_SEED=0` —
which the seed itself tells you to do. `demo_admin` creates or updates, so running it every start is
what makes the login reliably there.

`DEMO_MODE` drives `/system/info/`, which takes **no authentication**. Leaving it on anywhere real
publishes the staff login sheet.

The branch's licence key is printed once by the seed. A till cannot be activated without it:

```sh
docker compose logs api | grep "licence key"
```

`seed_demo` refuses to run once real orders exist, and the start script treats that refusal as
normal rather than fatal — so a second boot cannot mix demo rows into live trading, and cannot
crash-loop the container either.

> ⚠️ `admin`/`admin` on a public domain is guessed, not cracked. It is the first pair an automated
> scanner tries, and the account reads every sale, every staff record and every cost. This is what
> was asked for and it is what is deployed; it should not outlive the demo.
>
> ```
> docker compose exec api python manage.py demo_admin --rotate
> ```
>
> Prints a strong password once and does not store it. **Then set `DEMO_ADMIN=0`** — otherwise the
> next restart recreates `admin` and quietly undoes the rotation.

## Running the production shape locally

Worth doing before any deploy — it is the same containers, the same gunicorn, the same headers:

```sh
docker network create dokploy-network   # once — PROXY_NETWORK names it
docker compose --profile local up -d --build
# → http://127.0.0.1:8080   (HTTP_PORT changes it, and affects nothing else)
```

It works over plain `http://127.0.0.1:8080` only because Caddy sends `X-Forwarded-Proto: https`,
which is exactly what Traefik does in production. A CSP or header mistake surfaces here, on your
machine, instead of on the domain.

For day-to-day work on the code, change one line in `.env` (`DJANGO_ENV=dev`) and:

```sh
docker compose up -d --build            # runserver + built SPA
docker compose --profile dev up -d      # adds Vite with hot reload on :5173
docker compose watch                    # syncs backend edits into the container
```

`docker compose watch` replaces the old dev bind mount, which could not survive into a shared file:
mounting the host's `./backend` over `/app` in production would shadow the image's installed code
with whatever the clone happened to contain.

## Updating

Dokploy redeploys on push to `main`. Migrations run on every start, so an ordinary release needs
nothing by hand. Before a migration that rewrites a table, take a backup first —
`make prod-migrate` does both in that order.

## What is checked in CI

- `docker compose config` in **both** modes, so a mistake reachable from one mode only still fails.
- `scripts/check_compose_ports.py`, so no port escapes loopback.
- `caddy validate` on `deploy/Caddyfile`.
- `docker build -f deploy/Dockerfile.web`, which runs `vue-tsc --noEmit && vite build` inside the
  image — so the bundle Dokploy serves is known to have type-checked, not merely to have built.

---

**Next:** [13 — Operations](13-operations.md)
