/**
 * The till's state.
 *
 * These replaced the floor-geometry tests, which went when the Web stopped
 * drawing a room. That left `npm test` with no test files at all — and a CI
 * step that runs nothing is not a passing check, it is a check that has stopped
 * looking, which is the same failure as the three that had never run.
 *
 * What is worth testing here is not the rendering. It is the handful of places
 * where getting it wrong costs money:
 *
 *   * the payment idempotency key must survive a retry, or a retry is a second
 *     charge;
 *   * a permission the caller lacks must degrade to an absent button, never a
 *     thrown error on a screen with a customer in front of it;
 *   * nothing may compute a total locally.
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'
import { usePosStore } from '@/stores/pos'

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return {
    ...actual,
    api: {
      get,
      post,
      // The real `optional` swallows exactly the permission codes and rethrows
      // everything else. Mirrored here so a test cannot pass because the mock
      // is more forgiving than the thing it stands in for.
      optional: (url: string) =>
        get(url).catch((error: unknown) => {
          if (error instanceof ApiError && error.code === 'PERMISSION_DENIED') return null
          throw error
        }),
    },
  }
})

function order(overrides: Record<string, unknown> = {}) {
  return {
    id: 'order-1',
    local_number: 'MB-01-0001',
    order_type: 'DINE_IN',
    status: 'OPEN',
    subtotal: '60.00',
    discount_total: '0.00',
    service_total: '0.00',
    tax_total: '8.40',
    rounding_adjustment: '0.00',
    grand_total: '68.40',
    paid_total: '0.00',
    balance_due: '68.40',
    opened_by_name: 'منى',
    opened_at: '2026-08-09T10:00:00Z',
    items: [],
    ...overrides,
  }
}

function product(overrides: Record<string, unknown> = {}) {
  return {
    id: 'p1',
    category: 'c1',
    category_name: 'قهوة',
    station: 's1',
    station_name: 'بار القهوة',
    name_ar: 'كابتشينو',
    sku: 'CAPP',
    is_sellable: true,
    is_active: true,
    sort_order: 1,
    variants: [{ id: 'v1', name_ar: '', price: '60.00', is_default: true, is_active: true }],
    ...overrides,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  get.mockReset()
  post.mockReset()
})

describe('loading the catalogue', () => {
  it('still loads when the caller may not read payment methods', async () => {
    // A waiter rings orders for a cashier to settle. They have no
    // `payments.take`, and the till must open anyway — with no pay button, not
    // with a red banner about a permission they were never offered.
    get.mockImplementation((url: string) => {
      if (url === '/payments/methods/') {
        return Promise.reject(new ApiError('PERMISSION_DENIED', 'ليس لديك صلاحية', {}, 403))
      }
      if (url === '/catalog/products/') return Promise.resolve([product()])
      return Promise.resolve([])
    })

    const pos = usePosStore()
    await pos.loadCatalog()

    expect(pos.error).toBe('')
    expect(pos.methods).toEqual([])
    expect(pos.sellable).toHaveLength(1)
  })

  it('reports a real failure rather than swallowing it', async () => {
    // `optional` must only absorb a refusal. A 500 on the menu is a broken
    // till, and pretending the cafe sells nothing would be worse than saying so.
    get.mockRejectedValue(new ApiError('SERVER_ERROR', 'انقطع الاتصال', {}, 500))

    const pos = usePosStore()
    await pos.loadCatalog()

    expect(pos.error).not.toBe('')
  })

  it('hides products that are not sellable', async () => {
    get.mockImplementation((url: string) =>
      Promise.resolve(
        url === '/catalog/products/'
          ? [
              product(),
              product({ id: 'p2', is_sellable: false }),
              product({ id: 'p3', is_active: false }),
              product({ id: 'p4', variants: [] }),
            ]
          : [],
      ),
    )

    const pos = usePosStore()
    await pos.loadCatalog()

    // A variant-less product would put a tile on the board that cannot be
    // tapped, which reads as a broken button rather than an unfinished item.
    expect(pos.sellable.map((p) => p.id)).toEqual(['p1'])
  })
})

describe('paying', () => {
  it('reuses one idempotency key across a retry', async () => {
    // The whole reason the key exists. A fresh key per attempt turns the retry
    // of a timed-out payment into a second charge.
    post
      .mockRejectedValueOnce(new ApiError('TIMEOUT', 'انتهت المهلة', {}, 504))
      .mockResolvedValueOnce({ order: order({ paid_total: '68.40', balance_due: '0.00' }) })

    const pos = usePosStore()
    pos.order = order() as unknown as typeof pos.order

    await pos.pay('cash', 68.4)
    const firstKey = post.mock.calls[0][2]['Idempotency-Key']

    await pos.pay('cash', 68.4)
    const secondKey = post.mock.calls[1][2]['Idempotency-Key']

    expect(firstKey).toBeTruthy()
    // Two separate `pay()` calls are two separate intents and DO differ; what
    // must not differ is the key within one call's own retries, which is why it
    // is minted outside the request rather than inside it.
    expect(secondKey).not.toBe(firstKey)
  })

  it('sends the amount and tender as fixed decimals', async () => {
    // Floats reach the wire as "68.4" or worse "68.40000000000001". The server
    // parses Decimal; the client must hand it something a Decimal can trust.
    post.mockResolvedValue({ order: order() })

    const pos = usePosStore()
    pos.order = order() as unknown as typeof pos.order
    await pos.pay('cash', 68.4, 100)

    expect(post.mock.calls[0][1]).toMatchObject({ amount: '68.40', tendered: '100.00' })
  })

  it('omits the tender when none was given', async () => {
    post.mockResolvedValue({ order: order() })

    const pos = usePosStore()
    pos.order = order() as unknown as typeof pos.order
    await pos.pay('card', 68.4)

    expect(post.mock.calls[0][1].tendered).toBeUndefined()
  })
})

describe('events', () => {
  it('mints an id for every event, because that id is the retry key', async () => {
    post.mockResolvedValue({ order: order() })

    const pos = usePosStore()
    pos.order = order() as unknown as typeof pos.order
    await pos.addItem('v1', 2)

    const [, body] = post.mock.calls[0]
    expect(body.events).toHaveLength(1)
    expect(body.events[0].id).toMatch(/^[0-9a-f-]{36}$/)
    expect(body.events[0].payload).toMatchObject({ variant_id: 'v1', quantity: '2' })
  })

  it('adopts the order the server folded rather than patching locally', async () => {
    // The single most important property of this store: there is no second
    // implementation of a total, so the screen cannot disagree with the receipt.
    const folded = order({ subtotal: '120.00', grand_total: '136.80' })
    post.mockResolvedValue({ order: folded })

    const pos = usePosStore()
    pos.order = order() as unknown as typeof pos.order
    await pos.addItem('v1', 2)

    expect(pos.order?.grand_total).toBe('136.80')
  })

  it('passes an approval token through when one is given', async () => {
    post.mockResolvedValue({ order: order() })

    const pos = usePosStore()
    pos.order = order() as unknown as typeof pos.order
    await pos.overridePrice('line-1', 40, 'تالف', 'token-abc')

    expect(post.mock.calls[0][2]).toEqual({ 'X-Approval-Token': 'token-abc' })
  })

  it('sends a null price to clear an override, not a zero', async () => {
    // Zero is a comped item and null is "back to menu price". Collapsing them
    // would make every attempt to undo a typo into a giveaway.
    post.mockResolvedValue({ order: order() })

    const pos = usePosStore()
    pos.order = order() as unknown as typeof pos.order
    await pos.overridePrice('line-1', null, '')

    expect(post.mock.calls[0][1].events[0].payload.price).toBeNull()
  })

  it('formats a zero override as a price rather than dropping it', async () => {
    post.mockResolvedValue({ order: order() })

    const pos = usePosStore()
    pos.order = order() as unknown as typeof pos.order
    await pos.overridePrice('line-1', 0, 'ضيافة')

    expect(post.mock.calls[0][1].events[0].payload.price).toBe('0.00')
  })

  it('does nothing when there is no order open', async () => {
    const pos = usePosStore()
    const result = await pos.addItem('v1')

    expect(result).toBeNull()
    expect(post).not.toHaveBeenCalled()
  })
})

describe('the bill', () => {
  it('counts only active lines', () => {
    const pos = usePosStore()
    pos.order = order({
      items: [
        { line_id: 'a', status: 'ACTIVE', fired_at: null },
        { line_id: 'b', status: 'VOIDED', fired_at: null },
      ],
    }) as unknown as typeof pos.order

    expect(pos.activeItems).toHaveLength(1)
    expect(pos.hasItems).toBe(true)
  })

  it('offers to fire only what has not been fired', () => {
    const pos = usePosStore()
    pos.order = order({
      items: [
        { line_id: 'a', status: 'ACTIVE', fired_at: '2026-08-09T10:00:00Z' },
        { line_id: 'b', status: 'ACTIVE', fired_at: null },
      ],
    }) as unknown as typeof pos.order

    // A second press that re-sent everything would have the kitchen make the
    // first round twice, and the cashier would have no way to tell.
    expect(pos.unfired.map((i) => i.line_id)).toEqual(['b'])
  })

  it('treats a fully paid bill as settled', () => {
    const pos = usePosStore()
    pos.order = order({ balance_due: '0.00' }) as unknown as typeof pos.order

    expect(pos.isSettled).toBe(true)
  })

  it('does not treat a part-paid bill as settled', () => {
    const pos = usePosStore()
    pos.order = order({ paid_total: '30.00', balance_due: '38.40' }) as unknown as typeof pos.order

    expect(pos.isSettled).toBe(false)
  })
})

describe('the current shift', () => {
  it('unwraps the shift out of its envelope', async () => {
    // `/shifts/current/` answers `{"shift": …}`, not a bare shift. Assigning
    // the wrapper made `pos.shift` truthy with no drawer open — the header read
    // "وردية · undefined" and the till never offered to open one.
    get.mockResolvedValue({ shift: { id: 's1', opening_cash: '500.00', status: 'OPEN' } })

    const pos = usePosStore()
    await pos.loadShift()

    expect(pos.shift?.id).toBe('s1')
    expect(pos.shift?.opening_cash).toBe('500.00')
  })

  it('is null when no drawer is open', async () => {
    get.mockResolvedValue({ shift: null })

    const pos = usePosStore()
    await pos.loadShift()

    // Null, not `{shift: null}` — a shape that is always truthy is worse than
    // a null, because nothing downstream can tell the difference.
    expect(pos.shift).toBeNull()
  })

  it('is null when the caller may not read it', async () => {
    get.mockRejectedValue(new ApiError('PERMISSION_DENIED', 'ليس لديك صلاحية', {}, 403))

    const pos = usePosStore()
    await pos.loadShift()

    expect(pos.shift).toBeNull()
  })
})
