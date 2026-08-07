# Caesar Cafe â€” ÙƒØ§ÙÙŠÙ‡ Ø§Ù„Ù‚ÙŠØµØ±

Integrated POS, Kitchen & Management System.

A Django REST API is the source of truth. A Vue 3 web application is the management control plane
(catalog, pricing, staff, inventory, suppliers, reports, licensing). A PySide6 Windows application
is the operational client (cashier, floor, kitchen, stock) and cannot start until the branch has
been activated with a license key issued from the web system. The desktop keeps working offline and
reconciles through an idempotent event log.

---

## Status

**Phases 1â€“3 complete.** Foundation, identity, authorization, licensing, and the
desktop activation gate â€” **405 tests passing** (341 backend + 64 desktop), 28 endpoints.
Next: Phase 4, catalog & inventory. See the [roadmap](docs/08-roadmap.md).

```bash
cp .env.example .env          # adjust API_PORT if 8000 is taken
make up                       # postgres, redis, api, worker, beat, frontend
make migrate
docker compose -f docker-compose.dev.yml run --rm api \
  python manage.py bootstrap --admin-email=you@example.com
make check                    # lint + typecheck + tests
```

API â†’ `http://localhost:${API_PORT}/api/v1/system/health/`
Web â†’ `http://localhost:${FRONTEND_PORT}/`
Docs â†’ `/api/v1/docs/` (dev only)

**Desktop client** (`desktop/`) â€” PySide6, Windows:

```bash
make signing-key              # generate LICENSE_SIGNING_KEY, put it in .env
make desktop-test             # 64 tests, headless
python scripts/vendor_shared.py --check   # shared logic in sync with backend
```

The desktop vendors `money.py`, `offline_token.py` and `keys.py` from the backend
verbatim â€” order totals and licence checks must give identical answers on both
sides, so they are copied rather than reimplemented, and CI fails if they drift.

---

## Architecture

The full design is documented and reviewed:

### ðŸ“ [Read the Architecture Dossier â†’](docs/README.md)

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

Start with the [index](docs/README.md) â€” it carries the eleven architectural commitments and the
assumptions register.

Every business value in the system is a setting the admin edits from the web, not a constant in the
code â€” service model, tax rates, thresholds, limits, labels, and lists. See
[11](docs/11-configuration.md).

---

## Planned Stack

**Backend** Python 3.12 Â· Django 5 Â· DRF Â· PostgreSQL 16 Â· Redis 7 Â· Celery Â· Channels
**Frontend** Vue 3 Â· Vite Â· TypeScript Â· Tailwind (RTL-first) Â· Pinia Â· ECharts
**Desktop** PySide6 Â· SQLite Â· PyInstaller + Inno Setup
**Infra** Docker Compose Â· Caddy (automatic TLS)
