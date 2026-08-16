// @vitest-environment happy-dom
/**
 * The till board renders.
 *
 * **Why this file exists.** The till was reported as showing nothing at all — the
 * `PosLayout` header and an empty body. Every string the report contained came
 * from the layout, which meant `<RouterView>` was rendering nothing, and there
 * was no way to find out why: this project had no component tests, so the only
 * instrument available was a browser nobody could point at from here.
 *
 * A blank screen is the one failure that types, lint and a green build cannot
 * see. `vue-tsc` checks the contract, `vite build` checks it compiles, and both
 * pass on a component that throws the moment it is mounted.
 *
 * These mount for real and assert the cashier can see the things a cashier needs:
 * the order-type control, the search box, the category tabs and either products
 * or an honest empty message. Nothing here talks to a server — `api` is stubbed —
 * because the question is whether the component survives its own setup and render,
 * not whether the backend is up.
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.fn()
const post = vi.fn()

vi.mock('@/api/client', () => ({
  api: {
    get: (...args: unknown[]) => get(...args),
    post: (...args: unknown[]) => post(...args),
    patch: vi.fn(),
    delete: vi.fn(),
    upload: vi.fn(),
    optional: (...args: unknown[]) => get(...args),
  },
  ApiError: class ApiError extends Error {
    code: string
    constructor(code: string, message: string) {
      super(message)
      this.code = code
    }
  },
  tokens: { access: 'x', refresh: 'y', set: vi.fn(), clear: vi.fn() },
}))

const CATEGORY = { id: 'c1', name_ar: 'مشروبات ساخنة', color: '#7b1e28', sort_order: 0 }
const PRODUCT = {
  id: 'p1',
  category: 'c1',
  category_name: 'مشروبات ساخنة',
  station_name: null,
  name_ar: 'كابتشينو',
  sku: 'CAPP',
  image: null,
  is_sellable: true,
  is_active: true,
  sort_order: 0,
  variants: [
    {
      id: 'v1',
      name_ar: '',
      sku: 'CAPP-M',
      price: '45.00',
      is_default: true,
      is_active: true,
      sort_order: 0,
      channel_prices: [],
    },
  ],
}

/**
 * The route the board reads its table from.
 *
 * Overridable per test: the board's job now depends on whether a table came with
 * it, so "no table" and "table 2" are two different screens and both need
 * covering.
 */
let routeQuery: Record<string, string> = {}

vi.mock('vue-router', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  useRoute: () => ({ query: routeQuery }),
}))

async function mountBoard() {
  const { mount } = await import('@vue/test-utils')
  const { default: PosBoardView } = await import('./PosBoardView.vue')
  const wrapper = mount(PosBoardView, {
    global: {
      // The board pulls in the order panel, the item sheet and the payment sheet.
      // Stubbed so a failure in one of them cannot be mistaken for the board
      // failing — this test is about the board.
      stubs: {
        OrderPanel: true,
        ItemSheet: true,
        PaymentSheet: true,
        UiIcon: true,
        // A real anchor, not `true`. RouterLink needs a router installed and
        // this mount has none — the same lesson the main.ts bug taught, arriving
        // here as a stub. Rendering an `<a href>` keeps the assertion about the
        // destination meaningful instead of asserting on a placeholder tag.
        RouterLink: {
          props: ['to'],
          template: '<a :href="to"><slot /></a>',
        },
      },
    },
  })
  await new Promise((resolve) => setTimeout(resolve, 0))
  return wrapper
}

beforeEach(() => {
  routeQuery = {}
  setActivePinia(createPinia())
  get.mockReset()
  post.mockReset()
  get.mockImplementation((url: string) => {
    if (url.includes('/catalog/categories/')) return Promise.resolve([CATEGORY])
    if (url.includes('/catalog/products/')) return Promise.resolve([PRODUCT])
    if (url.includes('/payments/methods/')) return Promise.resolve([])
    if (url.includes('/shifts/current/'))
      return Promise.resolve({ shift: { id: 's1', opening_cash: '500.00', status: 'OPEN' } })
    return Promise.resolve([])
  })
})

describe('the till board', () => {
  it('mounts without throwing', async () => {
    // The whole point. A component that throws in setup renders nothing, and the
    // layout around it still looks fine — which is exactly what was reported.
    const wrapper = await mountBoard()
    expect(wrapper.exists()).toBe(true)
  })

  it('shows the controls a cashier needs before any data arrives', async () => {
    const wrapper = await mountBoard()
    const text = wrapper.text()

    expect(text).toContain('طلب جديد')
    expect(text).toContain('صالة')
    expect(wrapper.find('input[type="search"]').exists()).toBe(true)
    expect(text).toContain('الكل')
  })

  it('draws a tile for a sellable product', async () => {
    const wrapper = await mountBoard()

    expect(wrapper.text()).toContain('كابتشينو')
    expect(wrapper.findAll('.tile').length).toBeGreaterThan(0)
  })

  it('says so plainly when the menu is empty, rather than rendering a blank panel', async () => {
    // The failure mode this replaces: a cashier looking at nothing with no way to
    // tell an empty category from a broken screen.
    get.mockImplementation((url: string) => {
      if (url.includes('/shifts/current/'))
        return Promise.resolve({ shift: { id: 's1', opening_cash: '500.00', status: 'OPEN' } })
      return Promise.resolve([])
    })

    const wrapper = await mountBoard()

    expect(wrapper.text()).toContain('لا توجد أصناف')
  })

  it('keeps the menu usable when the catalogue request fails', async () => {
    /**
     * An outage must not blank the till.
     *
     * The store catches the error into `pos.error` and the board shows it — but
     * the search box and the tabs stay on screen, because a cashier who can see
     * the controls knows the screen is alive and can retry.
     */
    get.mockImplementation((url: string) => {
      if (url.includes('/shifts/current/'))
        return Promise.resolve({ shift: { id: 's1', opening_cash: '500.00', status: 'OPEN' } })
      return Promise.reject(new Error('network'))
    })

    const wrapper = await mountBoard()

    expect(wrapper.find('input[type="search"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('الكل')
  })
})

describe('no shift open', () => {
  beforeEach(() => {
    // Overrides the suite default, which keeps a shift open so the menu renders.
    get.mockImplementation((url: string) => {
      if (url.includes('/catalog/categories/')) return Promise.resolve([CATEGORY])
      if (url.includes('/catalog/products/')) return Promise.resolve([PRODUCT])
      if (url.includes('/shifts/current/')) return Promise.resolve({ shift: null })
      return Promise.resolve([])
    })
  })

  /**
   * The reported "POS does not work".
   *
   * The server refuses a sale without a shift and says so — SHIFT_REQUIRED,
   * "يجب فتح وردية قبل البيع". The till threw that away: `tap()` called
   * `openOrder`, it failed, `if (!pos.order) return` gave up, and the message
   * landed in an alert at the foot of a scrolling menu. A cashier tapped a
   * product, nothing happened, and reported the till as broken.
   */
  it('offers the one action that fixes it instead of a menu that cannot work', async () => {
    const wrapper = await mountBoard()

    expect(wrapper.text()).toContain('لازم تفتح وردية قبل البيع')
    expect(wrapper.find('a[href="/pos/shift"]').exists()).toBe(true)
  })

  it('does not draw product tiles that could only fail', async () => {
    const wrapper = await mountBoard()

    expect(wrapper.findAll('.tile').length).toBe(0)
  })

  it('draws the menu again once a shift is open', async () => {
    get.mockImplementation((url: string) => {
      if (url.includes('/catalog/categories/')) return Promise.resolve([CATEGORY])
      if (url.includes('/catalog/products/')) return Promise.resolve([PRODUCT])
      if (url.includes('/shifts/current/'))
        return Promise.resolve({ shift: { id: 's1', opening_cash: '500.00', status: 'OPEN' } })
      return Promise.resolve([])
    })

    const wrapper = await mountBoard()

    expect(wrapper.text()).not.toContain('لازم تفتح وردية')
    expect(wrapper.findAll('.tile').length).toBeGreaterThan(0)
  })
})

describe('the table the floor sent with the order', () => {
  /**
   * The floor has been sending `?table=&session=&number=` since it became the
   * till's landing screen, and this board ignored all three: it opened a plain
   * DINE_IN order with no session, so tapping table 2 and ringing a coffee
   * produced a bill attached to nobody.
   *
   * That is the worst shape a gap can take. The flow looked correct end to end —
   * you tapped a table, you got an order screen, you rang items — and the wrong
   * bill was only discoverable at closing, when nobody can reconstruct it.
   */

  it('names the table on the bill screen', async () => {
    // The cashier's confirmation that the order went where they tapped.
    routeQuery = { table: 't1', number: '2' }

    const wrapper = await mountBoard()

    expect(wrapper.text()).toContain('طاولة 2')
  })

  it('says nothing about a table when there is none', async () => {
    // Takeaway and the counter are not table service; a stray "طاولة" here would
    // be a claim about a bill that has no table.
    routeQuery = {}

    const wrapper = await mountBoard()

    expect(wrapper.text()).not.toContain('طاولة')
  })

  it('opens a session for a FREE table before the first sale', async () => {
    /**
     * Seating and ordering are one gesture at a till — nobody taps "seat this
     * party" and then "take their order". A free table arrives with no session,
     * so one is opened on the way.
     */
    routeQuery = { table: 't1', number: '2' }
    // A session, then an order — both go through `post`, so the mock has to
    // answer plausibly for each rather than returning one shape for both.
    post.mockImplementation((url: string) =>
      url === '/floor/sessions/'
        ? Promise.resolve({ id: 'new-session' })
        : Promise.resolve({ id: 'order-1', items: [], status: 'OPEN' }),
    )

    const wrapper = await mountBoard()
    const fresh = wrapper.findAll('button').find((b) => b.text().includes('طلب جديد'))
    await fresh!.trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(post).toHaveBeenCalledWith('/floor/sessions/', { table: 't1', guest_count: 1 })
  })

  it('reuses the session a seated table already has', async () => {
    // A party already sitting must not be seated twice — that is two bills for
    // one table, which is exactly what this whole flow exists to prevent.
    routeQuery = { table: 't1', number: '2', session: 'existing' }
    post.mockResolvedValue({ id: 'order-1', items: [], status: 'OPEN' })

    const wrapper = await mountBoard()
    const fresh = wrapper.findAll('button').find((b) => b.text().includes('طلب جديد'))
    await fresh!.trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))

    const sessionCalls = post.mock.calls.filter((c) => c[0] === '/floor/sessions/')
    expect(sessionCalls).toHaveLength(0)
  })

  it('opens no session at all without a table', async () => {
    routeQuery = {}

    const wrapper = await mountBoard()
    const fresh = wrapper.findAll('button').find((b) => b.text().includes('طلب جديد'))
    await fresh!.trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(post.mock.calls.filter((c) => c[0] === '/floor/sessions/')).toHaveLength(0)
  })
})
