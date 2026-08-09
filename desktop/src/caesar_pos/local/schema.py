"""
The local schema (docs/07 §52).

Versioned by a plain integer in `PRAGMA user_version`. Migrations are a list of
SQL scripts applied in order — no framework, because a POS that fails to start
because its migration library disagreed with itself is a cafe that cannot sell.

Every `m_` table stores the server payload verbatim in a `payload` JSON column
ALONGSIDE the few columns the UI actually filters on. That looks redundant and
is not: the server may add a field the shipped client does not know about, and a
mirror that dropped it would silently lose data on the next full re-pull.
"""

from __future__ import annotations

SCHEMA_VERSION = 1

#: Applied in order. Append only — never edit a script that has shipped, because
#: some terminal out there has already run it.
MIGRATIONS: list[str] = [
    # ── 1 ────────────────────────────────────────────────────────────────────
    """
    -- ══ MIRROR (pull-only, server-authoritative) ══════════════════════════

    CREATE TABLE m_categories (
        id          TEXT PRIMARY KEY,
        parent_id   TEXT,
        name_ar     TEXT NOT NULL,
        sort_order  INTEGER NOT NULL DEFAULT 0,
        is_active   INTEGER NOT NULL DEFAULT 1,
        payload     TEXT NOT NULL
    );

    CREATE TABLE m_products (
        id           TEXT PRIMARY KEY,
        category_id  TEXT,
        station_id   TEXT,
        sku          TEXT,
        name_ar      TEXT NOT NULL,
        is_sellable  INTEGER NOT NULL DEFAULT 1,
        sort_order   INTEGER NOT NULL DEFAULT 0,
        is_active    INTEGER NOT NULL DEFAULT 1,
        payload      TEXT NOT NULL
    );
    CREATE INDEX idx_m_products_grid ON m_products (category_id, sort_order);

    CREATE TABLE m_variants (
        id          TEXT PRIMARY KEY,
        product_id  TEXT NOT NULL,
        name_ar     TEXT,
        sku         TEXT,
        -- TEXT, not REAL. A price is a Decimal; SQLite REAL would reintroduce
        -- the imprecision the whole system avoids.
        price       TEXT NOT NULL,
        cost        TEXT NOT NULL DEFAULT '0',
        is_default  INTEGER NOT NULL DEFAULT 0,
        -- The size chooser's order. "وسط" before "كبير" is the admin's decision,
        -- and sorting it alphabetically would reverse it.
        sort_order  INTEGER NOT NULL DEFAULT 0,
        is_active   INTEGER NOT NULL DEFAULT 1,
        payload     TEXT NOT NULL
    );
    CREATE INDEX idx_m_variants_product ON m_variants (product_id);

    CREATE TABLE m_modifier_groups (
        id       TEXT PRIMARY KEY,
        name_ar  TEXT NOT NULL,
        payload  TEXT NOT NULL
    );

    CREATE TABLE m_modifiers (
        id           TEXT PRIMARY KEY,
        group_id     TEXT NOT NULL,
        name_ar      TEXT NOT NULL,
        price_delta  TEXT NOT NULL DEFAULT '0',
        is_active    INTEGER NOT NULL DEFAULT 1,
        payload      TEXT NOT NULL
    );

    CREATE TABLE m_areas (
        id       TEXT PRIMARY KEY,
        name_ar  TEXT NOT NULL,
        payload  TEXT NOT NULL
    );

    CREATE TABLE m_tables (
        id       TEXT PRIMARY KEY,
        area_id  TEXT NOT NULL,
        number   TEXT NOT NULL,
        seats    INTEGER NOT NULL DEFAULT 4,
        status   TEXT NOT NULL DEFAULT 'AVAILABLE',
        -- The admin's drag-and-drop canvas coordinates. Without these the floor
        -- map falls back to a flow layout, and a waiter loses the one thing that
        -- makes a map faster than a list: the screen matching the room.
        pos_x    INTEGER NOT NULL DEFAULT 0,
        pos_y    INTEGER NOT NULL DEFAULT 0,
        -- The actual furniture. A round two-top and a rectangular eight-top drawn
        -- as identical squares is a map of a room nobody works in.
        shape    TEXT NOT NULL DEFAULT 'SQUARE',
        span_x   INTEGER NOT NULL DEFAULT 1,
        span_y   INTEGER NOT NULL DEFAULT 1,
        rotation INTEGER NOT NULL DEFAULT 0,
        payload  TEXT NOT NULL
    );

    -- The branch's printers, defined once in the Web Admin instead of typed
    -- into every terminal. `device_path` may be overridden locally, because a
    -- serial port is a property of a machine and not of a cafe.
    CREATE TABLE m_printers (
        id             TEXT PRIMARY KEY,
        name_ar        TEXT NOT NULL,
        code           TEXT NOT NULL,
        kind           TEXT NOT NULL DEFAULT 'RECEIPT',
        connection     TEXT NOT NULL DEFAULT 'NETWORK',
        host           TEXT NOT NULL DEFAULT '',
        port           INTEGER NOT NULL DEFAULT 9100,
        device_path    TEXT NOT NULL DEFAULT '',
        paper_width_mm INTEGER NOT NULL DEFAULT 80,
        dots           INTEGER NOT NULL DEFAULT 576,
        copies         INTEGER NOT NULL DEFAULT 1,
        cut_after      INTEGER NOT NULL DEFAULT 1,
        is_default     INTEGER NOT NULL DEFAULT 0,
        is_active      INTEGER NOT NULL DEFAULT 1,
        payload        TEXT NOT NULL
    );

    -- This terminal's own binding for a printer whose port differs here. Local
    -- (`l_`) rather than mirrored, because it is the one printer fact the
    -- server cannot know.
    CREATE TABLE l_printer_bindings (
        printer_id   TEXT PRIMARY KEY,
        device_path  TEXT NOT NULL,
        bound_at     TEXT NOT NULL
    );

    CREATE TABLE m_stations (
        id       TEXT PRIMARY KEY,
        code     TEXT NOT NULL,
        name_ar  TEXT NOT NULL,
        payload  TEXT NOT NULL
    );

    CREATE TABLE m_payment_methods (
        id              TEXT PRIMARY KEY,
        code            TEXT NOT NULL,
        name_ar         TEXT NOT NULL,
        counts_as_cash  INTEGER NOT NULL DEFAULT 0,
        is_active       INTEGER NOT NULL DEFAULT 1,
        payload         TEXT NOT NULL
    );

    CREATE TABLE m_users (
        id            TEXT PRIMARY KEY,
        email         TEXT,
        full_name_ar  TEXT,
        -- A HASH. It is what lets a manager's step-up PIN be verified during an
        -- outage. Nothing here can mint a session.
        pin_hash      TEXT,
        is_active     INTEGER NOT NULL DEFAULT 1,
        payload       TEXT NOT NULL
    );

    CREATE TABLE m_permissions (
        id           TEXT PRIMARY KEY,
        user_id      TEXT NOT NULL,
        role_code    TEXT,
        permissions  TEXT NOT NULL,
        payload      TEXT NOT NULL
    );
    CREATE INDEX idx_m_permissions_user ON m_permissions (user_id);

    CREATE TABLE m_settings (
        key      TEXT PRIMARY KEY,
        value    TEXT NOT NULL,
        payload  TEXT NOT NULL
    );

    CREATE TABLE m_kids_areas (
        id       TEXT PRIMARY KEY,
        name_ar  TEXT NOT NULL,
        payload  TEXT NOT NULL
    );

    CREATE TABLE m_kids_tariffs (
        id       TEXT PRIMARY KEY,
        area_id  TEXT NOT NULL,
        name_ar  TEXT NOT NULL,
        payload  TEXT NOT NULL
    );

    -- Where each stream has been pulled up to. One row per stream.
    CREATE TABLE m_sync_meta (
        stream       TEXT PRIMARY KEY,
        cursor       INTEGER NOT NULL DEFAULT 0,
        last_pull_at TEXT,
        last_error   TEXT
    );

    -- ══ LOCAL (write here first, then push) ═══════════════════════════════

    CREATE TABLE l_orders (
        id            TEXT PRIMARY KEY,
        local_number  TEXT NOT NULL,
        order_type    TEXT NOT NULL DEFAULT 'DINE_IN',
        status        TEXT NOT NULL DEFAULT 'OPEN',
        table_id      TEXT,
        -- How many people are actually at the table. `m_tables.seats` is the
        -- furniture; this is the party, and only this one tells a waiter that a
        -- six-top still has four chairs free.
        guest_count   INTEGER NOT NULL DEFAULT 0,
        shift_id      TEXT,
        subtotal      TEXT NOT NULL DEFAULT '0.00',
        discount_total TEXT NOT NULL DEFAULT '0.00',
        service_total TEXT NOT NULL DEFAULT '0.00',
        tax_total     TEXT NOT NULL DEFAULT '0.00',
        grand_total   TEXT NOT NULL DEFAULT '0.00',
        paid_total    TEXT NOT NULL DEFAULT '0.00',
        -- Snapshotted at open time, exactly as the server does. A mid-service
        -- VAT change must not rewrite a bill the customer is looking at.
        vat_percent      TEXT NOT NULL DEFAULT '0',
        service_percent  TEXT NOT NULL DEFAULT '0',
        vat_inclusive    INTEGER NOT NULL DEFAULT 0,
        rounding_step    TEXT NOT NULL DEFAULT '0.01',
        discount_percent TEXT NOT NULL DEFAULT '0',
        opened_at     TEXT NOT NULL,
        closed_at     TEXT,
        synced_at     TEXT
    );
    CREATE INDEX idx_l_orders_open ON l_orders (status, opened_at);

    CREATE TABLE l_order_events (
        id           TEXT PRIMARY KEY,
        order_id     TEXT NOT NULL,
        sequence     INTEGER NOT NULL,
        event_type   TEXT NOT NULL,
        payload      TEXT NOT NULL,
        occurred_at  TEXT NOT NULL,
        actor_id     TEXT,
        UNIQUE (order_id, sequence)
    );

    CREATE TABLE l_order_items (
        line_id              TEXT PRIMARY KEY,
        order_id             TEXT NOT NULL,
        variant_id           TEXT NOT NULL,
        station_id           TEXT,
        name_snapshot        TEXT NOT NULL,
        unit_price_snapshot  TEXT NOT NULL,
        cost_snapshot        TEXT NOT NULL DEFAULT '0',
        tax_exempt_snapshot  INTEGER NOT NULL DEFAULT 0,
        quantity             TEXT NOT NULL DEFAULT '1',
        discount_percent     TEXT NOT NULL DEFAULT '0',
        -- NULL means "no override", which is why it is not defaulted to '0':
        -- zero is a real price here (a comped item) and the two must not blur.
        price_override       TEXT,
        price_override_reason TEXT NOT NULL DEFAULT '',
        line_total           TEXT NOT NULL DEFAULT '0.00',
        modifiers            TEXT NOT NULL DEFAULT '[]',
        note                 TEXT NOT NULL DEFAULT '',
        status               TEXT NOT NULL DEFAULT 'ACTIVE',
        fired_at             TEXT
    );
    CREATE INDEX idx_l_items_order ON l_order_items (order_id);

    CREATE TABLE l_payments (
        id               TEXT PRIMARY KEY,
        order_id         TEXT NOT NULL,
        method_id        TEXT NOT NULL,
        amount           TEXT NOT NULL,
        tendered         TEXT,
        change_given     TEXT NOT NULL DEFAULT '0.00',
        reference        TEXT NOT NULL DEFAULT '',
        idempotency_key  TEXT NOT NULL UNIQUE,
        -- Which drawer this money went into. Without it the terminal cannot
        -- compute its own Z-report, and a cashier counting out during an outage
        -- has nothing to count against.
        shift_id         TEXT,
        taken_at         TEXT NOT NULL
    );

    CREATE TABLE l_shifts (
        id            TEXT PRIMARY KEY,
        user_id       TEXT,
        opening_cash  TEXT NOT NULL DEFAULT '0.00',
        counted_cash  TEXT,
        status        TEXT NOT NULL DEFAULT 'OPEN',
        opened_at     TEXT NOT NULL,
        closed_at     TEXT
    );

    CREATE TABLE l_cash_movements (
        id             TEXT PRIMARY KEY,
        shift_id       TEXT NOT NULL,
        movement_type  TEXT NOT NULL,
        amount         TEXT NOT NULL,
        reason         TEXT NOT NULL DEFAULT '',
        occurred_at    TEXT NOT NULL
    );

    CREATE TABLE l_play_sessions (
        id             TEXT PRIMARY KEY,
        area_id        TEXT NOT NULL,
        tariff_id      TEXT NOT NULL,
        child_name     TEXT NOT NULL,
        guardian_name  TEXT NOT NULL,
        guardian_phone TEXT NOT NULL DEFAULT '',
        -- Allergies and conditions. Mirrored onto the SESSION rather than read
        -- through a child record, because the board has to show them while
        -- offline and with no network round-trip — an allergy that needs a
        -- lookup is an allergy nobody reads.
        medical_notes  TEXT NOT NULL DEFAULT '',
        age_months     INTEGER,
        tag_number     TEXT NOT NULL,
        status         TEXT NOT NULL DEFAULT 'ACTIVE',
        checked_in_at  TEXT NOT NULL,
        checked_out_at TEXT,
        tariff_snapshot TEXT NOT NULL DEFAULT '{}',
        order_id       TEXT
    );

    CREATE TABLE l_waste_events (
        id           TEXT PRIMARY KEY,
        item_id      TEXT NOT NULL,
        quantity     TEXT NOT NULL,
        reason       TEXT NOT NULL DEFAULT '',
        occurred_at  TEXT NOT NULL
    );

    -- ══ MACHINERY ═════════════════════════════════════════════════════════

    -- The outbox. Written in the SAME transaction as the data it describes —
    -- see local/outbox.py for why that is the crux of the whole design.
    CREATE TABLE sync_outbox (
        op_uuid       TEXT PRIMARY KEY,
        entity_type   TEXT NOT NULL,
        entity_id     TEXT,
        payload       TEXT NOT NULL,
        status        TEXT NOT NULL DEFAULT 'PENDING',
        attempts      INTEGER NOT NULL DEFAULT 0,
        created_seq   INTEGER NOT NULL,
        aggregate_seq INTEGER,
        created_at    TEXT NOT NULL,
        next_retry_at TEXT,
        last_error    TEXT,
        server_result TEXT
    );
    -- The drainer's query: pending work in causal order.
    CREATE INDEX idx_outbox_drain ON sync_outbox (status, created_seq);

    -- A monotonic counter for created_seq. A table rather than MAX(created_seq)
    -- so a purged outbox cannot hand out a sequence number twice.
    CREATE TABLE sync_sequence (
        id    INTEGER PRIMARY KEY CHECK (id = 1),
        value INTEGER NOT NULL DEFAULT 0
    );
    INSERT INTO sync_sequence (id, value) VALUES (1, 0);

    CREATE TABLE sync_conflicts (
        op_uuid       TEXT PRIMARY KEY,
        code          TEXT NOT NULL,
        message_ar    TEXT NOT NULL DEFAULT '',
        server_state  TEXT NOT NULL DEFAULT '{}',
        entity_type   TEXT NOT NULL,
        seen_at       TEXT NOT NULL,
        acknowledged  INTEGER NOT NULL DEFAULT 0
    );

    -- Invoice numbers reserved from the server (C9). Consumed locally with no
    -- coordination, because the ranges are disjoint by construction.
    CREATE TABLE invoice_blocks (
        id           TEXT PRIMARY KEY,
        range_start  INTEGER NOT NULL,
        range_end    INTEGER NOT NULL,
        next_unused  INTEGER NOT NULL,
        exhausted    INTEGER NOT NULL DEFAULT 0,
        allocated_at TEXT NOT NULL
    );

    CREATE TABLE print_queue (
        id           TEXT PRIMARY KEY,
        kind         TEXT NOT NULL,
        payload      TEXT NOT NULL,
        printer      TEXT NOT NULL DEFAULT '',
        status       TEXT NOT NULL DEFAULT 'PENDING',
        attempts     INTEGER NOT NULL DEFAULT 0,
        created_at   TEXT NOT NULL,
        last_error   TEXT
    );
    """,
]


def apply_migrations(connection) -> int:
    """
    Bring the database up to `SCHEMA_VERSION`. Returns the version applied to.

    Each script runs in its own transaction, so a failure halfway through
    migration 3 leaves the database at version 2 rather than at a version that
    exists nowhere.
    """
    current = connection.execute("PRAGMA user_version").fetchone()[0]

    for index, script in enumerate(MIGRATIONS, start=1):
        if index <= current:
            continue
        with connection:
            connection.executescript(script)
            connection.execute(f"PRAGMA user_version = {index}")

    return connection.execute("PRAGMA user_version").fetchone()[0]


#: Tables the UI may write. Everything else is a mirror, and `db.py` refuses.
WRITABLE_PREFIXES = ("l_", "sync_", "invoice_blocks", "print_queue")
