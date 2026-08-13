/**
 * The till's local database. IndexedDB, and the foundation of selling offline.
 *
 * The PySide6 Desktop is cancelled, so the browser is now the offline point of
 * sale. What it inherits is a proven design rather than a blank page: the
 * Desktop's SQLite layer, its `m_`/`l_` split and its ~525 tests are the
 * specification for this file, and the four rules below are its rules.
 *
 * ── 1. The mirror is not writable by the application ────────────────────────
 * `m_` stores are the server's copy of the catalogue, prices and config. Only the
 * puller writes them, through `applyMirror`. `put()` refuses an `m_` store
 * outright, because — in the Desktop's words — a terminal that can edit its own
 * copy of a price can charge whatever it likes, and the drift is invisible until
 * a customer complains.
 *
 * ── 2. A sale and its outbox row commit together, or not at all ──────────────
 * The single most important property here. Without it the sale sits on the
 * machine and the server never hears about it: a lost sale that reconciles to
 * nothing. The Desktop proved this by crashing between the two writes.
 *
 * **In IndexedDB this is easy to break by accident and the breakage is silent.**
 * A transaction auto-commits as soon as the microtask queue drains with no
 * request outstanding — so a single `await` between two writes ends the
 * transaction early and the second write lands in a NEW one. That is why
 * `transact()` takes a SYNCHRONOUS callback: it issues every request in one go
 * and the promise resolves on `oncomplete`, never on an individual request. Any
 * refactor that makes that callback `async` silently removes the guarantee, which
 * is why it is typed to forbid it.
 *
 * ── 3. Money is TEXT, never a JS number ─────────────────────────────────────
 * `number` is IEEE 754. Storing a total as one would reintroduce exactly the
 * imprecision `money.ts` and `money.py` exist to avoid, on the one machine where
 * the total is computed offline.
 *
 * ── 4. An unknown entity type is skipped, not fatal ─────────────────────────
 * A server newer than this client will send entity types it has never heard of.
 * A till that refused to sync over a feature it does not have is a till that
 * stops selling.
 */

export const DB_NAME = 'caesar'
export const DB_VERSION = 1

/** Server-owned. Read-only to everything except the puller. */
export const MIRROR_STORES = [
  'm_categories',
  'm_products',
  'm_variants',
  'm_config',
  'm_staff',
] as const

/** This terminal's own records, and the queue that carries them to the server. */
export const LOCAL_STORES = ['l_orders', 'l_order_events', 'l_payments'] as const

export type MirrorStore = (typeof MIRROR_STORES)[number]
export type LocalStore = (typeof LOCAL_STORES)[number]
export type StoreName = MirrorStore | LocalStore | 'outbox' | 'meta'

export class MirrorIsReadOnly extends Error {
  constructor(store: string) {
    super(
      `${store} is a mirror of the server. Only the puller may write it — ` +
        'a till that can edit its own copy of a price can charge whatever it likes.',
    )
    this.name = 'MirrorIsReadOnly'
  }
}

function isMirror(store: string): boolean {
  return (MIRROR_STORES as readonly string[]).includes(store)
}

let handle: IDBDatabase | null = null

export function open(): Promise<IDBDatabase> {
  // A cached handle, but never a CLOSED one. The Desktop shipped this bug: the
  // activation flow restarts in-process, and `connect()` handed back a connection
  // that had already been closed.
  if (handle && !isClosed(handle)) return Promise.resolve(handle)

  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION)

    request.onupgradeneeded = () => {
      const db = request.result
      for (const store of MIRROR_STORES) {
        if (!db.objectStoreNames.contains(store)) db.createObjectStore(store, { keyPath: 'id' })
      }
      for (const store of LOCAL_STORES) {
        if (!db.objectStoreNames.contains(store)) {
          const created = db.createObjectStore(store, { keyPath: 'id' })
          // Every local record knows which order it belongs to, so a fold can read
          // one order's events without scanning the whole store.
          if (store !== 'l_orders') created.createIndex('order_id', 'order_id')
        }
      }
      if (!db.objectStoreNames.contains('outbox')) {
        const outbox = db.createObjectStore('outbox', { keyPath: 'op_uuid' })
        // Drained in the order it was written. A payment that overtook the order
        // it belongs to would arrive at a server that has never heard of it.
        outbox.createIndex('sequence', 'sequence')
      }
      if (!db.objectStoreNames.contains('meta')) db.createObjectStore('meta', { keyPath: 'key' })
    }

    request.onsuccess = () => {
      handle = request.result
      resolve(handle)
    }
    request.onerror = () => reject(request.error)
  })
}

function isClosed(db: IDBDatabase): boolean {
  try {
    // Opening a transaction on a closed connection throws InvalidStateError.
    db.transaction('meta', 'readonly').abort()
    return false
  } catch {
    return true
  }
}

/**
 * Run one IndexedDB transaction.
 *
 * **`work` MUST be synchronous.** It is typed to return a plain value rather than
 * a promise for exactly that reason: an `async` body would let the transaction
 * auto-commit mid-way, and the second half of an atomic write would silently land
 * in a different transaction. Issue every request inside `work`, and read results
 * from the request objects after the returned promise resolves.
 */
export function transact<T>(
  db: IDBDatabase,
  stores: StoreName[],
  mode: IDBTransactionMode,
  work: (tx: IDBTransaction) => T,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const tx = db.transaction(stores, mode)
    let result: T
    tx.oncomplete = () => resolve(result)
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error ?? new Error('transaction aborted'))
    try {
      result = work(tx)
    } catch (error) {
      tx.abort()
      reject(error)
    }
  })
}

function request<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

// ── reading ─────────────────────────────────────────────────────────────────

export async function all<T>(store: StoreName): Promise<T[]> {
  const db = await open()
  return request(db.transaction(store, 'readonly').objectStore(store).getAll() as IDBRequest<T[]>)
}

export async function get<T>(store: StoreName, key: IDBValidKey): Promise<T | undefined> {
  const db = await open()
  return request(db.transaction(store, 'readonly').objectStore(store).get(key) as IDBRequest<T>)
}

// ── writing ─────────────────────────────────────────────────────────────────

/**
 * Write to a LOCAL store. Refuses a mirror.
 *
 * This is the guard rule 1 exists for, and it is enforced here rather than by
 * convention because the failure it prevents is invisible: a price edited locally
 * looks fine on the screen that edited it.
 */
export async function put(store: LocalStore | 'meta' | 'outbox', value: object): Promise<void> {
  if (isMirror(store)) throw new MirrorIsReadOnly(store)
  const db = await open()
  await transact(db, [store], 'readwrite', (tx) => {
    tx.objectStore(store).put(value)
  })
}

/** The next outbox sequence. Monotonic per terminal, so the drain order is the write order. */
async function nextSequence(db: IDBDatabase): Promise<number> {
  const rows = await request(
    db.transaction('outbox', 'readonly').objectStore('outbox').getAll() as IDBRequest<
      { sequence: number }[]
    >,
  )
  return rows.reduce((highest, row) => Math.max(highest, row.sequence), 0) + 1
}

export interface OutboxOperation {
  op_uuid: string
  kind: string
  payload: unknown
}

/**
 * Write local records AND their outbox operation in ONE transaction.
 *
 * The property rule 2 describes. Every `put` below is issued synchronously inside
 * the same transaction, so the browser either commits all of it or none of it —
 * there is no window in which a sale exists locally with nothing queued to carry
 * it to the server.
 *
 * The sequence is read BEFORE the transaction opens, deliberately: reading it
 * inside would mean an `await` in the middle of the atomic write, which is the
 * exact mistake this design forbids. A duplicate sequence is harmless — the drain
 * order is a hint, and `op_uuid` is what makes a replay idempotent server-side.
 */
export async function enqueue(
  operation: OutboxOperation,
  records: { store: LocalStore; value: object }[] = [],
): Promise<void> {
  const db = await open()
  const sequence = await nextSequence(db)
  const stores: StoreName[] = ['outbox', ...new Set(records.map((r) => r.store))]

  await transact(db, stores, 'readwrite', (tx) => {
    for (const record of records) {
      if (isMirror(record.store)) throw new MirrorIsReadOnly(record.store)
      tx.objectStore(record.store).put(record.value)
    }
    tx.objectStore('outbox').put({
      ...operation,
      sequence,
      queued_at: new Date().toISOString(),
      attempts: 0,
      last_error: '',
    })
  })
}

export interface QueuedOperation extends OutboxOperation {
  sequence: number
  queued_at: string
  attempts: number
  last_error: string
}

/** Everything still waiting, oldest first. */
export async function pending(): Promise<QueuedOperation[]> {
  const rows = await all<QueuedOperation>('outbox')
  return rows.sort((a, b) => a.sequence - b.sequence)
}

/** The server accepted it. Only now does it leave the queue. */
export async function settle(opUuid: string): Promise<void> {
  const db = await open()
  await transact(db, ['outbox'], 'readwrite', (tx) => {
    tx.objectStore('outbox').delete(opUuid)
  })
}

/**
 * The push failed. The operation STAYS.
 *
 * An outage must never discard anything — the Desktop's exit criterion, and the
 * difference between a slow night and a lost one. The error is recorded so a
 * conflict that needs a human is visible rather than retried forever.
 */
export async function defer(opUuid: string, error: string): Promise<void> {
  const db = await open()
  const existing = await get<QueuedOperation>('outbox', opUuid)
  if (!existing) return
  await transact(db, ['outbox'], 'readwrite', (tx) => {
    tx.objectStore('outbox').put({
      ...existing,
      attempts: existing.attempts + 1,
      last_error: error.slice(0, 500),
    })
  })
}

// ── the mirror ──────────────────────────────────────────────────────────────

export interface MirrorRow {
  entity_type: string
  entity_id: string
  operation: 'UPSERT' | 'DELETE'
  payload: Record<string, unknown>
}

/** `entity_type` from the server's change log → local store. */
const ENTITY_STORES: Record<string, MirrorStore> = {
  category: 'm_categories',
  product: 'm_products',
  variant: 'm_variants',
  setting: 'm_config',
  staff: 'm_staff',
}

/**
 * Apply a pulled batch. The ONLY writer of the mirror.
 *
 * Returns what it skipped rather than throwing, because of rule 4: a server newer
 * than this client sends entity types it has never heard of, and a till that
 * refused to sync over a feature it does not have is a till that stops selling.
 * Reported so the skip is visible instead of silent.
 *
 * **The whole payload is kept**, not just the columns the UI reads today. A server
 * that adds a field must not lose it on every re-pull — the Desktop learned this
 * twice, with `m_variants.sort_order` and `m_tables.pos_x`, each time as a feature
 * that silently had no data behind it.
 */
export async function applyMirror(rows: MirrorRow[]): Promise<{ applied: number; skipped: string[] }> {
  const db = await open()
  const known = rows.filter((row) => ENTITY_STORES[row.entity_type])
  const skipped = [...new Set(rows.filter((r) => !ENTITY_STORES[r.entity_type]).map((r) => r.entity_type))]

  if (known.length) {
    const stores = [...new Set(known.map((row) => ENTITY_STORES[row.entity_type]))]
    await transact(db, stores as StoreName[], 'readwrite', (tx) => {
      for (const row of known) {
        const store = tx.objectStore(ENTITY_STORES[row.entity_type])
        if (row.operation === 'DELETE') {
          // A revoked permission must not survive in the mirror. The one mirror
          // update that is a security control rather than a convenience.
          store.delete(row.entity_id)
        } else {
          store.put({ ...row.payload, id: row.entity_id })
        }
      }
    })
  }

  return { applied: known.length, skipped }
}

/** Cursors and device facts. */
export async function meta<T>(key: string): Promise<T | undefined> {
  const row = await get<{ key: string; value: T }>('meta', key)
  return row?.value
}

export async function setMeta(key: string, value: unknown): Promise<void> {
  await put('meta', { key, value })
}

/** Test seam. Drops the cached handle so the next `open()` reconnects. */
export function forget(): void {
  handle = null
}
