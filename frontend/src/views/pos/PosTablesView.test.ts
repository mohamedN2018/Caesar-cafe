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

/** Tap a party size in the sheet a free table opens. */
async function pickGuests(wrapper: { findAll: (s: string) => { text: () => string; trigger: (e: string) => Promise<void> }[] }, n: string) {
  const choice = wrapper.findAll('.guest-choice').find((b) => b.text() === n)
  await choice!.trigger('click')
}

beforeEach(() => {
  get.mockReset()
  push.mockReset()
  get.mockResolvedValue([table()])
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
    await pickGuests(wrapper, '2')

    expect(push).toHaveBeenCalledWith({
      name: 'pos-order',
      query: { table: 't1', session: undefined, number: '3', guests: '2' },
    })
  })

  it('offers quick sell on the floor itself, not only in the tab bar', async () => {
    /**
     * A counter sale happens while somebody is standing at THIS screen. Takeaway,
     * delivery and the till are not table service and must not be routed through
     * a table that does not exist.
     */
    const wrapper = await mountTables()

    const walkIn = wrapper.findAll('button').find((b) => b.text().includes('بيع سريع'))
    await walkIn!.trigger('click')

    expect(push).toHaveBeenCalledWith({ name: 'pos-order' })
  })

  it('counts the FREE tables, which is the number somebody is looking for', async () => {
    /**
     * The header counted occupied — the same arithmetic seen from the wrong end.
     * Somebody standing at the door with a party of four is not counting the
     * tables they cannot seat them at.
     */
    get.mockResolvedValue([
        table(),
        table({ table_id: 't2', number: '4' }),
        table({ table_id: 't3', number: '5', session_id: 's1' }),
      ])

    const wrapper = await mountTables()

    expect(wrapper.text()).toContain('2 فاضية')
    expect(wrapper.text()).toContain('1 مشغولة')
  })
})

describe('picking a table', () => {
  it('says whether anyone is on it before anything is added', async () => {
    /**
     * Adding to a bill that already belongs to somebody is the mistake that
     * surfaces at closing, when there is no way left to work out which items
     * were whose. So the state is stated, not inferred from a fill colour.
     */
    get.mockResolvedValue([
        table({
          session_id: 's1',
          seated_count: 3,
          order_count: 2,
          total_due: '210.00',
          seated_minutes: 25,
          waiter: 'يوسف',
        }),
      ])

    const wrapper = await mountTables()
    await wrapper.find('.table-card').trigger('click')

    const sheet = wrapper.find('.picked')
    expect(sheet.exists()).toBe(true)
    expect(sheet.text()).toContain('عليها ناس')
    expect(sheet.text()).toContain('210.00')
    expect(sheet.text()).toContain('يوسف')
    expect(push).not.toHaveBeenCalled()
  })

  it('adds to the session already open rather than starting a second one', async () => {
    get.mockResolvedValue([table({ session_id: 's1', seated_count: 3, order_count: 2 })])

    const wrapper = await mountTables()
    await wrapper.find('.table-card').trigger('click')
    const add = wrapper.findAll('button').find((b) => b.text().includes('إضافة إلى'))
    await add!.trigger('click')

    expect(push).toHaveBeenCalledWith({
      name: 'pos-order',
      // No `guests`: the party was counted when they sat, and re-sending it here
      // would silently rewrite a number somebody already took.
      query: { table: 't1', session: 's1', number: '3', guests: undefined },
    })
  })

  it('says a free table is free, and asks the one thing worth asking', async () => {
    const wrapper = await mountTables()
    await wrapper.find('.table-card').trigger('click')

    const sheet = wrapper.find('.picked')
    expect(sheet.text()).toContain('فاضية')
    expect(sheet.text()).toContain('كم شخص؟')
  })

  it('records how many actually sat down', async () => {
    /**
     * The bug this closes: every session opened claiming ONE guest, so a party of
     * four showed as "1 من 4" and the room read emptier than it was. One tap is
     * the whole cost of the truth.
     */
    get.mockResolvedValue([table({ number: '7', seats: 6 })])

    const wrapper = await mountTables()
    await wrapper.find('.table-card').trigger('click')
    await pickGuests(wrapper, '4')

    expect(push).toHaveBeenCalledWith({
      name: 'pos-order',
      query: { table: 't1', session: undefined, number: '7', guests: '4' },
    })
  })

  it('lets a party squeeze one more chair in than the table has', async () => {
    // A picker that cannot express what happened sends the waiter to the wrong
    // number rather than to the right one.
    get.mockResolvedValue([table({ seats: 4 })])

    const wrapper = await mountTables()
    await wrapper.find('.table-card').trigger('click')

    const offered = wrapper.findAll('.guest-choice').map((b) => b.text())
    expect(offered).toEqual(['1', '2', '3', '4', '5'])
  })

  it('closes without seating anybody when cancelled', async () => {
    const wrapper = await mountTables()
    await wrapper.find('.table-card').trigger('click')

    const cancel = wrapper.findAll('button').find((b) => b.text().includes('إلغاء'))
    await cancel!.trigger('click')

    expect(wrapper.find('.picked').exists()).toBe(false)
    expect(push).not.toHaveBeenCalled()
  })
})

describe('what a table shows', () => {
  it('shows what is owed when the party has ordered', async () => {
    get.mockResolvedValue([table({ session_id: 's1', seated_count: 2, order_count: 2, total_due: '184.50' })])

    const wrapper = await mountTables()

    expect(wrapper.text()).toContain('184.50')
    expect(wrapper.text()).toContain('2 من 4')
  })

  it('says so when a party is seated with nothing ordered', async () => {
    // A blank where a total goes reads as zero owed; it needs to say why.
    get.mockResolvedValue([table({ session_id: 's1', seated_count: 2, order_count: 0, seated_minutes: 3 })])

    const wrapper = await mountTables()

    expect(wrapper.text()).toContain('لم يطلب بعد')
  })

  it('flags a table sitting a long time with no order', async () => {
    /**
     * The one state worth marking on a floor. Ten minutes seated and nothing
     * taken is a table somebody has forgotten, and it is the difference between
     * a slow service and a walked customer.
     */
    get.mockResolvedValue([table({ session_id: 's1', order_count: 0, seated_minutes: 14 })])

    const wrapper = await mountTables()

    expect(wrapper.find('.table-card').classes()).toContain('table-neglected')
  })

  it('does not flag a table that has ordered, however long it has sat', async () => {
    get.mockResolvedValue([table({ session_id: 's1', order_count: 3, seated_minutes: 90 })])

    const wrapper = await mountTables()

    expect(wrapper.find('.table-card').classes()).not.toContain('table-neglected')
  })

  it('marks free and busy by more than colour', async () => {
    // The room is read at a glance by people who may not separate two hues, so
    // the state is in the words as well as the fill.
    get.mockResolvedValue([table(), table({ table_id: 't2', number: '4', session_id: 's1', order_count: 1, total_due: '50.00' })])

    const wrapper = await mountTables()

    expect(wrapper.text()).toContain('متاحة')
    expect(wrapper.text()).toContain('50.00')
  })
})

describe('the shape the server actually sends', () => {
  /**
   * `/floor/status/` answers with a bare ARRAY — the client strips the
   * `{success, data}` envelope, so `data` IS the list. This screen read
   * `payload.tables`, which is `undefined` against the real server: a full room
   * came back and rendered as "لا توجد طاولات معرَّفة".
   *
   * The old tests agreed with the mistake, because they mocked the shape the
   * CODE expected rather than the shape the SERVER sends. A whole screen was
   * blank in production and green in CI. Every mock in this file is now the
   * array, and these two say so out loud.
   */
  it('renders the room from a bare array', async () => {
    get.mockResolvedValue([table({ number: '9' })])

    const wrapper = await mountTables()

    expect(wrapper.find('.table-card').exists()).toBe(true)
    expect(wrapper.find('.table-number').text()).toBe('9')
  })

  it('does not read a `tables` key that never arrives', async () => {
    // The exact old bug, pinned: this payload must NOT produce a room.
    get.mockResolvedValue({ tables: [table()] } as unknown as never)

    const wrapper = await mountTables()

    expect(wrapper.find('.table-card').exists()).toBe(false)
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
    get.mockResolvedValue([])

    const wrapper = await mountTables()

    expect(wrapper.text()).toContain('لا توجد طاولات')
  })
})

describe('the plan is drawn, not listed', () => {
  it('places a table where the floor plan puts it', async () => {
    /**
     * `pos_x`/`pos_y` had been in the payload — under a CI geometry guard — since
     * the floor module was built, and nothing drew them. A list tells you a table
     * exists; a plan tells you which one the customer is waving from.
     */
    get.mockResolvedValue([table({ pos_x: 3, pos_y: 1, span_x: 2, span_y: 1 })])

    const wrapper = await mountTables()

    const style = wrapper.find('.table-card').attributes('style') ?? ''
    expect(style).toContain('grid-column: 4 / span 2')
    expect(style).toContain('grid-row: 2 / span 1')
  })

  it('gives each table its own shape', async () => {
    get.mockResolvedValue([table({ shape: 'ROUND' }), table({ table_id: 't2', number: '5', shape: 'BOOTH' })])

    const wrapper = await mountTables()
    const classes = wrapper.findAll('.table-card').map((c) => c.classes())

    expect(classes[0]).toContain('shape-round')
    expect(classes[1]).toContain('shape-booth')
  })

  it('rotates the furniture but never the number', async () => {
    // A number turned 15 degrees is a number you tilt your head to read, and
    // reading at a glance is the entire point of a plan.
    get.mockResolvedValue([table({ rotation: 15 })])

    const wrapper = await mountTables()

    expect(wrapper.find('.table-shape').attributes('style')).toContain('rotate(15deg)')
    expect(wrapper.find('.table-number').attributes('style') ?? '').not.toContain('rotate')
  })

  it('draws each area as its own room', async () => {
    // Two rooms have two coordinate systems. Overlaying them puts table 11 on
    // top of table 1.
    get.mockResolvedValue([table(), table({ table_id: 't2', number: '11', area: 'التراس' })])

    const wrapper = await mountTables()
    const all = wrapper.findAll('.area-tab').find((b) => b.text() === 'الكل')
    await all!.trigger('click')

    expect(wrapper.findAll('.plan-grid')).toHaveLength(2)
  })

  it('opens on ONE room, so that room gets the whole till', async () => {
    /**
     * A screen split three ways is three plans too small to read, and this shell
     * does not scroll — so the space is all there is. A waiter works one room at
     * a time; seeing the rest is a tap.
     */
    get.mockResolvedValue([table(), table({ table_id: 't2', number: '11', area: 'التراس' })])

    const wrapper = await mountTables()

    expect(wrapper.findAll('.plan-grid')).toHaveLength(1)
    expect(wrapper.find('.area-tab-on').text()).toBe('الصالة الداخلية')
  })

  it('does not overrule a room somebody chose, on the next refresh', async () => {
    /**
     * The board reloads every ten seconds. Re-applying the default on each one
     * would snap "الكل" back to a single room under somebody's hand, repeatedly,
     * with nothing on screen admitting it.
     */
    get.mockResolvedValue([table(), table({ table_id: 't2', number: '11', area: 'التراس' })])

    const wrapper = await mountTables()
    const all = wrapper.findAll('.area-tab').find((b) => b.text() === 'الكل')
    await all!.trigger('click')
    expect(wrapper.findAll('.plan-grid')).toHaveLength(2)

    // A refresh lands.
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(wrapper.findAll('.plan-grid')).toHaveLength(2)
  })

  it('collapses the tracks nothing sits on, and keeps them', async () => {
    /**
     * A room's coordinates are sparse — tables cluster on the walls and whole
     * rows in the middle hold nothing. Drawing every track full size spent most
     * of a screen that cannot scroll on floor nobody sits on.
     *
     * Collapsed, not deleted. A gap between two clusters is the aisle, and
     * removing it would make this plan disagree with the admin's drawing of the
     * same room — the one thing a second view of a floor must never do.
     */
    get.mockResolvedValue([table({ pos_x: 2, pos_y: 0, span_x: 1, span_y: 1 })])

    const wrapper = await mountTables()
    const style = wrapper.find('.plan-grid').attributes('style') ?? ''

    // Four columns: two empty, the table's, and the trailing one.
    expect(style).toContain('grid-template-columns: 0.34fr 0.34fr 1fr 0.34fr')
    // Still four tracks — the aisle is narrow, not gone.
    expect(style.match(/fr/g)).toHaveLength(6)
  })

  it('keeps the full detail reachable when the cell is too small to show it', async () => {
    /**
     * The failure `FloorPlanView` recorded when a drawn room was removed before:
     * tables ended up the size the layout dictated rather than the size their
     * information needed. Nine columns on a tablet is ~88px a table — three
     * lines, not six. The rest lives in the title, which is also what a screen
     * reader reads, so nothing is lost.
     */
    get.mockResolvedValue([
        table({ session_id: 's1', seated_count: 3, order_count: 2, total_due: '210.00', seated_minutes: 25, waiter: 'يوسف' }),
      ])

    const wrapper = await mountTables()
    const title = wrapper.find('.table-card').attributes('title') ?? ''

    expect(title).toContain('طاولة 3')
    expect(title).toContain('210.00')
    expect(title).toContain('يوسف')
    expect(title).toContain('منذ')
  })
})
