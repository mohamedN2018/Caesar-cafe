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
- **Every published port binds `127.0.0.1`, and none of them is a fixed number.** Two separate
  rules, and the second was learned the hard way.

  `127.0.0.1`, because written as `8080:80` the same line publishes an unencrypted app to the
  world — and it behaves identically in every local test, which is why
  `scripts/check_compose_ports.py` checks it in CI rather than a reviewer checking it by eye.

  **No fixed number**, because the first Dokploy deploy died on
  `failed to bind port 127.0.0.1:8080/tcp: address already in use`. A server runs other things, and
  with one `.env` for both environments there is no port guaranteed free in both. Unset, Docker
  picks a free one. Nothing needs it predictable — Traefik reaches the web container over the proxy
  network, and Vite proxies `/api` to `api:8000` over the compose network. Ask Docker what it chose:

  ```sh
  docker compose port web 80
  docker compose port api 8000
  ```

  Set `HTTP_PORT` only when you want a fixed local URL, and remember the same file goes to the
  server.
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
   - Domain: `caesar.deplois.net`, HTTPS on, Let's Encrypt
3. **Environment.** Paste `.env` into Dokploy's Environment panel. There is one env file and it
   works unchanged in both places — two env files is how a variable gets fixed in one and stays
   broken in the other. The secrets in it are real and generated. It is gitignored, so it is the
   only copy: keep it somewhere safe. Rotating `JWT_SIGNING_KEY` logs everyone out; rotating
   `LICENSE_PEPPER` bricks every activated terminal, because the stored licence hashes stop
   matching.
4. **Deploy.** The api container migrates, runs `sync_roles`, collects static files and starts
   gunicorn. `sync_roles` is in the start command rather than a runbook step because a release that
   adds a permission code otherwise lands with the code in the catalogue, the routes enforcing it,
   and no role holding it — deployed, and unreachable.
5. **Check.** `https://caesar.deplois.net/api/v1/system/health/` should answer `ok`.

## Demo data, and the admin login

Three switches, all in `.env`, all deliberately separate. They answer different questions
and they stop being true at different times.

| | what it does | when to turn it off |
|---|---|---|
| `DEMO_SEED=1` | Seeds the café on first boot: one branch — catalogue, stock, suppliers, floor plan, kitchen stations, kids area, licence and two weeks of trading | once you have your own data |
| `DEMO_ADMIN=1` | Creates/refreshes `admin@caesar.deplois.net` / `admin` on **every** boot, MFA off | after `--rotate`, or it undoes the rotation |
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
docker compose up -d --build
docker compose port web 80              # → 127.0.0.1:PORT, open it
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
