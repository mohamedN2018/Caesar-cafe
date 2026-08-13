# Caesar Cafe — كافيه القيصر

Integrated POS, Kitchen & Management System — an internal system for the kitchen and living
room, fully connected to the web system.

A Django REST API is the source of truth. A Vue 3 web application is the management control plane
(catalog, pricing, staff, inventory, suppliers, reports, licensing). A PySide6 Windows application
is the operational client (cashier, floor, kitchen, stock) and cannot start until the branch has
been activated with a license key issued from the web system. The desktop keeps working offline and
reconciles through an idempotent event log.

---

## Status

**Phases 1–5 complete** (domain layers) — **504 tests passing** (440 backend + 64 desktop).
Foundation, identity, authorization, licensing, the desktop activation gate, catalog,
inventory, purchasing, and event-sourced orders with payments and shifts.

Outstanding: the REST surface for orders/payments/shifts, the Web Admin screens, and
Phases 6–10 (kitchen, kids area, sync, reporting, hardening, production). See the
[roadmap](docs/08-roadmap.md).

```bash
cp .env.example .env          # adjust API_PORT if 8000 is taken
make up                       # postgres, redis, api, worker, beat, frontend
make migrate
docker compose run --rm api \
  python manage.py bootstrap --admin-email=you@example.com
make check                    # lint + typecheck + tests
```

API → `http://localhost:${API_PORT}/api/v1/system/health/`
Web → `http://localhost:${FRONTEND_PORT}/`
Docs → `/api/v1/docs/` (dev only)

**Desktop client** (`desktop/`) — PySide6, Windows:

```bash
make signing-key              # generate LICENSE_SIGNING_KEY, put it in .env
make desktop-test             # 64 tests, headless
python scripts/vendor_shared.py --check   # shared logic in sync with backend
```

The desktop vendors `money.py`, `offline_token.py` and `keys.py` from the backend
verbatim — order totals and licence checks must give identical answers on both
sides, so they are copied rather than reimplemented, and CI fails if they drift.

---

## Architecture

The full design is documented and reviewed:

### 📐 [Read the Architecture Dossier →](docs/README.md)

| | |
|---|---|
| [01 System Architecture](docs/01-system-architecture.md) | Topology, folder structure, stack rationale, Docker |
| [02 Data Model & ERD](docs/02-data-model.md) | Entity diagrams, models, financial precision |
| [03 API Map](docs/03-api-map.md) | Endpoints, envelope, idempotency, WebSockets |
| [04 UI Map](docs/04-ui-map.md) | Web pages, desktop screens, kitchen display |
| [05 Permission Matrix](docs/05-permissions.md) | Roles, codes, limits, step-up approval |
| [06 Licensing & Activation](docs/06-licensing.md) | Key generation, device binding, offline tokens |
| [07 Offline & Sync Engine](docs/07-sync.md) | Event-sourced orders, outbox, conflict policy |
| [08 Development Roadmap](docs/08-roadmap.md) | 10 phases, exit criteria, definition of done |
| [09 Security Threat Model](docs/09-security.md) | STRIDE analysis, controls, accepted risks |
| [10 Future Extensions](docs/10-future.md) | Multi-branch, SaaS, and what we're deliberately not doing |
| [11 Configuration Framework](docs/11-configuration.md) | Settings registry and the full ~180-setting catalog |
| [12 Kids Area](docs/12-kids-area.md) | Time-based billing, tariff engine, capacity & child safety |

Start with the [index](docs/README.md) — it carries the eleven architectural commitments and the
assumptions register.

Every business value in the system is a setting the admin edits from the web, not a constant in the
code — service model, tax rates, thresholds, limits, labels, and lists. See
[11](docs/11-configuration.md).

---

## Planned Stack

**Backend** Python 3.12 · Django 5 · DRF · PostgreSQL 16 · Redis 7 · Celery · Channels
**Frontend** Vue 3 · Vite · TypeScript · Tailwind (RTL-first) · Pinia · ECharts
**Desktop** PySide6 · SQLite · PyInstaller + Inno Setup
**Infra** Docker Compose · Caddy (automatic TLS)
