# 10 — Future Extensions

Covers master prompt section **N**, plus §43 (multi-branch), §44 (multi-tenant).

---

## The Governing Rule

§44 asks for a SaaS-ready architecture and, in the same breath, warns against over-engineering v1.
Those are only in tension if you conflate two different things:

- **Leaving room** — a nullable `branch_id`, a UUID primary key, a scoped queryset manager. Costs
  close to nothing today. Removes a painful migration later.
- **Building the feature** — a billing engine, a tenant signup flow, a plan matrix. Costs weeks
  today for a customer who does not exist.

Everything below is deliberately *not built*. What follows each item is the specific seam already
present in the v1 schema that makes building it later a feature rather than a rewrite.

---

## Near Term

### 1. Second Branch

**Seam:** `Organization → Branch` exists from day one; every business row carries `branch_id`; no
code contains a hardcoded branch (enforced by review and by the tenant-scoping manager).

Remaining work: a branch switcher in the SPA header, `RoleAssignment.branch` filtering in the UI,
cross-branch consolidated reports, and per-branch licensing (already modeled — `License.branch`).

Genuinely new decisions: whether the catalog is shared or per-branch (likely shared master with
per-branch price overrides — a new `BranchProductOverride` table, additive), and inter-branch stock
transfers (`StockMovement.movement_type = TRANSFER` is already reserved).

**Estimated: small.** This is the payoff for C2.

### 2. Auto-Update

Deferred from v1 per AR6. When built: staged rollout by device, signature verification against a
pinned key, download-then-verify-then-swap, automatic rollback on a failed launch, and **never
during business hours**. `min_supported_version` and `app_version` reporting already exist to drive
it.

### 3. Customer Loyalty

**Seam:** `Customer` exists with `visit_count` and `lifetime_value`; orders can already reference
one.

Adds: points accrual rules, redemption as an order event type (`LOYALTY_REDEEMED` — the event model
absorbs this without schema change), a tier model, and phone-based lookup at the POS. The event
sourcing from C1 pays off here: a redemption becomes one more immutable, attributable fact on the
order.

### 4. Delivery & Aggregators

**Seam:** `order_type = DELIVERY` is already in the state machine; `Customer.address` exists.

Adds: a driver model, delivery-status tracking, zone-based fees, and webhook ingestion from
Talabat / Elmenus. The important design note: an aggregator order arrives as an event stream from
an external source, which is exactly the shape the order aggregate already accepts. Aggregator
commission needs a distinct field so net revenue reporting stays honest.

---

## Medium Term

### 5. Egyptian ETA E-Invoicing

The most likely regulatory requirement to become mandatory for this business.

**Seam:** `Invoice.snapshot` freezes the full document as JSON; `Organization.tax_number` and
`Supplier.tax_number` exist; per-line tax is stored rather than only an order-level total.

Adds: ETA JSON document mapping, digital signing (the HSM/USB-token flow), submission with a
retry queue, UUID/status tracking, and handling of rejections. Non-trivial — mostly integration
work against a specification that changes — but it does not touch the data model, which is why the
per-line tax breakdown is stored now even though nothing currently reads it.

### 6. Mobile Applications

Three plausible apps, in descending order of value:

1. **Owner dashboard** (read-only) — today's numbers from a phone. Small: the reports API already
   exists and is already mobile-responsive on the web, so this is only worth building if a native
   experience is specifically wanted.
2. **Waiter ordering** — the highest operational value. It is the Desktop's order screen minus
   payment, and it would reuse the same event API and offline model. The right technology question
   is whether to reimplement in Flutter/React Native or run the existing PySide6 client on a
   Windows tablet — the latter is nearly free and worth trying first.
3. **Customer ordering / QR menu** — a different product with a different security model (public,
   unauthenticated, abuse-prone). Would need its own rate limiting and order-validation path.

### 7. Advanced Analytics

The data captured in v1 supports more than v1 reports on it:

- **Demand forecasting** from `sales_by_hour` × weekday × weather → staffing and prep guidance
- **Menu engineering** — the classic popularity × margin quadrant, computable today from
  `/reports/products/profitability/`
- **Shrinkage detection** — the variance report already exists; the extension is alerting on
  *patterns* (which staff, which shift, which item) rather than on totals
- **Basket analysis** — what sells with what, driving combo pricing

None of this requires new capture. It is all queries over data the transactional system already
records, which is the argument for capturing `cost_snapshot` and `prep_seconds` now even though
nothing reads them yet.

### 8. Kitchen Capacity & Prep Scheduling

`KitchenTicket.prep_seconds` and `is_late` accumulate from Phase 6 onward. With a few months of
that data: per-station load prediction, sequencing suggestions so a table's items finish together,
and realistic customer wait-time estimates. Building this without the historical data would be
guesswork; the data is being collected precisely so it is available when the feature is worth
doing.

---

## Long Term

### 9. True Multi-Tenant SaaS

Selling this to other cafes. Per §44 the architecture accommodates it; the *product* work is what
does not exist:

**Seam:** `Organization` is the tenant boundary. Every row is scoped. UUID keys mean no
cross-tenant id collisions. The licensing system already models customers, plans, expiry, seat
limits, and renewal — which is most of a subscription model's data layer.

Would need to be built:
- Self-service signup and tenant provisioning
- Billing integration (Paymob / Fawry for Egypt), invoicing, dunning
- Plan/feature gating — the settings registry ([11](11-configuration.md)) is already the hook: a
  plan becomes a set of setting defaults scoped to the organization
- Tenant-aware operations: per-tenant backups, restore, and support tooling
- Onboarding that does not require an engineer

**The scaling decision that would arrive with it:** shared-schema (C2) is right for tens to low
hundreds of tenants. Past that, the honest options are per-tenant database routing or Postgres
partitioning by `organization_id` on the largest tables (`order_event`, `stock_movement`,
`audit_log`). Both are reachable from where we are; neither should be attempted before there is a
real load profile to design against.

### 10. High Availability

AR5 accepts a single VPS. If uptime becomes a business requirement rather than a preference, the
path in order of cost-effectiveness:

1. Postgres streaming replica + automated failover
2. Two API containers behind the reverse proxy (already stateless — sessions are in JWTs, not
   memory)
3. Redis Sentinel
4. Multi-region, only if there is a multi-region customer base

Nothing in the current architecture blocks any of this, because the app tier holds no state. That
was worth ensuring; building it now would not be.

---

## Deliberately Not Doing

Saying no explicitly is as useful as the roadmap, because these are the things that get proposed
mid-project and quietly consume a sprint.

| Idea | Why not |
|---|---|
| Blockchain audit trail | An append-only table with restricted DB grants and off-site backups solves the actual requirement. |
| Microservices | One cafe, a handful of developers. Microservices would add distributed-transaction problems to a system whose hardest requirement is financial consistency. |
| GraphQL | The client set is fixed and known. REST + OpenAPI gives us generated types and cacheability; GraphQL would add query-cost and auth complexity for no gain here. |
| NoSQL primary store | This is a transactional financial system. It wants ACID and foreign keys. |
| Kubernetes | Docker Compose on one VPS matches the load by orders of magnitude. |
| AI menu recommendations in v1 | No data yet. Revisit after a year of real sales — see item 7. |
| Custom ORM / repository layer everywhere | Django's ORM is fine. §62 is right: repositories only where they earn their keep. |
| Offline-capable Web Admin | Management is not time-critical. The complexity belongs in the POS, where it is genuinely needed. |

---

## What Would Change These Plans

Three signals that should trigger re-planning rather than continued execution:

1. **A second branch is signed before Phase 8.** Then branch switching and consolidated reporting
   move up, and the catalog sharing question needs answering earlier than planned.
2. **ETA e-invoicing becomes mandatory for this business size.** It jumps the queue — regulatory
   deadlines are not negotiable and the integration is slow.
3. **The offline path proves unnecessary** — the cafe's connection turns out to be genuinely
   reliable over a month of measurement. Phase 7 would shrink rather than disappear (idempotency
   and the event model stay regardless), but the sync engine's scope could be reduced. Worth
   measuring during Phases 1–3 rather than assuming in either direction.

---

## Closing Note

Per §69, the standard this is built to is a real commercial product, not a demo. The specific way
that shows up in these documents:

- The hard problems — licensing, offline sync, financial integrity — are solved **first**, in
  Phases 3, 5, and 7, before anything cosmetic.
- Every extension above is reachable through a seam that already exists, without a rewrite.
- The risks that cannot be engineered away are **stated** ([09](09-security.md#accepted-risks))
  rather than papered over.
- Nothing is claimed to work until it has been run (§66).

The foundation is what is being reviewed here. Everything on this page is easy once it is right,
and impossible once it is wrong.

---

**Back to:** [Index](README.md)
