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

async function mountBoard() {
  const { mount } = await import('@vue/test-utils')
  const { default: PosBoardView } = await import('./PosBoardView.vue')
  const wrapper = mount(PosBoardView, {
    global: {
      // The board pulls in the order panel, the item sheet and the payment sheet.
      // Stubbed so a failure in one of them cannot be mistaken for the board
      // failing — this test is about the board.
      stubs: { OrderPanel: true, ItemSheet: true, PaymentSheet: true, UiIcon: true },
    },
  })
  await new Promise((resolve) => setTimeout(resolve, 0))
  return wrapper
}

beforeEach(() => {
  setActivePinia(createPinia())
  get.mockReset()
  post.mockReset()
  get.mockImplementation((url: string) => {
    if (url.includes('/catalog/categories/')) return Promise.resolve([CATEGORY])
    if (url.includes('/catalog/products/')) return Promise.resolve([PRODUCT])
    if (url.includes('/payments/methods/')) return Promise.resolve([])
    if (url.includes('/shifts/current/')) return Promise.resolve({ shift: null })
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
      if (url.includes('/shifts/current/')) return Promise.resolve({ shift: null })
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
      if (url.includes('/shifts/current/')) return Promise.resolve({ shift: null })
      return Promise.reject(new Error('network'))
    })

    const wrapper = await mountBoard()

    expect(wrapper.find('input[type="search"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('الكل')
  })
})
