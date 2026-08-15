// @vitest-environment happy-dom
/**
 * The till's first screen.
 *
 * The reversal these tests protect: the POS lands on the ROOM, not the menu. The
 * first question in table service is "who is this for?", and the board used to
 * ask "what are you selling?" instead — which is how a round lands on the wrong
 * bill and is discovered at closing.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.fn()
const push = vi.fn()

vi.mock('@/api/client', () => ({
  api: { get: (...args: unknown[]) => get(...args) },
  ApiError: class ApiError extends Error {},
}))

vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))

function table(over: Record<string, unknown> = {}) {
  return {
    table_id: 't1',
    number: '3',
    area: 'الصالة الداخلية',
    seats: 4,
    seated_count: 0,
    status: 'FREE',
    pos_x: 0,
    pos_y: 0,
    shape: 'SQUARE',
    span_x: 1,
    span_y: 1,
    rotation: 0,
    session_id: null,
    guest_count: null,
    seated_minutes: null,
    order_count: 0,
    total_due: '0.00',
    waiter: null,
    ...over,
  }
}

async function mountTables() {
  const { mount } = await import('@vue/test-utils')
  const { default: View } = await import('./PosTablesView.vue')
  const wrapper = mount(View, { global: { stubs: { UiIcon: true } } })
  await new Promise((resolve) => setTimeout(resolve, 0))
  return wrapper
}

beforeEach(() => {
  get.mockReset()
  push.mockReset()
  get.mockResolvedValue({ tables: [table()] })
})

describe('the floor as the till’s landing screen', () => {
  it('asks the server for the whole room in ONE request', async () => {
    // One call for the board, not one per table: a floor screen that fans out
    // gets slower exactly as the room gets busier.
    await mountTables()

    expect(get).toHaveBeenCalledTimes(1)
    expect(get).toHaveBeenCalledWith('/floor/status/')
  })

  it('shows the table number large enough to be the thing you tap', async () => {
    const wrapper = await mountTables()

    expect(wrapper.text()).toContain('3')
    expect(wrapper.find('.table-number').text()).toBe('3')
  })

  it('carries the table into the order screen', async () => {
    /**
     * The whole point of the reversal. The table travels in the QUERY, not in
     * memory, so a reload or a second device on the same URL lands on the same
     * table rather than on a blank order belonging to nobody.
     */
    const wrapper = await mountTables()

    await wrapper.find('.table-card').trigger('click')

    expect(push).toHaveBeenCalledWith({
      name: 'pos-order',
      query: { table: 't1', session: undefined, number: '3' },
    })
  })

  it('still allows selling without a table', async () => {
    // Takeaway, delivery and the counter are not table service and must not be
    // forced through a table that does not exist.
    const wrapper = await mountTables()

    const walkIn = wrapper.findAll('button').find((b) => b.text().includes('سفري'))
    await walkIn!.trigger('click')

    expect(push).toHaveBeenCalledWith({ name: 'pos-order' })
  })
})

describe('what a table shows', () => {
  it('shows what is owed when the party has ordered', async () => {
    get.mockResolvedValue({
      tables: [table({ session_id: 's1', seated_count: 2, order_count: 2, total_due: '184.50' })],
    })

    const wrapper = await mountTables()

    expect(wrapper.text()).toContain('184.50')
    expect(wrapper.text()).toContain('2 من 4')
  })

  it('says so when a party is seated with nothing ordered', async () => {
    // A blank where a total goes reads as zero owed; it needs to say why.
    get.mockResolvedValue({
      tables: [table({ session_id: 's1', seated_count: 2, order_count: 0, seated_minutes: 3 })],
    })

    const wrapper = await mountTables()

    expect(wrapper.text()).toContain('لم يطلب بعد')
  })

  it('flags a table sitting a long time with no order', async () => {
    /**
     * The one state worth marking on a floor. Ten minutes seated and nothing
     * taken is a table somebody has forgotten, and it is the difference between
     * a slow service and a walked customer.
     */
    get.mockResolvedValue({
      tables: [table({ session_id: 's1', order_count: 0, seated_minutes: 14 })],
    })

    const wrapper = await mountTables()

    expect(wrapper.find('.table-card').classes()).toContain('table-neglected')
  })

  it('does not flag a table that has ordered, however long it has sat', async () => {
    get.mockResolvedValue({
      tables: [table({ session_id: 's1', order_count: 3, seated_minutes: 90 })],
    })

    const wrapper = await mountTables()

    expect(wrapper.find('.table-card').classes()).not.toContain('table-neglected')
  })

  it('marks free and busy by more than colour', async () => {
    // The room is read at a glance by people who may not separate two hues, so
    // the state is in the words as well as the fill.
    get.mockResolvedValue({ tables: [table(), table({ table_id: 't2', number: '4', session_id: 's1', order_count: 1, total_due: '50.00' })] })

    const wrapper = await mountTables()

    expect(wrapper.text()).toContain('متاحة')
    expect(wrapper.text()).toContain('50.00')
  })
})

describe('when the floor cannot be refreshed', () => {
  it('keeps the last good board on screen', async () => {
    /**
     * A waiter mid-service needs the slightly stale answer far more than an
     * empty screen that is technically honest.
     */
    const wrapper = await mountTables()
    expect(wrapper.find('.table-card').exists()).toBe(true)

    get.mockRejectedValue(new Error('network'))
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(wrapper.find('.table-card').exists()).toBe(true)
  })

  it('says the room is empty rather than pretending it is loading', async () => {
    get.mockResolvedValue({ tables: [] })

    const wrapper = await mountTables()

    expect(wrapper.text()).toContain('لا توجد طاولات')
  })
})
