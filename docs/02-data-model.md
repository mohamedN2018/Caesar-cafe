# 02 — Data Model & ERD

Covers master prompt sections **C** (ERD) and **D** (main models).

One ERD for the whole system would be unreadable, so it is split by bounded context. Cross-context
links are noted in prose.

---

## Global Conventions

Every business table inherits these. They are not repeated in the diagrams below.

```python
class BaseModel(models.Model):
    id          = UUIDField(primary_key=True, default=uuid4, editable=False)
    created_at  = DateTimeField(auto_now_add=True, db_index=True)
    updated_at  = DateTimeField(auto_now=True)
    created_by  = ForeignKey(User, null=True, on_delete=PROTECT, related_name="+")
    updated_by  = ForeignKey(User, null=True, on_delete=PROTECT, related_name="+")

class TenantScopedModel(BaseModel):
    organization = ForeignKey(Organization, on_delete=PROTECT)
    branch       = ForeignKey(Branch, on_delete=PROTECT, db_index=True)
```

**UUID primary keys everywhere.** Non-negotiable for this system: an offline Desktop must mint an
order's identity before it has ever spoken to the server. Sequential integers make that impossible
without a coordination round-trip, which is exactly what we cannot have during a network outage.
UUIDv7 (time-ordered) is used where index locality matters — orders, events, movements — so we get
distributed identity without shredding B-tree insert performance.

**Deletion is soft, or forbidden.** Financial records use `on_delete=PROTECT` and are never
deleted. Master data (products, categories) uses `is_active` flags. A product that has ever been
sold can be deactivated but not removed — deleting it would orphan historical line items and
silently rewrite last quarter's reports.

### Money and quantity precision

| Concept | Type | Rationale |
|---|---|---|
| Prices, totals, payments, balances | `DecimalField(max_digits=12, decimal_places=2)` | EGP has 2 decimals; 12 digits covers 9,999,999,999.99 |
| Inventory quantities | `DecimalField(max_digits=14, decimal_places=3)` | A recipe uses 18g of beans and 150ml of milk — 2 decimals loses grams |
| Unit costs | `DecimalField(max_digits=12, decimal_places=4)` | Cost per gram is fractions of a piaster; rounding at 2dp corrupts COGS |
| Percentages (tax, discount) | `DecimalField(max_digits=5, decimal_places=2)` | 0.00–100.00 |

**`float` appears nowhere near money.** Enforced by a CI check that greps for `FloatField` in
`apps/` and fails the build.

Rounding is `ROUND_HALF_UP`, applied **once**, at the line-total and order-total boundaries — never
mid-calculation. The order of operations is fixed and shared between Desktop and server:

```
line_gross    = round(unit_price × quantity, 2)
line_discount = round(line_gross × discount_pct / 100, 2)
line_net      = line_gross − line_discount
order_net     = Σ line_net
order_discount= round(order_net × order_discount_pct / 100, 2)
taxable       = order_net − order_discount
service       = round(taxable × service_pct / 100, 2)      # dine-in only
tax           = round((taxable + service) × vat_pct / 100, 2)
order_total   = taxable + service + tax
```

Both implementations run against a **shared golden-file test fixture** of ~200 cases (including
the nasty ones: 3-way splits of odd piasters, 100% discounts, tax-exempt lines). If the Desktop
and the server ever disagree by one piaster, CI fails. This fixture is written in Phase 1, before
either implementation exists.

---

## C1. Organizations, Accounts & Authorization

```mermaid
erDiagram
    ORGANIZATION ||--o{ BRANCH : has
    ORGANIZATION ||--o{ USER : employs
    BRANCH ||--o{ SETTING_VALUE : overrides
    BRANCH ||--o{ ROLE_ASSIGNMENT : scopes
    USER ||--o| STAFF_PROFILE : has
    USER ||--o{ ROLE_ASSIGNMENT : granted
    ROLE ||--o{ ROLE_ASSIGNMENT : used_in
    ROLE ||--o{ ROLE_PERMISSION : contains

    ORGANIZATION {
        uuid id PK
        string name_ar
        string name_en
        string tax_number
        string currency "EGP"
        string timezone "Africa/Cairo"
        bool is_active
    }
    BRANCH {
        uuid id PK
        uuid organization_id FK
        string code UK "MB"
        string name_ar
        string name_en
        string phone
        text address
        bool is_active
    }
    SETTING_VALUE {
        uuid id PK
        string scope_type "ORGANIZATION|BRANCH|DEVICE|ROLE"
        uuid scope_id
        string key "finance.vat_percent"
        json value
        uuid updated_by FK
        datetime updated_at
    }
    USER {
        uuid id PK
        uuid organization_id FK
        string email UK
        string phone
        string full_name_ar
        string password_hash
        string pin_hash "POS quick login"
        bool is_active
        bool mfa_enabled
        datetime last_login_at
    }
    STAFF_PROFILE {
        uuid user_id PK,FK
        string employee_code
        string national_id
        date hired_at
        decimal salary
        string job_title
    }
    ROLE {
        uuid id PK
        uuid organization_id FK
        string code UK "CASHIER"
        string name_ar
        bool is_system "not deletable"
    }
    ROLE_PERMISSION {
        uuid id PK
        uuid role_id FK
        string permission_code "orders.discount"
    }
    ROLE_ASSIGNMENT {
        uuid id PK
        uuid user_id FK
        uuid role_id FK
        uuid branch_id FK "null = all branches"
    }
```

**`SETTING_VALUE` replaces a fixed settings table.** A `BRANCH_SETTINGS` model with one column per
option means every new setting is a migration, and the ~180 settings in
[11](11-configuration.md) would make it an unmanageable wide table. Instead: definitions are a typed
registry in code, values are sparse rows here, and resolution walks Device → Branch → Organization →
default. Adding a setting is one registry entry with no schema change, and a branch that has
overridden nothing stores nothing.

**Two credentials per user, deliberately.** `password_hash` is a full Argon2id password used on the
Web Admin. `pin_hash` is a 4–6 digit PIN used for fast Desktop login and re-authorization
(approving a discount, voiding an item). A PIN is weak by construction — so it is only ever
accepted from an **activated device**, is rate-limited per device, locks after 5 failures, and can
never authenticate against the Web Admin. The device credential is what carries the real entropy;
the PIN identifies *which human* is standing at an already-trusted terminal.

---

## C2. Licensing & Devices

```mermaid
erDiagram
    ORGANIZATION ||--o{ LICENSE : owns
    BRANCH ||--o{ LICENSE : activates
    LICENSE ||--o{ DEVICE : authorizes
    LICENSE ||--o{ LICENSE_EVENT : audits
    DEVICE ||--o{ DEVICE_SESSION : issues
    DEVICE ||--o{ INVOICE_BLOCK : reserves

    LICENSE {
        uuid id PK
        uuid organization_id FK
        uuid branch_id FK "nullable until bound"
        string key_hash UK "HMAC-SHA256, indexed"
        string key_prefix "QSR-7X29"
        string key_last4
        string customer_email
        string license_type "TRIAL|MONTHLY|YEARLY|LIFETIME"
        string status "PENDING|ACTIVE|SUSPENDED|EXPIRED|REVOKED"
        datetime starts_at
        datetime expires_at "null = lifetime"
        int max_devices
        int activation_count
        int offline_grace_hours
        json policy "expiry behaviour"
        datetime last_activation_at
        text notes
    }
    DEVICE {
        uuid id PK
        uuid license_id FK
        uuid branch_id FK
        string device_name "Cashier-01"
        string secret_hash "Argon2id"
        string mode "POS|KDS|BOTH"
        string platform
        string app_version
        string fingerprint "advisory only"
        string status "ACTIVE|SUSPENDED|REVOKED"
        datetime first_activated_at
        datetime last_seen_at
        inet last_ip
    }
    DEVICE_SESSION {
        uuid id PK
        uuid device_id FK
        uuid user_id FK "nullable"
        string refresh_token_hash
        datetime issued_at
        datetime expires_at
        datetime revoked_at
    }
    LICENSE_EVENT {
        uuid id PK
        uuid license_id FK
        string event "CREATED|ACTIVATED|SUSPENDED|RENEWED|REVOKED|DEVICE_RESET"
        uuid actor_id FK
        json detail
        datetime created_at
    }
    INVOICE_BLOCK {
        uuid id PK
        uuid branch_id FK
        uuid device_id FK
        bigint range_start
        bigint range_end
        bigint next_unused
        datetime allocated_at
        datetime exhausted_at
    }
```

Detailed in [06 — Licensing & Activation](06-licensing.md). `INVOICE_BLOCK` is explained in
[07 — Sync](07-sync.md#invoice-numbering-under-partition).

---

## C3. Catalog & Recipes

```mermaid
erDiagram
    CATEGORY ||--o{ CATEGORY : parent_of
    CATEGORY ||--o{ PRODUCT : contains
    PRODUCT ||--o{ PRODUCT_VARIANT : has
    PRODUCT ||--o{ PRODUCT_MODIFIER_GROUP : offers
    MODIFIER_GROUP ||--o{ MODIFIER : contains
    MODIFIER_GROUP ||--o{ PRODUCT_MODIFIER_GROUP : linked
    PRODUCT ||--o{ PRICE_HISTORY : tracks
    PRODUCT_VARIANT ||--o| RECIPE : consumes
    RECIPE ||--o{ RECIPE_LINE : contains
    INVENTORY_ITEM ||--o{ RECIPE_LINE : used_in
    STATION ||--o{ PRODUCT : routed_to

    CATEGORY {
        uuid id PK
        uuid branch_id FK
        uuid parent_id FK
        string name_ar
        string name_en
        string color "#hex for POS grid"
        int sort_order
        bool is_active
    }
    PRODUCT {
        uuid id PK
        uuid branch_id FK
        uuid category_id FK
        uuid station_id FK
        string sku UK
        string barcode
        string name_ar
        string name_en
        text description_ar
        image image
        decimal tax_percent
        bool is_tax_exempt
        bool track_inventory
        bool is_active
        bool is_sellable
        int sort_order
    }
    PRODUCT_VARIANT {
        uuid id PK
        uuid product_id FK
        string name_ar "وسط | كبير"
        string sku UK
        decimal price
        decimal cost "computed from recipe"
        bool is_default
        bool is_active
    }
    MODIFIER_GROUP {
        uuid id PK
        uuid branch_id FK
        string name_ar "إضافات"
        int min_select
        int max_select
        bool is_required
    }
    MODIFIER {
        uuid id PK
        uuid modifier_group_id FK
        uuid inventory_item_id FK "nullable"
        string name_ar "شوت اسبريسو زيادة"
        decimal price_delta
        decimal quantity_consumed
        bool is_active
    }
    PRODUCT_MODIFIER_GROUP {
        uuid id PK
        uuid product_id FK
        uuid modifier_group_id FK
        int sort_order
    }
    PRICE_HISTORY {
        uuid id PK
        uuid variant_id FK
        decimal old_price
        decimal new_price
        uuid changed_by FK
        datetime effective_from
    }
    RECIPE {
        uuid id PK
        uuid variant_id FK,UK
        decimal yield_quantity
        text notes
        bool is_active
    }
    RECIPE_LINE {
        uuid id PK
        uuid recipe_id FK
        uuid inventory_item_id FK
        decimal quantity
        uuid unit_id FK
        bool is_optional
        decimal waste_percent
    }
```

**Why variants exist even though the cafe may not use them yet.** Coffee shops add sizes
constantly (وسط / كبير). Retrofitting a variant layer after 3,000 orders reference `product_id`
directly is a painful migration touching every historical line item. A product with one default
variant costs almost nothing today and removes that migration entirely. The POS UI hides the
variant selector when a product has exactly one.

**`waste_percent` on recipe lines** captures real shrinkage — beans lost in grinding, milk left in
the pitcher. Without it, theoretical stock drifts from counted stock and staff stop trusting the
numbers, which is how inventory systems die.

---

## C4. Inventory, Suppliers & Purchasing

```mermaid
erDiagram
    UNIT ||--o{ INVENTORY_ITEM : measured_in
    UNIT ||--o{ UNIT_CONVERSION : converts
    INVENTORY_ITEM ||--|| STOCK_LEVEL : current
    INVENTORY_ITEM ||--o{ STOCK_MOVEMENT : ledger
    INVENTORY_ITEM ||--o{ PO_LINE : ordered
    INVENTORY_ITEM ||--o{ COUNT_LINE : counted
    SUPPLIER ||--o{ PURCHASE_ORDER : receives
    SUPPLIER ||--o{ SUPPLIER_LEDGER : owes
    PURCHASE_ORDER ||--o{ PO_LINE : contains
    PURCHASE_ORDER ||--o{ GOODS_RECEIPT : fulfilled_by
    GOODS_RECEIPT ||--o{ GR_LINE : contains
    GR_LINE ||--o| STOCK_MOVEMENT : generates
    STOCK_COUNT ||--o{ COUNT_LINE : contains

    UNIT {
        uuid id PK
        string code "KG|G|L|ML|PCS"
        string name_ar
        int decimal_places
    }
    UNIT_CONVERSION {
        uuid id PK
        uuid from_unit_id FK
        uuid to_unit_id FK
        decimal factor "1 KG = 1000 G"
    }
    INVENTORY_ITEM {
        uuid id PK
        uuid branch_id FK
        string code UK
        string name_ar
        string item_type "RAW|CONSUMABLE|PACKAGING|FINISHED"
        uuid base_unit_id FK
        uuid default_supplier_id FK
        decimal minimum_stock
        decimal reorder_level
        decimal reorder_quantity
        string costing_method "WEIGHTED_AVG"
        bool is_active
    }
    STOCK_LEVEL {
        uuid item_id PK,FK
        decimal quantity_on_hand
        decimal quantity_reserved
        decimal weighted_avg_cost
        decimal total_value
        datetime last_movement_at
    }
    STOCK_MOVEMENT {
        uuid id PK
        uuid branch_id FK
        uuid item_id FK
        string movement_type "PURCHASE|SALE|WASTE|ADJUSTMENT|RETURN|TRANSFER|OPENING|COUNT"
        decimal quantity_delta "signed"
        decimal unit_cost
        decimal balance_after "snapshot"
        string ref_type "polymorphic"
        uuid ref_id
        uuid user_id FK
        uuid device_id FK
        text reason
        datetime occurred_at
    }
    SUPPLIER {
        uuid id PK
        uuid branch_id FK
        string name
        string phone
        string email
        text address
        string tax_number
        int payment_terms_days
        decimal current_balance
        bool is_active
    }
    SUPPLIER_LEDGER {
        uuid id PK
        uuid supplier_id FK
        string entry_type "INVOICE|PAYMENT|RETURN|ADJUSTMENT"
        decimal amount "signed"
        decimal balance_after
        string ref_type
        uuid ref_id
        datetime occurred_at
    }
    PURCHASE_ORDER {
        uuid id PK
        uuid branch_id FK
        uuid supplier_id FK
        string po_number UK
        string status "DRAFT|SUBMITTED|PARTIAL|RECEIVED|CANCELLED"
        date expected_date
        decimal subtotal
        decimal tax_total
        decimal grand_total
        text notes
    }
    PO_LINE {
        uuid id PK
        uuid po_id FK
        uuid item_id FK
        decimal quantity_ordered
        decimal quantity_received
        uuid unit_id FK
        decimal unit_price
        decimal line_total
    }
    GOODS_RECEIPT {
        uuid id PK
        uuid po_id FK
        uuid supplier_id FK
        string grn_number UK
        string supplier_invoice_no
        date received_date
        decimal grand_total
        uuid received_by FK
    }
    GR_LINE {
        uuid id PK
        uuid receipt_id FK
        uuid po_line_id FK
        uuid item_id FK
        decimal quantity_received
        decimal unit_cost
        date expiry_date
    }
    STOCK_COUNT {
        uuid id PK
        uuid branch_id FK
        string status "DRAFT|COUNTING|REVIEW|POSTED"
        datetime counted_at
        uuid posted_by FK
    }
    COUNT_LINE {
        uuid id PK
        uuid count_id FK
        uuid item_id FK
        decimal system_quantity
        decimal counted_quantity
        decimal variance
        text reason
    }
```

### The ledger rule

`STOCK_LEVEL` is a **projection**, never the truth. Truth is the ordered sequence of
`STOCK_MOVEMENT` rows. Any code path that changes stock does so by appending a movement inside the
same transaction that updates the level:

```python
@transaction.atomic
def apply_movement(item, delta, movement_type, ref, actor, unit_cost=None):
    level = StockLevel.objects.select_for_update().get(item=item)   # row lock
    level.quantity_on_hand += delta
    if delta > 0 and unit_cost is not None:
        level.weighted_avg_cost = weighted_average(level, delta, unit_cost)
    level.save()
    return StockMovement.objects.create(
        item=item, quantity_delta=delta, movement_type=movement_type,
        balance_after=level.quantity_on_hand,
        unit_cost=unit_cost or level.weighted_avg_cost,
        ref_type=ref.__class__.__name__, ref_id=ref.pk, user=actor,
    )
```

`select_for_update()` is what makes concurrent sales of the same product safe. Without it, two
simultaneous cappuccino sales both read 500g of beans and both write 482g — losing 18g silently,
every time it races. A nightly Celery task recomputes each item's level by replaying its movements
and alarms on any drift; that reconciliation is how we find out if a code path ever bypassed
`apply_movement`.

**`quantity_reserved`** covers items on open (unpaid) orders, so the low-stock alert reflects what
is actually committed rather than what has been paid for.

### Purchasing rule (§25)

A `PURCHASE_ORDER` moves no stock. Only a `GOODS_RECEIPT` does. This separation matters because a
PO is an intention and a GRN is a fact, and cafes routinely receive partial deliveries at prices
that differ from what was ordered. Receiving at the *actual* invoiced cost is what keeps
weighted-average cost — and therefore gross profit — honest.

---

## C5. Orders, Payments & Shifts

```mermaid
erDiagram
    AREA ||--o{ TABLE : contains
    TABLE ||--o{ TABLE_SESSION : hosts
    TABLE_SESSION ||--o{ ORDER : groups
    ORDER ||--o{ ORDER_EVENT : sourced_from
    ORDER ||--o{ ORDER_ITEM : projects
    ORDER_ITEM ||--o{ ORDER_ITEM_MODIFIER : has
    ORDER ||--o{ PAYMENT : settles
    ORDER ||--o| INVOICE : issues
    ORDER ||--o{ KITCHEN_TICKET : dispatches
    ORDER ||--o{ REFUND : reverses
    SHIFT ||--o{ ORDER : during
    SHIFT ||--o{ CASH_MOVEMENT : records
    PAYMENT_METHOD ||--o{ PAYMENT : typed_by
    CUSTOMER ||--o{ ORDER : places

    AREA {
        uuid id PK
        uuid branch_id FK
        string name_ar "الصالة | التراس"
        int sort_order
    }
    TABLE {
        uuid id PK
        uuid area_id FK
        string number "T-05"
        int seats
        string status "AVAILABLE|OCCUPIED|RESERVED|CLEANING"
        int pos_x
        int pos_y
        bool is_active
    }
    TABLE_SESSION {
        uuid id PK
        uuid table_id FK
        datetime opened_at
        datetime closed_at
        int guest_count
        uuid opened_by FK
    }
    ORDER {
        uuid id PK "client-minted UUIDv7"
        uuid branch_id FK
        uuid device_id FK
        uuid shift_id FK
        uuid table_session_id FK
        uuid customer_id FK
        string local_number "MB-01-0042"
        string order_type "DINE_IN|TAKE_AWAY|DELIVERY"
        string status "state machine"
        decimal subtotal
        decimal discount_total
        decimal service_total
        decimal tax_total
        decimal grand_total
        decimal paid_total
        uuid opened_by FK
        datetime opened_at
        datetime closed_at
        bigint server_seq "change cursor"
    }
    ORDER_EVENT {
        uuid id PK "client-minted, idempotency key"
        uuid order_id FK
        int sequence "per-order, gapless"
        string event_type
        json payload
        uuid device_id FK
        uuid actor_id FK
        datetime occurred_at "client clock"
        datetime recorded_at "server clock"
    }
    ORDER_ITEM {
        uuid id PK
        uuid order_id FK
        uuid variant_id FK
        string name_snapshot "immutable"
        decimal unit_price_snapshot
        decimal quantity
        decimal discount_percent
        decimal line_total
        decimal tax_amount
        decimal cost_snapshot
        string status "ACTIVE|VOIDED"
        text note
        uuid station_id FK
    }
    ORDER_ITEM_MODIFIER {
        uuid id PK
        uuid order_item_id FK
        uuid modifier_id FK
        string name_snapshot
        decimal price_delta_snapshot
        decimal quantity
    }
    PAYMENT_METHOD {
        uuid id PK
        uuid branch_id FK
        string code "CASH|CARD|INSTAPAY"
        string name_ar
        bool opens_drawer
        bool requires_reference
        bool is_active
    }
    PAYMENT {
        uuid id PK
        uuid order_id FK
        uuid method_id FK
        uuid shift_id FK
        decimal amount
        decimal tendered
        decimal change_given
        string reference
        string idempotency_key UK
        uuid received_by FK
        datetime paid_at
    }
    INVOICE {
        uuid id PK
        uuid order_id FK,UK
        bigint invoice_number UK "from block"
        string serial "MB-2026-000123"
        datetime issued_at
        json snapshot "immutable receipt"
    }
    REFUND {
        uuid id PK
        uuid order_id FK
        uuid original_payment_id FK
        decimal amount
        string reason
        uuid approved_by FK
        datetime refunded_at
    }
    CUSTOMER {
        uuid id PK
        uuid branch_id FK
        string name
        string phone UK
        text address
        int visit_count
        decimal lifetime_value
    }
    SHIFT {
        uuid id PK
        uuid branch_id FK
        uuid device_id FK
        uuid user_id FK
        string status "OPEN|CLOSING|CLOSED"
        decimal opening_cash
        decimal expected_cash
        decimal counted_cash
        decimal variance
        datetime opened_at
        datetime closed_at
        json z_report
    }
    CASH_MOVEMENT {
        uuid id PK
        uuid shift_id FK
        string movement_type "IN|OUT|EXPENSE|DROP"
        decimal amount
        string reason
        uuid user_id FK
        datetime occurred_at
    }
```

### Snapshot columns are not denormalization sloppiness

`name_snapshot`, `unit_price_snapshot`, `cost_snapshot` on `ORDER_ITEM` exist because a receipt is
a legal record of what was sold at what price. If the manager raises the cappuccino price on
Tuesday, Monday's receipt must still print Monday's price, and Monday's gross-profit report must
still use Monday's cost. Joining live to `PRODUCT_VARIANT` would silently rewrite history every
time a price changes. `INVOICE.snapshot` goes further and freezes the entire rendered receipt as
JSON, so a reprint two years later is byte-identical even if the product was long since deleted.

### Order state machine (§50)

```mermaid
stateDiagram-v2
    [*] --> DRAFT
    DRAFT --> OPEN : confirm
    DRAFT --> CANCELLED : abandon
    OPEN --> IN_KITCHEN : send to kitchen
    OPEN --> OPEN : add / void item
    IN_KITCHEN --> OPEN : add more items
    IN_KITCHEN --> READY : all tickets ready
    READY --> SERVED : delivered to table
    OPEN --> PAID : settle
    SERVED --> PAID : settle
    READY --> PAID : settle
    OPEN --> CANCELLED : void (authorized)
    IN_KITCHEN --> CANCELLED : void (authorized)
    PAID --> REFUNDED : refund (authorized)
    PAID --> [*]
    REFUNDED --> [*]
    CANCELLED --> [*]
```

Transitions live in one table, checked in one place, on the server:

```python
ALLOWED = {
    "DRAFT":      {"OPEN", "CANCELLED"},
    "OPEN":       {"IN_KITCHEN", "PAID", "CANCELLED"},
    "IN_KITCHEN": {"OPEN", "READY", "PAID", "CANCELLED"},
    "READY":      {"SERVED", "PAID", "CANCELLED"},
    "SERVED":     {"PAID"},
    "PAID":       {"REFUNDED"},
    "CANCELLED":  set(),
    "REFUNDED":   set(),
}
```

An invalid transition raises `InvalidStateTransition` and returns HTTP 409 with the current state,
so the Desktop can reconcile rather than retry blindly. Terminal states have no outbound edges —
`PAID → OPEN` is unreachable by construction, which is the point. Reopening a paid order is not an
edit; it is a refund followed by a new order, and it leaves two auditable records.

---

## C6. Kitchen

```mermaid
erDiagram
    STATION ||--o{ KITCHEN_TICKET : receives
    STATION ||--o{ PRODUCT : routes
    ORDER ||--o{ KITCHEN_TICKET : dispatches
    KITCHEN_TICKET ||--o{ TICKET_LINE : contains
    ORDER_ITEM ||--o| TICKET_LINE : appears_as

    STATION {
        uuid id PK
        uuid branch_id FK
        string code "COFFEE|HOT|COLD|DESSERT"
        string name_ar "بار القهوة"
        uuid printer_id FK
        bool auto_accept
        int target_prep_minutes
        int sort_order
        bool is_active
    }
    KITCHEN_TICKET {
        uuid id PK
        uuid order_id FK
        uuid station_id FK
        int ticket_number "per shift"
        string status "NEW|ACCEPTED|PREPARING|READY|SERVED|CANCELLED"
        datetime created_at
        datetime accepted_at
        datetime ready_at
        int prep_seconds "computed"
        bool is_late
        bool printed
    }
    TICKET_LINE {
        uuid id PK
        uuid ticket_id FK
        uuid order_item_id FK
        string name_snapshot
        decimal quantity
        json modifiers_snapshot
        text note
        string status
    }
```

**Routing.** When an order fires, its active items are grouped by `product.station_id` and one
ticket is created per station. A cappuccino and a slice of cake on the same order become two
tickets — coffee bar and dessert — because they are prepared by different people in different
places. The order is `READY` only when every one of its tickets is `READY`.

`prep_seconds` and `is_late` (measured against `station.target_prep_minutes`) are the raw material
for the kitchen-performance report and the "Kitchen Delay" notification, and they cost nothing to
capture at the moment the status changes.

---

## C7. Sync & Audit

```mermaid
erDiagram
    DEVICE ||--o{ SYNC_OPERATION : submits
    BRANCH ||--o{ CHANGE_LOG : emits
    DEVICE ||--o{ SYNC_CURSOR : tracks
    USER ||--o{ AUDIT_LOG : performs

    SYNC_OPERATION {
        uuid id PK
        uuid op_uuid UK "client idempotency key"
        uuid device_id FK
        uuid branch_id FK
        string entity_type
        uuid entity_id
        string operation
        json payload
        string status "APPLIED|REJECTED|CONFLICT"
        json result
        text error_code
        datetime received_at
        datetime applied_at
    }
    CHANGE_LOG {
        bigserial seq PK "monotonic cursor"
        uuid branch_id FK
        string entity_type
        uuid entity_id
        string operation "UPSERT|DELETE"
        json payload
        datetime created_at
    }
    SYNC_CURSOR {
        uuid id PK
        uuid device_id FK
        string stream "catalog|floor|orders|config"
        bigint last_seq
        datetime last_pull_at
    }
    AUDIT_LOG {
        uuid id PK
        uuid organization_id FK
        uuid branch_id FK
        uuid actor_id FK
        uuid device_id FK
        string action "orders.void"
        string entity_type
        uuid entity_id
        json before
        json after
        inet ip_address
        string user_agent
        datetime created_at
    }
```

Mechanics in [07 — Sync](07-sync.md).

**Audit volume.** `AUDIT_LOG` only records *sensitive* actions — price changes, voids, discounts,
refunds, stock adjustments, permission changes, license operations. Logging every read would bury
the signal and balloon the table. `CHANGE_LOG` is separately pruned by Celery once every active
device's cursor has advanced past a row and 30 days have elapsed.

---

## Index Strategy

The queries that will actually be slow, and the indexes that fix them:

| Query | Index |
|---|---|
| Today's orders for a branch | `(branch_id, opened_at DESC)` partial `WHERE status != 'CANCELLED'` |
| Open orders on the floor | partial `(branch_id, status) WHERE status IN ('OPEN','IN_KITCHEN','READY','SERVED')` |
| Sync pull by cursor | `(branch_id, seq)` on `change_log` — this is the hot path, hit every few seconds per device |
| Idempotency lookup | `UNIQUE (op_uuid)` on `sync_operation` |
| Product grid in POS | `(branch_id, category_id, sort_order) WHERE is_active` |
| Stock ledger for an item | `(item_id, occurred_at DESC)` |
| Top products report | `(branch_id, variant_id)` on `order_item` + a materialized daily rollup |
| License activation | `UNIQUE (key_hash)` — single indexed lookup, no table scan |

Reports do **not** run against the transactional tables at scale. A Celery Beat job materializes
`daily_sales_summary`, `daily_product_summary`, and `daily_inventory_valuation` after the business
day closes (A5). Ad-hoc date ranges read the rollups and only touch raw tables for the current,
still-open day.

---

**Next:** [03 — API Map](03-api-map.md)
