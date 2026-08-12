/**
 * The till's local store.
 *
 * The Desktop's SQLite layer is the specification for this — its exit criteria are
 * these tests, in order of how much a failure costs:
 *
 *   * the sale and its outbox row commit together or not at all;
 *   * an outage discards nothing;
 *   * the mirror cannot be written by the application;
 *   * a DELETE in the mirror really removes (a revoked permission must not
 *     survive);
 *   * an unknown entity type is skipped, not fatal;
 *   * money is text.
 */
import 'fake-indexeddb/auto'

import { IDBFactory } from 'fake-indexeddb'
import { beforeEach, describe, expect, it } from 'vitest'

import {
  applyMirror,
  all,
  defer,
  enqueue,
  forget,
  get,
  meta,
  MirrorIsReadOnly,
  open,
  pending,
  put,
  setMeta,
  settle,
  transact,
} from './db'

beforeEach(() => {
  // A fresh database per test. Without this the outbox sequence and the mirror
  // leak between tests and the failures become order-dependent.
  globalThis.indexedDB = new IDBFactory()
  forget()
})

describe('the outbox contract', () => {
  it('writes the sale and its outbox row in one transaction', async () => {
    await enqueue(
      { op_uuid: 'op-1', kind: 'ORDER_OPEN', payload: { id: 'o1' } },
      [{ store: 'l_orders', value: { id: 'o1', grand_total: '204.29' } }],
    )

    expect((await all('l_orders')).length).toBe(1)
    expect((await pending()).length).toBe(1)
  })

  it('commits NEITHER when the record write is rejected', async () => {
    /**
     * The property the whole design rests on. If a sale could land without its
     * outbox row, the sale sits on this machine and the server never hears about
     * it — a lost sale that reconciles to nothing. The Desktop proved this by
     * crashing between the two writes; here the abort stands in for the crash.
     */
    await expect(
      enqueue({ op_uuid: 'op-2', kind: 'ORDER_OPEN', payload: {} }, [
        { store: 'l_orders', value: { id: 'o2' } },
        // A mirror store in the middle: the guard throws, the transaction aborts.
        { store: 'm_products' as never, value: { id: 'nope' } },
      ]),
    ).rejects.toBeInstanceOf(MirrorIsReadOnly)

    expect(await all('l_orders')).toEqual([])
    expect(await pending()).toEqual([])
  })

  it('drains in the order it was written', async () => {
    // A payment that overtook the order it belongs to would arrive at a server
    // that has never heard of it.
    for (const id of ['a', 'b', 'c']) {
      await enqueue({ op_uuid: id, kind: 'ORDER_OPEN', payload: {} })
    }

    expect((await pending()).map((op) => op.op_uuid)).toEqual(['a', 'b', 'c'])
  })

  it('only leaves the queue once the server has accepted it', async () => {
    await enqueue({ op_uuid: 'op-3', kind: 'PAYMENT', payload: {} })

    await settle('op-3')

    expect(await pending()).toEqual([])
  })

  it('keeps a failed operation and records why', async () => {
    // An outage never discards anything. This is the difference between a slow
    // night and a lost one.
    await enqueue({ op_uuid: 'op-4', kind: 'PAYMENT', payload: {} })

    await defer('op-4', 'network unreachable')

    const [queued] = await pending()
    expect(queued.attempts).toBe(1)
    expect(queued.last_error).toBe('network unreachable')
  })

  it('does not resurrect an operation the server already accepted', async () => {
    await enqueue({ op_uuid: 'op-5', kind: 'PAYMENT', payload: {} })
    await settle('op-5')

    await defer('op-5', 'a late failure for an operation already gone')

    expect(await pending()).toEqual([])
  })
})

describe('the mirror is the server’s, not the till’s', () => {
  it('refuses an application write to a mirror store', async () => {
    // A terminal that can edit its own copy of a price can charge whatever it
    // likes, and the drift is invisible until a customer complains.
    await expect(put('m_products' as never, { id: 'p1', price: '1.00' })).rejects.toBeInstanceOf(
      MirrorIsReadOnly,
    )
  })

  it('names the store and the reason in the error', async () => {
    await expect(put('m_variants' as never, { id: 'v1' })).rejects.toThrow(/m_variants/)
  })

  it('accepts a pulled batch through the puller’s path', async () => {
    const result = await applyMirror([
      {
        entity_type: 'product',
        entity_id: 'p1',
        operation: 'UPSERT',
        payload: { name_ar: 'كابتشينو', sku: 'CAPP' },
      },
    ])

    expect(result.applied).toBe(1)
    expect(await get('m_products', 'p1')).toMatchObject({ id: 'p1', name_ar: 'كابتشينو' })
  })

  it('keeps the WHOLE payload, not just the fields the UI reads today', async () => {
    /**
     * A server newer than this client sends fields it has never heard of, and a
     * mirror that dropped them would lose data on every re-pull. The Desktop
     * learned this twice — `m_variants.sort_order` and `m_tables.pos_x` — each
     * time as a feature that silently had no data behind it.
     */
    await applyMirror([
      {
        entity_type: 'variant',
        entity_id: 'v1',
        operation: 'UPSERT',
        payload: { price: '45.00', sort_order: 2, some_field_from_the_future: 'kept' },
      },
    ])

    expect(await get('m_variants', 'v1')).toMatchObject({
      sort_order: 2,
      some_field_from_the_future: 'kept',
    })
  })

  it('really removes on DELETE', async () => {
    // The one mirror update that is a security control rather than a convenience:
    // a revoked permission must not survive in a local cache.
    await applyMirror([
      { entity_type: 'staff', entity_id: 'u1', operation: 'UPSERT', payload: { pin_hash: 'x' } },
    ])
    await applyMirror([
      { entity_type: 'staff', entity_id: 'u1', operation: 'DELETE', payload: {} },
    ])

    expect(await get('m_staff', 'u1')).toBeUndefined()
  })

  it('skips an unknown entity type instead of failing the batch', async () => {
    /**
     * Rule 4. A till that refused to sync over a feature it does not have is a
     * till that stops selling — so the unknown type is reported, and everything
     * alongside it still lands.
     */
    const result = await applyMirror([
      { entity_type: 'product', entity_id: 'p9', operation: 'UPSERT', payload: { sku: 'X' } },
      { entity_type: 'loyalty_card', entity_id: 'z1', operation: 'UPSERT', payload: {} },
    ])

    expect(result.applied).toBe(1)
    expect(result.skipped).toEqual(['loyalty_card'])
    expect(await get('m_products', 'p9')).toBeDefined()
  })

  it('reports each unknown type once, however many rows carried it', async () => {
    const result = await applyMirror([
      { entity_type: 'loyalty_card', entity_id: 'a', operation: 'UPSERT', payload: {} },
      { entity_type: 'loyalty_card', entity_id: 'b', operation: 'UPSERT', payload: {} },
    ])

    expect(result.skipped).toEqual(['loyalty_card'])
  })
})

describe('money is text', () => {
  it('round-trips a total as a string, never a number', async () => {
    // `number` is IEEE 754. Storing a total as one reintroduces exactly the
    // imprecision money.ts exists to avoid, on the one machine that computes the
    // total offline.
    await enqueue({ op_uuid: 'op-6', kind: 'ORDER_OPEN', payload: {} }, [
      { store: 'l_orders', value: { id: 'o6', grand_total: '204.29' } },
    ])

    const [order] = await all<{ grand_total: unknown }>('l_orders')
    expect(typeof order.grand_total).toBe('string')
    expect(order.grand_total).toBe('204.29')
  })
})

describe('cursors and reconnection', () => {
  it('stores and reads a pull cursor', async () => {
    await setMeta('cursor:catalog', 4127)

    expect(await meta<number>('cursor:catalog')).toBe(4127)
  })

  it('reconnects after the handle is dropped', async () => {
    // The Desktop shipped this bug: `connect()` returned a cached connection that
    // had already been closed, and the activation flow restarts in-process.
    await setMeta('k', 'v')
    forget()

    expect(await meta<string>('k')).toBe('v')
  })
})

describe('the transaction helper', () => {
  it('resolves only after the transaction has committed', async () => {
    const db = await open()

    await transact(db, ['l_orders'], 'readwrite', (tx) => {
      tx.objectStore('l_orders').put({ id: 'o7' })
    })

    // Read on a NEW transaction. If `transact` resolved before commit, this would
    // race and sometimes miss — which is precisely the silent failure the
    // synchronous-callback design exists to prevent.
    expect(await get('l_orders', 'o7')).toBeDefined()
  })

  it('aborts the whole transaction when the body throws', async () => {
    const db = await open()

    await expect(
      transact(db, ['l_orders'], 'readwrite', (tx) => {
        tx.objectStore('l_orders').put({ id: 'o8' })
        throw new Error('changed my mind')
      }),
    ).rejects.toThrow('changed my mind')

    expect(await get('l_orders', 'o8')).toBeUndefined()
  })
})
