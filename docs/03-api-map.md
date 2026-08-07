# 03 — API Map

Covers master prompt section **E**, plus §31 (design), §32 (docs), §41 (errors), §51 (idempotency).

---

## Conventions

**Base:** `https://api.<domain>/api/v1/` — version in the path, so a v2 can run beside v1 during a
Desktop rollout. This matters more than usual here: field terminals update on their own schedule,
and the server cannot break a POS that has not been upgraded yet.

**Envelope.** Every response uses the same shape (§41), so the Desktop and the SPA share one
parser and one error path.

```jsonc
// success
{ "success": true, "data": { }, "meta": { "request_id": "01JC…" } }

// paginated
{ "success": true,
  "data": [ ],
  "meta": { "count": 240, "next": "?cursor=…", "previous": null, "request_id": "01JC…" } }

// failure
{ "success": false,
  "message": "الكمية المتاحة غير كافية",        // safe to display, localized
  "code": "INSUFFICIENT_STOCK",                 // stable, machine-readable, never localized
  "errors": { "quantity": ["الحد الأقصى 4"] },  // per-field, for form binding
  "meta": { "request_id": "01JC…" } }
```

`request_id` is generated per request, returned in the body and the `X-Request-ID` header, and
written into every log line for that request. When a cashier reports a problem, that single string
retrieves the whole server-side story.

Raw exceptions never reach a client. A custom `EXCEPTION_HANDLER` maps everything; unmapped
exceptions become `INTERNAL_ERROR` with a generic Arabic message, and the traceback goes to logs
and Sentry only.

**Pagination:** cursor-based for anything that grows without bound (orders, movements, audit log)
— offset pagination degrades badly and, worse, skips or duplicates rows when data is inserted
mid-scroll. Small bounded lists (categories, tables, payment methods) return unpaginated.

**Filtering:** `django-filter` everywhere. Standard params: `?search=`, `?ordering=`, `?date_from=`,
`?date_to=`, `?branch=`, `?is_active=`.

### Authentication contexts

Three distinct principals, three different token types. Conflating them is a common and serious
mistake:

| Principal | Obtains token via | Lifetime | Can reach |
|---|---|---|---|
| **Web user** | `POST /auth/login/` (email + password) | 15m access / 7d rotating refresh | Management API |
| **Device** | `POST /licensing/activate/` then `/licensing/device-token/` | 60m access / 30d refresh | Sync + operational API **only** |
| **Device + user** | Device token + `POST /auth/pos-login/` (PIN) | tied to shift | Adds the user's permission set on top of the device token |

A device token alone can pull the catalog and push sync operations. It **cannot** create a payment
— that needs a human principal attached. And a user token from the Web Admin cannot call
`/sync/push/`, because sync is a device concern. Each surface is reachable only by the principal
that legitimately needs it.

### Idempotency (§51)

Mutating endpoints in the critical path accept an `Idempotency-Key` header (a client-generated
UUID):

```
POST /api/v1/orders/          POST /api/v1/payments/
POST /api/v1/sync/push/       POST /api/v1/refunds/
POST /api/v1/shifts/close/    POST /api/v1/purchasing/receipts/
```

Server behaviour:

1. `INSERT` the key into `idempotency_record` with a unique constraint. A conflict means this is a
   replay.
2. If the original request is still in flight → `409 REQUEST_IN_PROGRESS`; the client retries with
   backoff.
3. If it completed → return the **stored original response verbatim**, with `Idempotency-Replayed: true`.
4. Keys are scoped per device and expire after 48h.

A payment retried after a timeout charges the customer once. This is not optional and cannot be
bolted on later, because the retry semantics of every client call depend on it.

---

## E. Endpoint Map

### Authentication & Identity — `/auth/`

| Method | Path | Purpose | Auth |
|---|---|---|---|
| POST | `/auth/login/` | Web login → access + refresh | public, throttled 5/min/IP |
| POST | `/auth/refresh/` | Rotate refresh token, detect reuse | refresh token |
| POST | `/auth/logout/` | Revoke refresh token | user |
| POST | `/auth/pos-login/` | PIN login on an activated device | device token, throttled 5/min/device |
| POST | `/auth/pos-logout/` | End POS user session | device+user |
| POST | `/auth/verify-pin/` | Step-up auth for a privileged action | device+user |
| GET | `/auth/me/` | Profile + effective permission codes | any |
| POST | `/auth/change-password/` | Self-service | user |

`/auth/verify-pin/` is how a cashier without `orders.discount` gets a manager to approve one
without logging out: the manager types their PIN, the server returns a short-lived,
single-use **approval token** naming the exact permission and target, and the Desktop attaches it
to the next request. The approval is recorded in the audit log against the manager, not the
cashier — which is the entire point.

### Organizations & Branches — `/organizations/`, `/branches/`

| Method | Path | Purpose |
|---|---|---|
| GET/PATCH | `/organizations/current/` | Org profile |
| GET/POST | `/branches/` | List / create |
| GET/PATCH | `/branches/{id}/` | Detail |
| GET | `/branches/{id}/config/` | **Bundled config for Desktop** — single call |
| POST | `/setup/wizard/{step}/` | First-run wizard (§58) |
| GET | `/settings/schema/` | The registry — drives the self-rendering settings UI |
| GET | `/settings/` | Resolved values for a scope, with origin per key |
| PATCH | `/settings/` | Write overrides; validated, audited, permission-gated per key |
| DELETE | `/settings/{key}/` | Reset to default |
| GET | `/settings/history/` | Who changed what, when |

`/branches/{id}/config/` returns everything the Desktop needs to operate — every setting marked
`pushes_to_desktop`, plus payment methods, printers, and stations — as one versioned document with
an ETag. The Desktop sends `If-None-Match` and usually gets a `304`. One round trip on boot instead
of eight, and configuration can never be partially applied.

The `/settings/` endpoints are generic by design: they read the registry rather than enumerating
keys, so adding a setting requires no API change and no new endpoint. See
[11](11-configuration.md).

### Licensing — `/licensing/`

| Method | Path | Purpose | Auth |
|---|---|---|---|
| POST | `/licensing/activate/` | **Activation handshake** | public, throttled 5/hr/IP |
| POST | `/licensing/device-token/` | Device secret → access token | device secret |
| POST | `/licensing/heartbeat/` | Liveness + license status + refreshed offline token | device |
| GET/POST | `/licensing/licenses/` | Admin: list / issue | `licenses.manage` |
| GET/PATCH | `/licensing/licenses/{id}/` | Detail / edit | `licenses.manage` |
| POST | `/licensing/licenses/{id}/suspend/` | Suspend | `licenses.manage` |
| POST | `/licensing/licenses/{id}/revoke/` | Revoke — kills all devices | `licenses.manage` |
| POST | `/licensing/licenses/{id}/renew/` | Extend expiry | `licenses.manage` |
| GET | `/licensing/licenses/{id}/events/` | License audit trail | `licenses.view` |
| GET | `/licensing/devices/` | Activated devices + last seen | `devices.view` |
| POST | `/licensing/devices/{id}/revoke/` | Kill a device | `devices.manage` |
| POST | `/licensing/devices/{id}/reset/` | Free the seat for re-activation | `devices.manage` |

### Catalog — `/catalog/`

| Method | Path | Notes |
|---|---|---|
| GET/POST | `/catalog/categories/` | Tree via `?parent=`; ordered |
| GET/PATCH/DELETE | `/catalog/categories/{id}/` | DELETE = deactivate |
| GET/POST | `/catalog/products/` | `?category=`, `?station=`, `?search=`, `?is_active=` |
| GET/PATCH | `/catalog/products/{id}/` | |
| POST | `/catalog/products/{id}/image/` | Multipart; server resizes to 3 sizes |
| POST | `/catalog/products/bulk-price/` | Bulk change → writes `PRICE_HISTORY` + audit |
| GET/POST | `/catalog/products/{id}/variants/` | |
| GET/POST | `/catalog/modifier-groups/` | |
| GET/POST | `/catalog/recipes/` | |
| GET/PATCH | `/catalog/recipes/{id}/` | Recalculates variant cost on save |
| GET | `/catalog/recipes/{id}/cost/` | Live cost from current weighted-avg |
| POST | `/catalog/import/` | CSV/XLSX bulk import, dry-run first |

### Floor — `/floor/`

| Method | Path | Notes |
|---|---|---|
| GET/POST | `/floor/areas/` | |
| GET/POST | `/floor/tables/` | Includes `pos_x`/`pos_y` for the visual map |
| PATCH | `/floor/tables/{id}/` | Includes drag-to-reposition |
| GET | `/floor/status/` | Live board: every table + its open order summary |
| POST | `/floor/tables/{id}/transfer/` | Move a session to another table |
| POST | `/floor/tables/{id}/merge/` | Merge sessions |

### Orders — `/orders/`

| Method | Path | Notes |
|---|---|---|
| GET | `/orders/` | `?status=`, `?date_from=`, `?table=`, `?shift=`, `?type=` |
| POST | `/orders/` | Create; **idempotent** |
| GET | `/orders/{id}/` | Aggregate with items, modifiers, payments, tickets |
| POST | `/orders/{id}/events/` | **Primary mutation path** — append events |
| GET | `/orders/{id}/events/` | Full event stream (audit / debugging) |
| POST | `/orders/{id}/fire/` | Send to kitchen → creates tickets |
| POST | `/orders/{id}/void/` | Requires `orders.void` or an approval token |
| POST | `/orders/{id}/discount/` | Requires `orders.discount` |
| POST | `/orders/{id}/split/` | Split into multiple orders |
| POST | `/orders/{id}/move-items/` | Move items to another order |
| GET | `/orders/{id}/receipt/` | `ReceiptDocument` JSON for rendering |

`POST /orders/{id}/events/` is where nearly all order mutation happens. There is no
`PATCH /orders/{id}/` that lets a client set a total — totals are computed, never received.
Payload:

```jsonc
{ "events": [
    { "id": "0193…", "sequence": 4, "type": "ITEM_ADDED",
      "occurred_at": "2026-08-06T14:32:11Z",
      "payload": { "variant_id": "…", "quantity": 2,
                   "modifiers": ["…"], "note": "بدون سكر" } }
] }
```

Event types: `ORDER_OPENED`, `ITEM_ADDED`, `ITEM_QUANTITY_CHANGED`, `ITEM_VOIDED`, `ITEM_NOTE_SET`,
`MODIFIER_ADDED`, `MODIFIER_REMOVED`, `DISCOUNT_APPLIED`, `ORDER_FIRED`, `TABLE_ASSIGNED`,
`CUSTOMER_ASSIGNED`, `PAYMENT_TAKEN`, `ORDER_CLOSED`, `ORDER_VOIDED`.

### Payments & Invoices — `/payments/`

| Method | Path | Notes |
|---|---|---|
| GET | `/payments/methods/` | Configurable per branch |
| POST | `/payments/` | **Idempotent.** Supports split payment |
| GET | `/payments/{id}/` | |
| POST | `/refunds/` | Requires `orders.refund`; always partial-capable |
| GET | `/invoices/` | |
| GET | `/invoices/{id}/` | Frozen snapshot |
| GET | `/invoices/{id}/pdf/` | Server-rendered A4/80mm PDF |
| POST | `/invoices/blocks/allocate/` | Device reserves a number block |

### Kitchen — `/kitchen/`

| Method | Path | Notes |
|---|---|---|
| GET/POST | `/kitchen/stations/` | |
| GET | `/kitchen/tickets/` | `?station=`, `?status=`; the KDS poll fallback |
| POST | `/kitchen/tickets/{id}/accept/` | |
| POST | `/kitchen/tickets/{id}/start/` | → PREPARING |
| POST | `/kitchen/tickets/{id}/ready/` | → READY, notifies POS |
| POST | `/kitchen/tickets/{id}/lines/{lid}/ready/` | Per-item readiness |
| POST | `/kitchen/tickets/{id}/recall/` | Bring a served ticket back |
| GET | `/kitchen/performance/` | Prep times, late count, by station |

### Inventory — `/inventory/`

| Method | Path | Notes |
|---|---|---|
| GET/POST | `/inventory/items/` | |
| GET/PATCH | `/inventory/items/{id}/` | |
| GET | `/inventory/levels/` | Current stock + value; `?low_stock=true` |
| GET | `/inventory/movements/` | The ledger; `?item=`, `?type=`, date range |
| POST | `/inventory/adjustments/` | Requires `inventory.adjust` + a reason |
| POST | `/inventory/waste/` | Waste with reason codes |
| GET/POST | `/inventory/counts/` | Physical count sessions |
| POST | `/inventory/counts/{id}/post/` | Posts variances as movements |
| GET | `/inventory/valuation/` | Stock value by item / category |
| GET/POST | `/inventory/units/` | Units + conversions |

### Suppliers & Purchasing — `/suppliers/`, `/purchasing/`

| Method | Path | Notes |
|---|---|---|
| GET/POST | `/suppliers/` | |
| GET/PATCH | `/suppliers/{id}/` | |
| GET | `/suppliers/{id}/ledger/` | Statement of account |
| POST | `/suppliers/{id}/payments/` | Records payment, moves balance |
| GET/POST | `/purchasing/orders/` | POs |
| POST | `/purchasing/orders/{id}/submit/` | DRAFT → SUBMITTED |
| GET/POST | `/purchasing/receipts/` | **Goods receipt — the only thing that raises stock** |
| POST | `/purchasing/returns/` | |
| GET | `/purchasing/suggestions/` | Reorder list from `reorder_level` |

### Shifts — `/shifts/`

| Method | Path | Notes |
|---|---|---|
| GET | `/shifts/` | |
| POST | `/shifts/open/` | Opening float; one open shift per device |
| GET | `/shifts/current/` | |
| POST | `/shifts/{id}/cash-movements/` | Paid-in / paid-out / expense / drop |
| GET | `/shifts/{id}/x-report/` | Mid-shift read, non-closing |
| POST | `/shifts/{id}/close/` | Counted cash → variance → Z-report. **Idempotent** |
| GET | `/shifts/{id}/z-report/` | Frozen close-out |

### Sync — `/sync/`

| Method | Path | Notes |
|---|---|---|
| POST | `/sync/push/` | Batched operations from the outbox. **Idempotent** |
| GET | `/sync/pull/` | `?stream=&cursor=&limit=` |
| GET | `/sync/bootstrap/` | Full snapshot for a fresh device |
| GET | `/sync/status/` | Server view of this device's lag |
| POST | `/sync/conflicts/{id}/resolve/` | Manual resolution |

### Reports — `/reports/`

All accept `date_from`, `date_to`, `?format=json|csv|xlsx|pdf`. Large exports return `202` with a
task id and deliver via Celery + a notification — a year-long product report must not hold an HTTP
connection open.

| Path | Content |
|---|---|
| `/reports/sales/summary/` | Gross, discounts, refunds, net, tax, service |
| `/reports/sales/by-hour/` | Peak-hour staffing analysis |
| `/reports/sales/by-category/` | |
| `/reports/sales/by-payment-method/` | Cash-vs-card reconciliation |
| `/reports/products/top/` | Best/worst by qty and revenue |
| `/reports/products/profitability/` | Revenue − recipe cost per product |
| `/reports/inventory/movements/` | |
| `/reports/inventory/waste/` | |
| `/reports/inventory/variance/` | Theoretical vs counted — the shrinkage report |
| `/reports/purchases/summary/` | |
| `/reports/suppliers/balances/` | |
| `/reports/employees/sales/` | By cashier |
| `/reports/employees/voids/` | **Void and discount rates per user** |
| `/reports/shifts/variance/` | Cash variance trend by user |
| `/reports/financial/pnl/` | Net sales − COGS = gross profit |
| `/reports/dashboard/` | Everything the home dashboard needs, one call |

`/reports/employees/voids/` and `/reports/shifts/variance/` are the loss-prevention pair. Cash
shrinkage in cafes shows up as a pattern of voids-after-firing and consistent negative variance
concentrated on one person. The data is captured anyway; surfacing it costs one query each.

### Audit, Notifications, System

| Method | Path | Notes |
|---|---|---|
| GET | `/audit/logs/` | `?actor=`, `?action=`, `?entity=`, date range |
| GET | `/audit/logs/{id}/` | Before/after diff |
| GET | `/notifications/` | In-app feed |
| POST | `/notifications/{id}/read/` | |
| GET/PATCH | `/notifications/rules/` | Thresholds, channels |
| GET | `/system/health/` | Liveness — public, unauthenticated |
| GET | `/system/info/` | Versions, min supported client |
| GET/POST | `/system/backups/` | List / trigger. `backups.manage` |
| GET | `/system/backups/{id}/download/` | Signed, short-lived URL |

Restore is **not** an API endpoint. It is an operator procedure run on the host with documented
steps. An HTTP-reachable "restore database" button is a catastrophic single point of failure and
is worth the inconvenience of a manual runbook.

---

## WebSocket API

`wss://api.<domain>/ws/branch/{branch_id}/?token=<device_or_user_token>`

Authenticated in the connect handler; unauthenticated sockets are closed before joining any group.

| Group | Subscribers | Events |
|---|---|---|
| `branch.<id>.kitchen` | KDS | `ticket.created`, `ticket.updated`, `ticket.cancelled` |
| `branch.<id>.station.<sid>` | Per-station KDS | Same, filtered |
| `branch.<id>.pos` | Cashier terminals | `ticket.ready`, `order.updated`, `table.updated` |
| `branch.<id>.floor` | Floor tablets | `table.status_changed`, `order.updated` |
| `branch.<id>.admin` | Web dashboard | `sale.completed`, `stock.low`, `device.offline` |

Every client also polls its REST fallback on a slow interval (30–60s). WebSockets are an
optimization, never a correctness requirement — a dropped socket must degrade the kitchen's
latency, not lose its tickets.

---

## §32 — Documentation

`drf-spectacular` generates OpenAPI 3.1 at build time:

- `/api/v1/schema/` — raw
- `/api/v1/docs/` — Swagger UI (staging/dev only)
- `/api/v1/redoc/` — ReDoc

The schema is the contract, and it is enforced in both directions:

1. **Frontend types** are generated from it (`openapi-typescript`) — a removed field becomes a
   TypeScript compile error, not a runtime `undefined`.
2. **CI diffs the schema** against the committed snapshot. A breaking change without a version bump
   fails the build.
3. **Desktop contract tests** replay recorded schema responses so a server change that would break
   a field terminal is caught before it ships.

---

**Next:** [04 — UI Map](04-ui-map.md)
