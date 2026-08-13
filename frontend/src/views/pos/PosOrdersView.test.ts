// @vitest-environment happy-dom
/**
 * The cashier's order list on the till.
 *
 * Today by default, other days behind a filter. A list that opened on "everything
 * ever" would put a fortnight of history in front of somebody who needed the last
 * twenty minutes.
 *
 * The other thing pinned here is what is NOT on the screen: no cost, no margin, no
 * per-cashier performance. A cashier holds `orders.view`, not `reports.financial`,
 * and putting numbers on a screen the role is not trusted with is how a permission
 * boundary becomes decorative.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.fn()

vi.mock('@/api/client', () => ({
  api: { get: (...args: unknown[]) => get(...args), post: vi.fn(), optional: vi.fn() },
  ApiError: class ApiError extends Error {},
  tokens: { access: 'x', refresh: 'y', set: vi.fn(), clear: vi.fn() },
}))

function pad(n: number): string {
  return String(n).padStart(2, '0')
}
function today(): string {
  const d = new Date()
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

const ROWS = [
  {
    id: 'o1',
    local_number: 'MB-56-0001',
    order_type: 'DINE_IN',
    status: 'OPEN',
    table_number: '4',
    grand_total: '204.29',
    paid_total: '0.00',
    opened_at: '2026-08-12T09:15:00Z',
    item_count: 3,
  },
  {
    id: 'o2',
    local_number: 'MB-56-0002',
    order_type: 'TAKE_AWAY',
    status: 'PAID',
    table_number: null,
    grand_total: '68.40',
    paid_total: '68.40',
    opened_at: '2026-08-12T09:40:00Z',
    item_count: 1,
  },
]

async function mountOrders() {
  const { mount } = await import('@vue/test-utils')
  const { default: PosOrdersView } = await import('./PosOrdersView.vue')
  const wrapper = mount(PosOrdersView)
  await new Promise((resolve) => setTimeout(resolve, 0))
  return wrapper
}

beforeEach(() => {
  get.mockReset()
  get.mockResolvedValue(ROWS)
})

describe('the cashier’s order list', () => {
  it('asks for today, and only today', async () => {
    await mountOrders()

    const url = get.mock.calls[0][0] as string
    expect(url).toContain(`date_from=${today()}T00:00:00`)
    // The window ends at the last instant of the day. The server filters on
    // `opened_at`, so a `date_to` of midnight would drop every order after 00:00.
    expect(url).toContain(`date_to=${today()}T23:59:59`)
  })

  it('shows the number, the table, the time and the total', async () => {
    const wrapper = await mountOrders()
    const text = wrapper.text()

    expect(text).toContain('MB-56-0001')
    expect(text).toContain('ترابيزة 4')
    expect(text).toContain('204.29')
    expect(text).toContain('صالة')
  })

  it('counts what is still owed separately from what was taken', async () => {
    const wrapper = await mountOrders()
    const text = wrapper.text()

    expect(text).toContain('لسه مفتوح')
    expect(text).toContain('محصَّل')
    // Only the PAID row counts toward money taken.
    expect(text).toContain('68.40')
  })

  it('carries a word beside every status colour', async () => {
    // Colour is never the only signal — for colour-blind staff and for the
    // washed-out screens these run on.
    const wrapper = await mountOrders()

    expect(wrapper.text()).toContain('مفتوح')
    expect(wrapper.text()).toContain('مدفوع')
  })

  it('shows no cost, margin or performance figure', async () => {
    /**
     * The permission boundary, made visible. `orders.view` is not
     * `reports.financial`, and the admin list's costing columns have no business
     * on a till.
     */
    const wrapper = await mountOrders()
    const text = wrapper.text()

    for (const forbidden of ['التكلفة', 'الهامش', 'الربح']) {
      expect(text).not.toContain(forbidden)
    }
  })

  it('offers a way back to today once you have wandered off it', async () => {
    const wrapper = await mountOrders()

    // On today there is nothing to go back to, so the button is absent rather
    // than present-and-useless.
    expect(wrapper.text()).not.toContain('رجوع لليوم')

    await wrapper.findAll('button').find((b) => b.text() === 'اليوم السابق')?.trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(wrapper.text()).toContain('رجوع لليوم')
  })

  it('cannot walk into the future', async () => {
    const wrapper = await mountOrders()

    const next = wrapper.findAll('button').find((b) => b.text() === 'اليوم التالي')
    expect(next?.attributes('disabled')).toBeDefined()
  })

  it('says the day is empty rather than rendering a blank panel', async () => {
    get.mockResolvedValue([])

    const wrapper = await mountOrders()

    expect(wrapper.text()).toContain('لا توجد طلبات اليوم بعد')
  })
})
