# Caesar Cafe — كافيه القيصر

**Integrated POS, Kitchen & Management System**
Architecture Dossier — v1.0

> **Status: BUILT.** All ten phases and all twelve commitments are implemented. Every permission
> code in the catalogue is enforced by something a test can find. See
> [08-roadmap.md](08-roadmap.md) for what each phase delivered and what it cost.
>
> These documents are the *reasoning*, kept current with the code rather than frozen at sign-off.
> Where a decision was reversed during implementation, the roadmap says so and why — a dossier that
> only records the decisions that survived is a dossier that teaches nothing.

---

## Document Index

| # | Document | Covers |
|---|---|---|
| 01 | [System Architecture](01-system-architecture.md) | Component topology, runtime processes, folder structure, tech stack + rationale |
| 02 | [Data Model & ERD](02-data-model.md) | Entity diagrams per domain, core model definitions, financial precision rules |
| 03 | [API Map](03-api-map.md) | Every endpoint, versioning, envelope format, idempotency, error codes |
| 04 | [UI Map](04-ui-map.md) | Web Admin page tree, Desktop POS screen tree, Kitchen Display, navigation |
| 05 | [Permission Matrix](05-permissions.md) | Roles, granular permission codes, role × permission matrix, enforcement points |
| 06 | [Licensing & Activation](06-licensing.md) | Key generation, activation handshake, device binding, offline tokens, threat honesty |
| 07 | [Offline & Sync Engine](07-sync.md) | Event-sourced orders, outbox, cursors, conflict policy, invoice number allocation |
| 08 | [Development Roadmap](08-roadmap.md) | 10 phases, deliverables, exit criteria, definition of done |
| 09 | [Security Threat Model](09-security.md) | STRIDE-style analysis, trust boundaries, controls, accepted risks |
| 10 | [Future Extensions](10-future.md) | Multi-branch, SaaS, mobile, loyalty, e-invoicing, deliberately deferred items |
| 11 | [Configuration Framework](11-configuration.md) | Settings registry, scope resolution, the full ~180-setting catalog |
| 12 | [Kids Area](12-kids-area.md) | Time-based billing, tariff engine, capacity & child-safety rules |
| 13 | [Operations Runbook](13-operations.md) | Secret rotation, the restore drill, reading the audit trail during an incident |
| 14 | [دليل التدريب](14-training.md) | **In Arabic**, for the people who will actually use it: cashier, waiter, kitchen, kids area, manager, owner. Every step says why |

---

## The One-Paragraph Summary

PostgreSQL behind a Django REST API is the **single source of truth**. A Vue 3 SPA is the
**management control plane** — it owns all master data, pricing, staff, licensing, and reporting.
A PySide6 Windows application is the **operational client** — cashier, floor, kitchen, stock
counts — and it cannot start until a branch has been activated with a license key issued by the
Web Admin. The Desktop keeps a local SQLite mirror so the cafe keeps selling when the internet
drops, and reconciles through an append-only event log that is idempotent by construction. Money
is never computed on the client. Authorization is never trusted from the client.

---

## Architectural Commitments

These are the decisions that are expensive to reverse later. They are called out here so the
review can focus on them.

| # | Commitment | Where argued |
|---|---|---|
| **C1** | **Orders are an append-only event stream**, not mutable rows. The server folds events into an Order aggregate. | [07](07-sync.md#the-central-decision-orders-are-events) |
| **C2** | Multi-tenancy is **shared-schema** (`organization_id` + `branch_id` columns), not schema-per-tenant. | [01](01-system-architecture.md#tenancy-strategy) |
| **C3** | Sync uses a **server-side monotonic change cursor**, never `updated_at` timestamps. | [07](07-sync.md#pull-server--desktop) |
| **C4** | Devices authenticate with a **server-issued device secret**, not a hardware fingerprint. Fingerprint is advisory telemetry only. | [06](06-licensing.md#why-not-mac-address-binding) |
| **C5** | Offline startup is authorized by an **Ed25519-signed license token**; the private key never leaves the server. | [06](06-licensing.md#the-offline-license-token) |
| **C6** | Inventory deduction happens **server-side only**, from recipes, inside a locked transaction. The Desktop never writes authoritative stock. | [02](02-data-model.md#inventory--recipes) |
| **C7** | Permissions are **string codes on a custom Role model**, not Django `auth.Permission`. | [05](05-permissions.md#why-not-djangos-built-in-permissions) |
| **C8** | Arabic receipts are **rendered to a raster bitmap** before printing, not sent as ESC/POS text. | [01](01-system-architecture.md#printing-subsystem) |
| **C9** | Invoice numbers come from **server-allocated blocks** reserved per device, so offline sales stay gapless and sequential. | [07](07-sync.md#invoice-numbering-under-partition) |
| **C10** | **No business value is a code constant.** Every rate, threshold, label, list, and toggle is a registered setting resolved Device → Branch → Org → default. | [11](11-configuration.md) |
| **C11** | The system is **internet-facing from Phase 1**, not LAN-only, because the owner monitors remotely. Security hardening moves into the early phases. | [09](09-security.md) |
| **C12** | A kids-area play session is a **running meter that converts to one order line at checkout** — never a special billing path in the financial core. | [12](12-kids-area.md) |

---

## Assumptions Register

Per **C10**, every value below is a *default*, not a decision — all of them are editable from the
Web Admin without a deployment. The full catalog is in [11](11-configuration.md). Nothing here is
baked into code, so a wrong default costs a click rather than a release.

### Business

| ID | Default | Setting key | Notes |
|---|---|---|---|
| A1 | EGP, 2 decimals | `org.currency` | |
| A2 | `Africa/Cairo` | `org.timezone` | All timestamps stored UTC |
| A3 | VAT 14%, exclusive | `finance.vat_percent`, `finance.vat_inclusive` | Per-product exemption supported |
| A4 | Service 12%, Dine-In, **off** | `finance.service_percent`, `finance.service_applies_to` | |
| A5 | Business day starts **04:00** | `finance.business_day_start` | ✅ **Resolved** — configurable, no fixed assumption |
| A6 | Delivery modeled, not built | `orders.enabled_types` | Enable when the feature ships |
| A7 | ETA e-invoicing out of scope | `printing.show_qr` reserved | See [10](10-future.md) |
| A8 | 1 branch at launch | — | Architecture supports N |
| A9 | 2–4 devices | `license.max_devices` | |
| A10 | <300 orders/day | — | Sizing only |
| A11 | Service model: **waiter terminals** | `floor.service_mode` | ✅ **Resolved** — admin picks the mode |
| A12 | Week starts Saturday | `finance.week_start_day` | Egyptian business week |
| A13 | Stock deducts at **payment** | `inventory.deduct_on` | `FIRE` / `PAYMENT` / `SERVE` |

### Technical

| ID | Assumption | Default chosen |
|---|---|---|
| T1 | Desktop OS | Windows 10/11 x64 |
| T2 | Receipt printer | 80mm ESC/POS thermal (USB or LAN) |
| T3 | Kitchen output | Screen (KDS) **and** optional ticket printer — both supported |
| T4 | Cash drawer | Kick-opened via the receipt printer's DK port |
| T5 | Server hosting | Single VPS, Docker Compose. Not Kubernetes. |
| T6 | Backend domain | `https://api.<domain>` — **internet-facing from Phase 1**. Desktop never touches PostgreSQL directly |
| T7 | Max tolerated offline window | 72h grace (`license.offline_grace_hours`) |
| T8 | UI primary language | Arabic, RTL-first; English strings present from day one |
| T9 | Owner remote access | **Required.** Web Admin is an installable PWA with push notifications |

### Resolved Questions

All three architectural questions from the first review are answered. Details in
[11](11-configuration.md#answers-to-the-three-open-questions).

| Question | Answer | Consequence |
|---|---|---|
| Business day cutoff | **Configurable**, default 04:00 | Every report derives its boundary from one setting; historical rollups keep the boundary they were computed under |
| Cashier vs waiter ordering | **Admin chooses** — three service modes plus independent toggles | Floor/Waiter screens are firmly **in Phase 5**, not deferred; `CASHIER_ONLY` simply hides them |
| Remote owner access | **Yes** | Public TLS from Phase 1; MFA mandatory for admin roles; rate limiting, fail2ban, and alerting move from Phase 9 into Phases 1–3; Web Admin becomes a PWA |

**No open questions remain.** All three were answered before Phase 1, and none of the three had to
be reopened during implementation — which is the only evidence that answering them up front was
worth the delay.

---

## How to Read This Dossier

If you have 10 minutes: this file + the diagrams in [01](01-system-architecture.md).
If you have an hour: add [06](06-licensing.md) and [07](07-sync.md) — those two are where a
mistake gets structurally expensive, and they are the reason we are reviewing before coding.
If you want to check that nothing important is hardcoded: [11](11-configuration.md) is the full
settings catalog, and every default in this dossier traces back to a key in it.
