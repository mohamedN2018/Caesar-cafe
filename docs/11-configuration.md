# 11 — Configuration Framework & Settings Catalog

> Added after review. Answers the directive: **"خلي كل حاجة متغيرة مش ثابتة"** — everything is a
> setting the admin controls, nothing is a constant in the code.

Covers master prompt §35 (web master control) and §36 (desktop settings), extended to the whole
system.

---

## The Rule

**No business value is a literal in the source code.** Not tax rates, not the discount ceiling, not
the kitchen's late threshold, not the number of columns in the POS grid, not the receipt footer.
Every one of them is a registered setting with a documented default that the admin can change from
the Web Admin without a deployment.

The distinction that keeps this from becoming chaos:

| Configurable | Not configurable |
|---|---|
| Values, thresholds, rates, labels, lists, templates, toggles | The order state machine's legal transitions |
| Which stations exist and what routes to them | That stock deducts server-side inside a locked transaction |
| Whether waiters can take payment | That a client-supplied price is never trusted |
| Grace periods, retry intervals, batch sizes | That every financial change writes an audit row |

**Rules that protect money or integrity are code. Everything a business person would reasonably
want to change is configuration.** A setting that could put the system into an inconsistent state
is not a setting — it is a bug waiting for someone to find it.

---

## Architecture

### Registry in code, values in the database

```python
# apps/settings/registry.py
register(
    key="finance.vat_percent",
    label_ar="نسبة ضريبة القيمة المضافة",
    type=Decimal, default=Decimal("14.00"),
    scope=Scope.BRANCH,
    validators=[Range(0, 100)],
    permission="branch.edit_settings",
    group="finance",
    pushes_to_desktop=True,
    help_ar="النسبة المطبقة على الفواتير. التغيير يسري على الطلبات الجديدة فقط.",
)
```

Two halves, deliberately:

- **The definition lives in code** — typed, validated, permission-gated, documented, and covered by
  tests. This is what stops "fully dynamic settings" from becoming an untestable pile of untyped
  strings.
- **The value lives in the database** — so changing it is a click, not a deployment.

Adding a new setting is **one registry entry**. No migration, no API change, no frontend work — the
settings UI renders itself from the registry, grouped and typed. That property is what makes the
"everything is configurable" rule survivable for the developers rather than a permanent tax.

### Scope resolution

```mermaid
graph RL
    D["Device<br/>e.g. this terminal's printer"] --> B
    B["Branch<br/>e.g. VAT, service mode"] --> O
    O["Organization<br/>e.g. currency, timezone"] --> C
    C["Code default<br/>documented in the registry"]

    style D fill:#1d4e89,color:#fff
    style C fill:#3d3d3d,color:#fff
```

Most specific wins. `settings.get("printing.receipt_copies", device=d)` checks the device override,
then the branch, then the organization, then the registry default. A branch can set the VAT rate
without every device needing a row; one terminal can print two copies without affecting the others.

```sql
CREATE TABLE setting_value (
    id          UUID PRIMARY KEY,
    scope_type  TEXT NOT NULL,     -- ORGANIZATION | BRANCH | DEVICE | ROLE
    scope_id    UUID NOT NULL,
    key         TEXT NOT NULL,
    value       JSONB NOT NULL,
    updated_by  UUID,
    updated_at  TIMESTAMPTZ,
    UNIQUE (scope_type, scope_id, key)
);
```

### Propagation to the Desktop

Any setting marked `pushes_to_desktop=True` writes a `change_log` row on the `config` stream when
it changes. The Desktop picks it up on its next pull (≤5 min, or immediately over WebSocket) and
applies it without a restart.

Settings that affect **money** — VAT, service charge, rounding, discount ceilings — carry
`effective_from` semantics: they apply to orders opened **after** the change. An order already open
on a table keeps the rules it was opened under. Otherwise a mid-service tax change silently
rewrites a bill the customer is already looking at.

Every change writes an `AuditLog` row with before/after, actor, and IP. `/system/settings/history/`
answers "who changed the service charge and when" — which is the first question asked when a total
looks wrong.

---

## Answers to the Three Open Questions

### A5 — Business day boundary → **configurable, no fixed assumption**

`finance.business_day_start` (default `04:00`, editable per branch). Every report, shift summary,
and dashboard comparison derives its day boundary from this one setting. Changing it re-derives
reports going forward; historical rollups keep the boundary they were computed under and are
labelled with it, so a change never silently rewrites last month's numbers.

Related, also configurable: `finance.week_start_day` (default Saturday — the Egyptian business
week), `finance.fiscal_year_start_month`.

### Q2 — Cashier vs waiter → **the admin chooses**

`floor.service_mode`, with three modes, switchable at any time:

| Mode | How it works |
|---|---|
| `CASHIER_ONLY` | Waiters carry no device. The cashier enters every order. Floor screens are hidden. |
| `WAITER_TERMINAL` *(default)* | Shared terminals on the floor. A waiter logs in with a PIN, fires an order, logs out. |
| `WAITER_DEVICE` | Each waiter carries their own tablet, stays logged in for the shift. |

Independently toggleable behaviours, so the mode is a starting point rather than a straitjacket:

- `floor.waiter_can_fire_to_kitchen` (default **on**)
- `floor.waiter_can_take_payment` (default **off**)
- `floor.waiter_sees_only_own_tables` (default **on**)
- `floor.waiter_can_transfer_table` (default **on**)
- `floor.waiter_can_apply_discount` (default **off**)
- `floor.require_guest_count` (default off)

**Consequence for the roadmap:** the Floor/Waiter screens are now firmly **in Phase 5**, not
deferred. `CASHIER_ONLY` is implemented as "hide the floor screens", which means the capability
ships once and the admin decides whether it is visible — rather than us guessing the service model
and building the wrong half.

### Q3 — Remote access → **yes, public from Phase 1**

The owner monitors everything over the internet. This is now a requirement, not an option, and it
changes three things:

1. **Public TLS from Phase 1**, not Phase 10. The API and Web Admin are internet-facing from the
   first deployment, so they are hardened from the first deployment rather than retrofitted.
2. **Security posture moves up.** See the revised threats in [09](09-security.md) — MFA for admin
   roles becomes mandatory rather than available, and rate limiting, fail2ban, and alerting move
   into Phase 1–3 instead of Phase 9.
3. **The Web Admin becomes an installable PWA** so the owner gets an app icon and push
   notifications on a phone, without us building a native app.

New settings this creates:

- `security.admin_ip_allowlist` (default empty = any IP) — optional lockdown for admin-only routes
- `security.require_mfa_for_roles` (default `["SUPER_ADMIN", "BRANCH_MANAGER"]`)
- `notifications.owner_daily_digest_time` (default `23:30`)
- `notifications.push_enabled` / `notifications.quiet_hours`
- `notifications.alert_on` — which events reach the owner's phone in real time

---

## Settings Catalog

Every entry is admin-editable. Defaults shown are the registry defaults, not hardcoded constants.

### Organization & Locale — scope: Organization

| Key | Default | Notes |
|---|---|---|
| `org.currency` | `EGP` | Symbol, code, position |
| `org.currency_decimals` | `2` | |
| `org.timezone` | `Africa/Cairo` | Storage stays UTC |
| `org.default_language` | `ar` | `ar` / `en` |
| `org.numeral_system` | `western` | `western` (0-9) / `eastern` (٠-٩) |
| `org.date_format` | `dd/MM/yyyy` | |
| `org.tax_number` | — | Appears on invoices |
| `org.logo` | — | Receipts + Web Admin |

### Finance — scope: Branch

| Key | Default | Notes |
|---|---|---|
| `finance.vat_percent` | `14.00` | |
| `finance.vat_inclusive` | `false` | Whether menu prices include VAT |
| `finance.vat_enabled` | `true` | Turn VAT off entirely |
| `finance.service_percent` | `12.00` | |
| `finance.service_applies_to` | `["DINE_IN"]` | Any subset of order types |
| `finance.service_enabled` | `false` | Off by default |
| `finance.rounding_mode` | `HALF_UP` | |
| `finance.rounding_step` | `0.01` | Set `0.25` to round totals to the nearest quarter pound |
| `finance.business_day_start` | `04:00` | **A5** |
| `finance.week_start_day` | `SATURDAY` | |
| `finance.fiscal_year_start_month` | `1` | |

### Orders — scope: Branch

| Key | Default | Notes |
|---|---|---|
| `orders.default_type` | `DINE_IN` | |
| `orders.enabled_types` | `["DINE_IN","TAKE_AWAY"]` | Enable delivery when ready |
| `orders.number_format` | `{branch}-{device}-{seq}` | Template |
| `orders.void_grace_seconds` | `120` | Free-void window after firing |
| `orders.void_reasons` | list | **Admin-managed list**, required on void |
| `orders.require_void_reason` | `true` | |
| `orders.allow_price_override` | `false` | |
| `orders.allow_negative_stock_sale` | `true` | Selling when theoretical stock is 0 |
| `orders.auto_close_on_payment` | `true` | |
| `orders.max_open_per_device` | `50` | |
| `orders.item_note_max_length` | `200` | |
| `orders.quick_notes` | list | One-tap notes: بدون سكر، سريع، … |

### Discounts — scope: Branch + Role

| Key | Default | Notes |
|---|---|---|
| `discounts.enabled` | `true` | |
| `discounts.reasons` | list | Admin-managed |
| `discounts.require_reason` | `true` | |
| `discounts.allow_line_level` | `true` | |
| `discounts.allow_order_level` | `true` | |
| `discounts.max_percent` | per-role | Cashier 10, Manager 100 — set per role |
| `discounts.max_amount` | per-role | Absolute ceiling |
| `discounts.preset_percentages` | `[5,10,15,20]` | One-tap buttons |

### Payments — scope: Branch

Payment methods themselves are **fully admin-managed rows**, not an enum. Each carries: name (ar/en),
code, icon, whether it opens the drawer, whether it needs a reference, whether it counts as cash for
shift reconciliation, and whether it is active.

| Key | Default | Notes |
|---|---|---|
| `payments.allow_split` | `true` | |
| `payments.allow_partial` | `true` | |
| `payments.quick_tender_mode` | `smart` | `smart` (computed) / `fixed` / `off` |
| `payments.quick_tender_values` | `[50,100,200]` | Used when `fixed` |
| `payments.change_rounding` | `0.01` | |
| `payments.tips_enabled` | `false` | |

### Floor & Service — scope: Branch

| Key | Default | Notes |
|---|---|---|
| `floor.service_mode` | `WAITER_TERMINAL` | **Q2** |
| `floor.waiter_can_fire_to_kitchen` | `true` | |
| `floor.waiter_can_take_payment` | `false` | |
| `floor.waiter_sees_only_own_tables` | `true` | |
| `floor.waiter_can_transfer_table` | `true` | |
| `floor.waiter_can_apply_discount` | `false` | |
| `floor.require_guest_count` | `false` | |
| `floor.auto_cleaning_status` | `true` | Table → CLEANING on close |
| `floor.cleaning_duration_minutes` | `5` | Auto-return to AVAILABLE |
| `floor.reservations_enabled` | `false` | |
| `floor.table_naming_pattern` | `T-{n}` | |

### Kids Area — scope: Branch + Area

Full table and rationale in [12](12-kids-area.md#settings). Capacity, age limits, guardian
verification, socks, tariff defaults, grace, rounding, alerts, tag range, and photo capture (off by
default) are all admin-editable.

### Kitchen — scope: Branch + Station

Stations are admin-managed rows; product→station routing is admin-managed.

| Key | Default | Notes |
|---|---|---|
| `kitchen.target_prep_minutes` | `8` | Per station override |
| `kitchen.warning_threshold_percent` | `80` | Card turns amber |
| `kitchen.late_threshold_percent` | `100` | Card turns red |
| `kitchen.auto_accept` | `false` | Per station |
| `kitchen.print_ticket_mode` | `on_kds_failure` | `always` / `on_kds_failure` / `never` |
| `kitchen.kds_columns` | `4` | Per device |
| `kitchen.kds_sound_on_new` | `true` | |
| `kitchen.allow_recall_minutes` | `30` | How far back a served ticket can be recalled |
| `kitchen.show_prices_on_ticket` | `false` | |

### Inventory — scope: Branch

| Key | Default | Notes |
|---|---|---|
| `inventory.costing_method` | `WEIGHTED_AVG` | `WEIGHTED_AVG` / `FIFO` |
| `inventory.deduct_on` | `PAYMENT` | `FIRE` / `PAYMENT` / `SERVE` |
| `inventory.allow_negative` | `true` | Block the sale or just warn |
| `inventory.low_stock_mode` | `absolute` | `absolute` / `percent_of_reorder` |
| `inventory.waste_reasons` | list | Admin-managed |
| `inventory.require_waste_reason` | `true` | |
| `inventory.count_requires_approval` | `true` | |
| `inventory.count_variance_alert_percent` | `5` | |
| `inventory.default_waste_percent` | `0` | Recipe shrinkage default |

`inventory.deduct_on` is worth a note: deducting at payment is safest (an abandoned order does not
consume stock), deducting at fire is most accurate in real time (the barista used the beans whether
or not the customer paid). Which is right depends on how the cafe operates, so it is a setting.

### Purchasing — scope: Branch

| Key | Default | Notes |
|---|---|---|
| `purchasing.po_approval_threshold` | `0` | `0` = never require approval |
| `purchasing.receive_tolerance_percent` | `10` | Over-receipt allowance |
| `purchasing.allow_price_variance` | `true` | Receive at a different price than ordered |
| `purchasing.price_variance_alert_percent` | `15` | |
| `purchasing.default_payment_terms_days` | `0` | |
| `purchasing.auto_suggest_reorder` | `true` | |

### Printing — scope: Branch + Device

| Key | Default | Notes |
|---|---|---|
| `printing.receipt_printer` | — | Per device |
| `printing.kitchen_printers` | — | Per station |
| `printing.paper_width_mm` | `80` | `58` / `80` |
| `printing.receipt_copies` | `1` | |
| `printing.auto_print_on_payment` | `true` | |
| `printing.header_lines` | list | Free text, per line, admin-editable |
| `printing.footer_lines` | `["شكراً لزيارتكم"]` | |
| `printing.show_logo` | `true` | |
| `printing.show_tax_breakdown` | `true` | |
| `printing.show_cashier_name` | `true` | |
| `printing.show_qr` | `false` | Ready for ETA e-invoicing |
| `printing.reprint_limit` | `3` | Then requires approval |
| `printing.drawer_kick_pin` | `2` | |

### Shifts — scope: Branch

| Key | Default | Notes |
|---|---|---|
| `shifts.required_to_sell` | `true` | |
| `shifts.blind_close` | `true` | Hide expected cash until counted — the anti-fudge setting |
| `shifts.max_duration_hours` | `16` | Warn beyond this |
| `shifts.max_variance` | `50` | Above this, closing needs approval |
| `shifts.require_variance_reason` | `true` | |
| `shifts.cash_movement_reasons` | list | Admin-managed |
| `shifts.max_cash_movement` | per-role | |
| `shifts.auto_print_z_report` | `true` | |

### Licensing — scope: License

| Key | Default | Notes |
|---|---|---|
| `license.offline_grace_hours` | `72` | |
| `license.expiry_policy` | `BLOCK_NEW_ORDERS` | `READ_ONLY` / `GRACE_ONLY` / `BLOCK_NEW_ORDERS` |
| `license.grace_days_after_expiry` | `7` | |
| `license.heartbeat_interval_minutes` | `15` | |
| `license.warn_before_expiry_days` | `14` | |

### Sync — scope: Branch + Device

| Key | Default | Notes |
|---|---|---|
| `sync.push_interval_seconds` | `2` | |
| `sync.push_batch_size` | `50` | |
| `sync.backoff_cap_seconds` | `300` | |
| `sync.pull_interval.catalog` | `60` | Per stream |
| `sync.pull_interval.orders` | `10` | |
| `sync.pull_interval.config` | `300` | |
| `sync.pending_alert_threshold` | `100` | |
| `sync.offline_alert_minutes` | `30` | |

### Security — scope: Organization

| Key | Default | Notes |
|---|---|---|
| `security.access_token_minutes` | `15` | |
| `security.refresh_token_days` | `7` | |
| `security.device_token_minutes` | `60` | |
| `security.pin_length` | `4` | 4–6 |
| `security.pin_lockout_attempts` | `5` | |
| `security.pin_lockout_minutes` | `15` | |
| `security.password_min_length` | `10` | |
| `security.require_mfa_for_roles` | `["SUPER_ADMIN","BRANCH_MANAGER"]` | **Q3** |
| `security.admin_ip_allowlist` | `[]` | Empty = any |
| `security.approval_token_seconds` | `60` | Step-up TTL |
| `security.session_idle_logout_minutes` | `30` | Web only |

### Notifications — scope: Branch + User

| Key | Default | Notes |
|---|---|---|
| `notifications.alert_on` | list | Low stock, license expiry, device offline, sync failure, kitchen delay, large discount, shift variance, failed backup |
| `notifications.channels` | `["in_app"]` | + `email`, `push`, `whatsapp` (future) |
| `notifications.owner_daily_digest_time` | `23:30` | **Q3** |
| `notifications.quiet_hours` | `["01:00","09:00"]` | |
| `notifications.recipients` | per-event | Who gets what |
| `notifications.push_enabled` | `true` | PWA push |

### UI — scope: Device + User

| Key | Default | Notes |
|---|---|---|
| `ui.theme` | `system` | light / dark / system |
| `ui.language` | inherits org | Per device |
| `ui.pos_grid_columns` | `4` | |
| `ui.pos_show_images` | `true` | Off is faster on old hardware |
| `ui.pos_tile_size` | `medium` | |
| `ui.keyboard_shortcuts` | map | Fully remappable |
| `ui.density` | `comfortable` | Web Admin |
| `ui.dashboard_widgets` | list | Which cards, in what order |

---

## Settings UI

Rendered entirely from the registry at `/system/settings`:

```text
┌─────────────────────────────────────────────────────────────────┐
│  الإعدادات                                    🔍 [ بحث ]        │
├──────────────────┬──────────────────────────────────────────────┤
│ ▸ عام            │  المالية                                     │
│ ▸ المالية    ●   │  ─────────────────────────────────────       │
│ ▸ الطلبات        │  نسبة ض.ق.م              [ ١٤.٠٠ ] %        │
│ ▸ الخصومات       │  الأسعار شاملة الضريبة   [ ○──  ]           │
│ ▸ الدفع          │  نسبة الخدمة             [ ١٢.٠٠ ] %        │
│ ▸ الصالة         │  تطبق الخدمة على         [☑ صالة ☐ تيك أواي] │
│ ▸ المطبخ         │  بداية اليوم المحاسبي    [ ٠٤:٠٠ ]          │
│ ▸ المخزون        │    ⓘ الطلبات قبل هذا الوقت تُحسب على اليوم   │
│ ▸ المشتريات      │       السابق في كل التقارير.                 │
│ ▸ الطباعة        │  تقريب الإجمالي          [ ٠.٠١ ▾ ]         │
│ ▸ الورديات       │                                              │
│ ▸ الأمان         │  ● معدّل عن الافتراضي   [ استعادة الافتراضي ]│
│ ▸ التنبيهات      │                                              │
│ ▸ المزامنة       │        [ إلغاء ]      [ حفظ التغييرات ]      │
└──────────────────┴──────────────────────────────────────────────┘
```

Properties that make this usable rather than a wall of switches:

- **Search across all settings** by key, label, or help text — with ~180 settings, browsing alone
  does not scale.
- **A dot marks anything changed from default**, and every setting has a one-click reset. Six months
  in, "what did we actually change?" is answerable at a glance.
- **Inline help in Arabic** explaining the consequence, not restating the label.
- **Scope indicator** — where the value is coming from (org / branch / device) and where the edit
  will be written.
- **Financial settings show a warning** that they apply only to new orders.
- **Change history per setting**, drawn from the audit log.

---

## Guardrails

Making everything configurable introduces one real risk: someone sets VAT to 140% at 8pm on a
Friday. Countermeasures:

1. **Validators in the registry.** Ranges, enums, and cross-field rules are declared and enforced
   server-side. A percentage cannot exceed 100.
2. **A confirmation step for high-impact settings** — anything financial, security-related, or
   licensing-related shows what will change and asks again.
3. **Full audit + one-click revert** to the previous value or the default.
4. **Permission-gated per setting.** `security.*` needs `system.settings`; `finance.*` needs
   `branch.edit_settings`; a branch manager cannot loosen `security.require_mfa_for_roles`.
5. **The defaults are a working configuration.** A fresh install with zero overrides runs a correct
   Egyptian cafe. Configuration is for tuning, never a prerequisite for the system to function.

---

**Back to:** [Index](README.md)
