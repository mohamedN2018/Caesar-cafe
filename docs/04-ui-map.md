# 04 — UI Map

Covers master prompt sections **F** (web pages) and **G** (desktop screens), plus §13–§19, §55–§58.

---

## F. Web Admin — Page Tree

Sidebar per §14. Every item is gated by a permission code — a user without `inventory.view` does
not see a greyed-out Inventory section, they see no Inventory section at all. Hiding what someone
cannot use keeps the interface honest about what it is for.

```text
📊 لوحة التحكم                          /dashboard

💰 المبيعات                             perm: sales.view
   ├── الطلبات                          /sales/orders
   ├── الفواتير                         /sales/invoices
   ├── المرتجعات                        /sales/refunds
   └── المدفوعات                        /sales/payments

🍽️ نقاط البيع                          perm: pos.view
   ├── الطاولات                         /pos/tables
   ├── الطلبات المفتوحة                 /pos/active-orders
   └── الورديات                         /pos/shifts

👨‍🍳 المطبخ                              perm: kitchen.view
   ├── طلبات المطبخ                     /kitchen/tickets
   ├── حالة التحضير                     /kitchen/status
   └── محطات التحضير                    /kitchen/stations

☕ المنتجات                             perm: catalog.view
   ├── المنتجات                         /catalog/products
   ├── الأقسام                          /catalog/categories
   ├── الإضافات                         /catalog/modifiers
   └── الوصفات                          /catalog/recipes

🧸 صالة الأطفال                         perm: kids.view
   ├── الجلسات النشطة                   /kids/active
   ├── سجل الجلسات                      /kids/sessions
   ├── التعريفات والأسعار               /kids/tariffs
   ├── أولياء الأمور                    /kids/guardians
   ├── سجل الحوادث                      /kids/incidents
   └── تقارير الصالة                    /reports/kids

📦 المخزون                              perm: inventory.view
   ├── الأصناف                          /inventory/items
   ├── الأرصدة                          /inventory/levels
   ├── حركة المخزون                     /inventory/movements
   ├── التسويات والهالك                 /inventory/adjustments
   ├── الجرد                            /inventory/counts
   └── تنبيهات النواقص                  /inventory/alerts

🚚 المشتريات                            perm: purchasing.view
   ├── الموردين                         /purchasing/suppliers
   ├── أوامر الشراء                     /purchasing/orders
   ├── استلام البضاعة                   /purchasing/receipts
   ├── مرتجعات المشتريات                /purchasing/returns
   └── أرصدة الموردين                   /purchasing/balances

👥 العملاء                              perm: customers.view
   ├── العملاء                          /customers
   └── سجل العميل                       /customers/:id

🧑‍💼 الموظفون                            perm: employees.view
   ├── الموظفون                         /staff/employees
   ├── المستخدمون                       /staff/users
   ├── الأدوار                          /staff/roles
   └── الصلاحيات                        /staff/permissions

📈 التقارير                             perm: reports.view
   ├── المبيعات                         /reports/sales
   ├── المنتجات                         /reports/products
   ├── الربحية                          /reports/profitability
   ├── المخزون                          /reports/inventory
   ├── المشتريات                        /reports/purchases
   ├── الموظفون                         /reports/employees
   └── الملخص المالي                    /reports/financial

🏪 الفرع                                perm: branch.view
   ├── بيانات الفرع                     /branch/info
   ├── الطاولات والمناطق                /branch/tables
   ├── الطابعات                         /branch/printers
   ├── طرق الدفع                        /branch/payment-methods
   └── الأجهزة                          /branch/devices

🔑 التراخيص                             perm: licenses.view
   ├── التراخيص                         /licensing/licenses
   ├── الأجهزة المفعّلة                  /licensing/devices
   └── سجل التراخيص                     /licensing/history

⚙️ النظام                               perm: system.manage
   ├── الإعدادات                        /system/settings
   ├── سجل التدقيق                      /system/audit
   └── النسخ الاحتياطي                  /system/backups
```

### Dashboard (§13)

A four-band layout, ordered by how quickly a glance should answer "is today going well?".

```text
┌───────────────────────────────────────────────────────────────────────┐
│  مبيعات اليوم    الطلبات   متوسط الفاتورة   الوردية الحالية            │
│   ١٢,٤٥٠ ج      ٨٧       ١٤٣ ج          مفتوحة · أحمد            │
│   ▲ ١٢٪ أمس     ▲ ٥٪      ▼ ٣٪           منذ ٦ ساعات               │
├───────────────────────────────────────────────────────────────────────┤
│  ⚠ تنبيهات                                                            │
│  • بن محمص: ٢.٤ كجم (الحد الأدنى ٥)      • ترخيص ينتهي خلال ١٢ يوم    │
│  • جهاز Cashier-02 غير متصل منذ ٣ ساعات  • ٣ عمليات لم تتم مزامنتها   │
├───────────────────────────────────────────────────────────────────────┤
│  ┌─── المبيعات خلال اليوم ────────┐  ┌─── الأكثر مبيعاً ─────────┐    │
│  │  [ line: hourly revenue ]      │  │ ١ كابتشينو      ٤٢       │    │
│  │  peak 20:00–23:00 highlighted  │  │ ٢ قهوة تركي     ٣٨       │    │
│  └────────────────────────────────┘  └──────────────────────────┘    │
│  ┌─── حسب القسم ──────┐  ┌─── طرق الدفع ────┐  ┌─ المطبخ الآن ──┐   │
│  │  [ donut ]         │  │ نقدي ٧٢٪ فيزا ٢٨٪│  │ ٤ قيد التحضير  │   │
│  └────────────────────┘  └──────────────────┘  │ ١ متأخر ⚠      │   │
│                                                 └────────────────┘   │
├───────────────────────────────────────────────────────────────────────┤
│  الطلبات المفتوحة  ·  live table, updates over WebSocket              │
└───────────────────────────────────────────────────────────────────────┘
```

Alerts sit **above** the charts deliberately. Charts describe the past; alerts describe something
that needs a decision now. An owner opening this on a phone between meetings should see the
problems without scrolling.

Comparisons are always against the same weekday last week, not yesterday — a Friday compared to a
Thursday tells a cafe owner nothing useful.

### Recurring page patterns

Rather than specifying 40 screens individually, three patterns cover nearly all of them:

**List page** — filter bar (search, date range, status chips, saved views) · bulk-action bar
appearing on selection · sortable table with sticky header · row actions in an overflow menu ·
cursor pagination · empty state that explains the next action rather than saying "no data" ·
skeleton loaders, never a spinner over a blank page.

**Detail / edit** — header with title, status badge, primary action · tabbed body (Details ·
History · Related) · a right drawer for editing so context stays visible behind it · unsaved-change
guard on navigation · an audit-trail tab on every financially meaningful entity.

**Wizard** — for setup, stock counts, and goods receipt. Numbered steps, per-step validation, a
review screen before commit, resumable from a draft.

### Notable non-generic screens

**Table Layout Editor** (`/branch/tables`) — a drag-and-drop canvas with a grid snap. Tables carry
`pos_x`/`pos_y` so the Desktop floor map mirrors the physical room. A cashier finding "table 7"
by matching the shape of the room is meaningfully faster than reading a list.

**Recipe Builder** (`/catalog/recipes`) — search inventory items, set quantity and unit (with live
conversion), see cost recompute per line as you type, and a footer showing cost / price / margin %
for the finished drink. Changing a recipe shows the margin impact **before** saving.

**Stock Variance** (`/reports/inventory/variance`) — theoretical usage (from recipes × units sold)
versus counted usage, per item, with the gap in both quantity and money, sorted by cost of
variance. This is the report that pays for the whole inventory module.

**License Detail** (`/licensing/licenses/:id`) — key (masked, `QSR-7X29-••••-••••-3F1A`), status,
expiry countdown, device seats used as `2 / 3`, per-device last-seen and app version, and the full
license event log. Actions: renew, suspend, revoke, reset a device seat.

### Responsiveness (§57)

Full layout ≥1280px · collapsed sidebar 768–1279px · below 768px the tables become stacked cards
and the sidebar becomes a bottom sheet. The mobile target is the owner checking today's numbers,
not data entry — so Dashboard and Reports are fully mobile-optimized, while the recipe builder and
table editor prompt to use a larger screen rather than degrading into something unusable.

**Installable PWA (C11).** Since the owner monitors the cafe remotely, the Web Admin ships a
manifest and service worker: an app icon on the home screen, push notifications for the alerts in
`notifications.alert_on`, and a cached shell so the dashboard opens instantly on a phone. This gets
a native-feeling owner app without building or maintaining one — the mobile experience is the same
codebase, which is also why it will not rot.

---

## G. Desktop Application — Screen Tree

One binary, three modes selected at activation (`POS` / `KDS` / `BOTH`), because the kitchen screen
and the cashier screen share the same sync engine, local database, and login. Shipping two
installers to maintain would be a self-inflicted wound.

**Which screens a given user sees is driven by `floor.service_mode` and its toggles**
([11](11-configuration.md#q2--cashier-vs-waiter--the-admin-chooses)), not by a build flag. The
waiter screens are always built; in `CASHIER_ONLY` they are simply not rendered. That means the
admin can switch the cafe's service model at any time — including trying waiter terminals for a
week and reverting — without any code change.

```text
CaesarPOS.exe
│
├── 🔐 Activation Gate        ← blocks everything until a valid license exists
│   ├── Welcome / Language
│   ├── Server URL
│   ├── Email + License Key
│   ├── Device Name + Mode
│   └── Activating… → Success / Failure
│
├── 🔑 Login (PIN pad)
│   └── Shift check → Open Shift if none
│
├── 💵 POS  (mode: POS | BOTH)
│   ├── Order Screen           ← the main screen
│   ├── Floor Map
│   ├── Open Orders
│   ├── Payment                ← hidden if floor.waiter_can_take_payment = off
│   ├── Order History (today)
│   └── Reprint
│
├── 🧑‍🍳 Waiter  (shown per floor.service_mode)
│   ├── My Tables              ← filtered by floor.waiter_sees_only_own_tables
│   ├── Take Order             ← the order screen, payment removed
│   ├── Send to Kitchen        ← gated by floor.waiter_can_fire_to_kitchen
│   └── Request Bill           ← notifies the cashier
│
├── 👨‍🍳 Kitchen  (mode: KDS | BOTH)
│   ├── Ticket Board
│   ├── Station Filter
│   └── Recall Served
│
├── 🧸 Kids Area  (if kids.enabled)
│   ├── Live Board             ← capacity, timers, running charges
│   ├── Check In
│   ├── Check Out              ← guardian verification
│   └── Incident Log
│
├── 📦 Stock
│   ├── Quick Count
│   ├── Record Waste
│   └── Receive Goods (simple)
│
├── 🧾 Shift
│   ├── Open / Cash Movements
│   ├── X-Report
│   └── Close → Z-Report
│
└── ⚙️ Settings
    ├── Printers (test print)
    ├── Sync Status
    ├── Device / License Info
    └── About / Version
```

### The Order Screen

```text
┌────────────────────────────────────────────────────────────────────────┐
│ القيصر  │ 🟢 متصل  │ وردية: أحمد  │ ١٤:٣٢  │ ⚙  │ 🔒                  │
├──────────────────────┬─────────────────────────────────────────────────┤
│  الطلب الحالي        │  [قهوة] [باردة] [حلويات] [مأكولات] [إضافات]     │
│  طاولة ٥ · ٤ أفراد   │                                                  │
│                      │  ┌────────┐┌────────┐┌────────┐┌────────┐       │
│  كابتشينو            │  │كابتشينو││ اسبريسو││  لاتيه ││قهوة تركي│      │
│    ٢ × ٦٠     ١٢٠    │  │  ٦٠ ج  ││  ٤٥ ج  ││  ٦٥ ج  ││  ٤٠ ج  │      │
│    بدون سكر          │  └────────┘└────────┘└────────┘└────────┘       │
│  ─────────────────   │  ┌────────┐┌────────┐┌────────┐┌────────┐       │
│  قهوة تركي           │  │ مكياتو ││كورتادو ││أمريكانو││  موكا  │       │
│    ١ × ٤٠      ٤٠    │  │  ٥٥ ج  ││  ٥٠ ج  ││  ٤٥ ج  ││  ٧٠ ج  │      │
│  ─────────────────   │  └────────┘└────────┘└────────┘└────────┘       │
│                      │                                                  │
│  الإجمالي     ١٦٠    │  🔍 [ بحث سريع بالاسم أو الباركود        ]     │
│  خدمة ١٢٪    ١٩.٢٠   │                                                  │
│  ض.ق.م ١٤٪   ٢٥.٠٩   ├─────────────────────────────────────────────────┤
│  ══════════════════  │ [F1 جديد] [F2 بحث] [F3 طاولة] [F4 دفع]         │
│  المطلوب    ٢٠٤.٢٩   │ [F5 تعليق] [F6 خصم] [F7 إرسال للمطبخ]          │
└──────────────────────┴─────────────────────────────────────────────────┘
```

Design rules, in priority order:

1. **Two taps to add a product.** Category → product. No confirmation dialog, no modal — the item
   lands in the cart and the cart animates. A modal per item would cost hours a week at the
   counter.
2. **Modifier sheet only when required.** A product with no required modifier group never
   interrupts. A required group opens a full-height sheet with large targets and a single confirm.
3. **The cart is on the right in RTL** — the natural reading start. This flips correctly, not as a
   blanket mirror of a left-to-right design.
4. **Touch targets ≥ 64×64px.** Assume a fingertip on a glossy screen, not a mouse.
5. **Totals always visible.** Never hidden behind a scroll or a tab.
6. **Optimistic and local.** Every tap writes to SQLite and updates the UI immediately; the network
   is never in the interaction path.
7. **Connection state is always on screen** — 🟢 متصل / 🟡 يزامن (n) / 🔴 غير متصل. Staff must be
   able to tell at a glance, before the manager asks why the kitchen never got the order.

**Keyboard shortcuts (§55):** `F1` new · `F2` search · `F3` table · `F4` pay · `F5` hold · `F6`
discount · `F7` fire to kitchen · `F9` open drawer · `Esc` close modal · `Del` remove selected line
· `+`/`-` quantity · `Ctrl+P` reprint. All remappable from Settings. A barcode scanner acts as
keyboard input into the search field and adds directly on match.

### Payment Screen

```text
┌────────────────────────────────────────────────────────────────┐
│  المطلوب                                          ٢٠٤.٢٩ ج    │
├────────────────────────────────────────────────────────────────┤
│  [ نقدي ]  [ فيزا ]  [ انستاباي ]  [ تقسيم الدفع ]            │
│                                                                 │
│  المبلغ المستلم:  [  ٢٥٠  ]      ┌───┬───┬───┐               │
│                                    │ ٧ │ ٨ │ ٩ │               │
│  الباقي:          ٤٥.٧١ ج         ├───┼───┼───┤               │
│                                    │ ٤ │ ٥ │ ٦ │               │
│  [٢٠٠] [٢٥٠] [٣٠٠] [مضبوط]        ├───┼───┼───┤               │
│                                    │ ١ │ ٢ │ ٣ │               │
│  ☑ طباعة الفاتورة                 └───┴───┴───┘               │
├────────────────────────────────────────────────────────────────┤
│              [  إلغاء  ]        [  تأكيد الدفع  ]              │
└────────────────────────────────────────────────────────────────┘
```

Quick-tender buttons are computed from the amount due (next round 50, next round 100, exact) —
this is the single highest-frequency interaction in a cash cafe and it should almost never require
typing.

### Kitchen Display

```text
┌────────────────────────────────────────────────────────────────────────┐
│  🍳 بار القهوة        [الكل] [جديد ٣] [تحضير ٢] [جاهز ١]      ١٤:٣٥  │
├──────────────┬──────────────┬──────────────┬───────────────────────────┤
│ ● #١٠٢٤      │ ● #١٠٢٥      │ ◐ #١٠٢٣      │ ✓ #١٠٢٢                  │
│ طاولة ٥      │ تيك أواي     │ طاولة ٢      │ طاولة ٨                  │
│ ⏱ ٠:٤٥      │ ⏱ ١:٢٠      │ ⏱ ٤:١٠      │ ⏱ ٦:٣٠ ⚠                 │
│              │              │              │                           │
│ ٢× كابتشينو  │ ١× لاتيه     │ ٣× اسبريسو   │ ٢× موكا                  │
│ ١× تركي      │ ٢× أمريكانو  │              │                           │
│              │              │              │                           │
│ 📝 بدون سكر  │              │ 📝 سريع      │                           │
│              │              │              │                           │
│ [ بدء ]      │ [ بدء ]      │ [ جاهز ]     │ [ تم التقديم ]           │
└──────────────┴──────────────┴──────────────┴───────────────────────────┘
```

- Cards colour by age against `station.target_prep_minutes`: white → amber at 80% → red past
  target. Colour is never the only signal — the ⚠ icon and the elapsed timer carry it too, for
  colour-blind staff and cheap washed-out screens.
- Newest on the right in RTL, oldest on the left, so the ticket most at risk sits where the eye
  lands first.
- Designed for **touch or a single hand**: one large button per card advances its state.
- Full-screen, no window chrome, no way to accidentally minimize into Windows.
- When the socket drops the header turns amber and polling takes over at 15s. The kitchen is told
  its data may be stale rather than silently shown a frozen board.

### Activation Screen

The first thing anyone sees, and the only thing they can reach before activation:

```text
┌───────────────────────────────────────────────────────┐
│                    ☕ القيصر                          │
│              تفعيل النظام لأول مرة                    │
├───────────────────────────────────────────────────────┤
│  عنوان الخادم                                         │
│  [ https://api.caesar-cafe.com              ]        │
│                                                        │
│  البريد الإلكتروني                                     │
│  [                                          ]        │
│                                                        │
│  مفتاح الترخيص                                        │
│  [ QSR- ____ - ____ - ____ - ____           ]        │
│                                                        │
│  اسم الجهاز            وضع التشغيل                    │
│  [ كاشير-١        ]    [ نقطة بيع        ▾ ]         │
│                                                        │
│               [  تفعيل الجهاز  ]                      │
│                                                        │
│  للحصول على مفتاح، تواصل مع مدير النظام.              │
└───────────────────────────────────────────────────────┘
```

The key field auto-formats and auto-advances between segments, accepts a paste of the whole key,
and is case-insensitive. Failures give a specific, actionable Arabic reason — *"عدد الأجهزة
المسموح بها مكتمل (٣/٣)"* — never a generic "activation failed". A cashier at 7am should be able
to read the message and know whether to call the manager or check the wifi.

---

## §58 — First-Run Setup Wizard

Runs once on the Web Admin, on a fresh database:

```text
1  المؤسسة        name, tax number, currency, timezone
2  الفرع          name, code, address, phone
3  المدير         admin account + password
4  الإعدادات      VAT %, service %, business day start ⚠ (A5)
5  الأقسام        starter categories, editable
6  المنتجات       quick-add grid or CSV import
7  الطاولات       count + areas → auto-generates a layout
8  المطبخ         stations + product routing
9  الترخيص        issue the first license → key shown ONCE
10 التفعيل        instructions + the key to type into the Desktop
```

Each step saves independently and the wizard is resumable — an interrupted setup must not force
starting over. Step 9 is the only place the full license key is ever displayed in plaintext; see
[06](06-licensing.md).

---

**Next:** [05 — Permission Matrix](05-permissions.md)
