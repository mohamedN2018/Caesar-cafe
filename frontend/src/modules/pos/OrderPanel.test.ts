// @vitest-environment happy-dom
/**
 * The bill panel, and the one control that must not be on it at a table.
 *
 * A seated party orders in rounds and pays once, when they leave. Offering «دفع»
 * beside a round invited settling a bill still being added to: a receipt printed,
 * and then two more coffees with nowhere to go but a second bill. Which is
 * exactly what the running demo had — six open orders on table 2.
 *
 * So on a table the action is «إضافة للفاتورة» and settling lives on the table's
 * own sheet. On the counter nothing changes: one customer, one bill, pay now.
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
    optional: (...args: unknown[]) => get(...args),
  },
  ApiError: class ApiError extends Error {},
  tokens: { access: 'x', refresh: 'y', set: vi.fn(), clear: vi.fn() },
}))

const ORDER = {
  id: 'order-1',
  local_number: 'MB-01-0007',
  order_type: 'DINE_IN',
  status: 'OPEN',
  subtotal: '120.00',
  discount_total: '0.00',
  service_total: '0.00',
  tax_total: '0.00',
  rounding_adjustment: '0.00',
  grand_total: '120.00',
  paid_total: '0.00',
  balance_due: '120.00',
  items: [
    {
      line_id: 'l1',
      variant: 'v1',
      name_ar: 'كابتشينو',
      quantity: '2',
      unit_price_snapshot: '60.00',
      line_total: '120.00',
      status: 'ACTIVE',
      fired_at: null,
      modifiers: [],
    },
  ],
}

async function mountPanel(props: Record<string, unknown> = {}) {
  const { mount } = await import('@vue/test-utils')
  const { usePosStore } = await import('@/stores/pos')
  const { default: OrderPanel } = await import('./OrderPanel.vue')

  const pos = usePosStore()
  pos.order = ORDER as never
  // A configured tender, so a hidden pay button can only mean the table rule and
  // not "nobody set up a payment method".
  pos.methods = [{ id: 'm1', name_ar: 'نقدي', kind: 'CASH', is_active: true }] as never

  const wrapper = mount(OrderPanel, {
    props,
    global: { stubs: { UiIcon: true } },
  })
  await new Promise((resolve) => setTimeout(resolve, 0))
  return wrapper
}

beforeEach(async () => {
  setActivePinia(createPinia())
  get.mockReset()
  post.mockReset()

  const { useAuthStore } = await import('@/stores/auth')
  const auth = useAuthStore()
  auth.me = {
    id: 'u1',
    email: 'x@caesar.test',
    permissions: ['payments.take', 'orders.discount'],
  } as never
})

describe('at the counter', () => {
  it('offers دفع', async () => {
    const wrapper = await mountPanel({ onATable: false })

    expect(wrapper.text()).toContain('دفع')
  })

  it('does not offer إضافة للفاتورة — there is no bill to come back to', async () => {
    const wrapper = await mountPanel({ onATable: false })

    expect(wrapper.text()).not.toContain('إضافة للفاتورة')
  })
})

describe('on a table', () => {
  it('does NOT offer دفع', async () => {
    const wrapper = await mountPanel({ onATable: true, tableNumber: '7' })

    const pay = wrapper.findAll('button').find((b) => b.text().trim() === 'دفع')
    expect(pay).toBeUndefined()
  })

  it('offers إضافة للفاتورة instead', async () => {
    const wrapper = await mountPanel({ onATable: true, tableNumber: '7' })

    expect(wrapper.text()).toContain('إضافة للفاتورة')
  })

  it('emits done rather than pay', async () => {
    const wrapper = await mountPanel({ onATable: true, tableNumber: '7' })

    const add = wrapper.findAll('button').find((b) => b.text().includes('إضافة للفاتورة'))
    await add!.trigger('click')

    expect(wrapper.emitted('done')).toHaveLength(1)
    expect(wrapper.emitted('pay')).toBeUndefined()
  })

  it('says which table the bill belongs to', async () => {
    // On the bill itself, not only above the menu: this is the line somebody
    // reads while saying the total out loud.
    const wrapper = await mountPanel({ onATable: true, tableNumber: '7' })

    expect(wrapper.find('.for-table').text()).toBe('طاولة 7')
  })

  it('still sends a round to the kitchen', async () => {
    // Filing to the bill is not instead of firing — the kitchen still needs it.
    const wrapper = await mountPanel({ onATable: true, tableNumber: '7' })

    expect(wrapper.text()).toContain('للمطبخ')
  })
})
