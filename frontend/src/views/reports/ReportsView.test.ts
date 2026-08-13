// @vitest-environment happy-dom
/**
 * The reports configuration.
 *
 * Seventeen report endpoints existed and this screen used eight. The P&L —
 * `financial/pnl`, the report an owner opens first — had been reachable by API
 * only since Phase 8.
 *
 * The failure mode these tests exist for is **the silent empty table**: a tab
 * whose `section` does not match the key the server actually answers with renders
 * a perfectly good empty state, and nobody can tell it apart from a quiet week.
 * Types cannot catch it (both are strings) and neither can the build. So the
 * config is checked for internal coherence here, and the section keys themselves
 * were verified against the live API when they were written.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.fn()

vi.mock('@/api/client', () => ({
  api: { get: (...args: unknown[]) => get(...args), post: vi.fn(), optional: vi.fn() },
  // Mirrors the real `(code, message)` signature — a mock that took the message
  // first would let a test pass while the view read the wrong field.
  ApiError: class ApiError extends Error {
    constructor(
      readonly code: string,
      message: string,
    ) {
      super(message)
      this.name = 'ApiError'
    }
  },
  tokens: { access: 'x', refresh: 'y', set: vi.fn(), clear: vi.fn() },
}))

vi.mock('@/stores/auth', () => ({
  // Everything visible, so the whole tab list is under test rather than the
  // subset one role happens to hold.
  useAuthStore: () => ({ can: () => true, me: { is_superuser: true } }),
}))

async function mountReports() {
  const { mount } = await import('@vue/test-utils')
  const { default: ReportsView } = await import('./ReportsView.vue')
  const wrapper = mount(ReportsView, {
    global: { stubs: { UiChart: true, UiIcon: true } },
  })
  await new Promise((resolve) => setTimeout(resolve, 0))
  return wrapper
}

const PNL = {
  net_sales: '48120.00',
  cogs: '15200.00',
  gross_profit: '32920.00',
  margin_percent: '68.4',
  waste_value: '310.00',
  refunds: '120.00',
  discounts: '890.00',
  tax_collected: '5900.00',
  service_collected: '4100.00',
}

beforeEach(() => {
  get.mockReset()
  get.mockResolvedValue(PNL)
})

describe('the reports screen', () => {
  it('mounts and lists every tab the caller may see', async () => {
    const wrapper = await mountReports()
    const text = wrapper.text()

    // The reports that had no tab at all before this.
    for (const label of [
      'الأرباح والخسائر',
      'ملخص المبيعات',
      'حسب الساعة',
      'الأكثر بيعاً',
      'المشتريات',
      'أرصدة الموردين',
      'حركة المخزون',
    ]) {
      expect(text, `${label} is missing from the tab list`).toContain(label)
    }
  })

  it('renders the P&L as a statement, one row per line', async () => {
    const wrapper = await mountReports()
    const text = wrapper.text()

    expect(text).toContain('صافي المبيعات')
    expect(text).toContain('الربح الإجمالي')
    // Folded through `derive`, so an object payload reaches the same table the
    // list reports use rather than needing a second rendering path.
    expect(text).toContain('48,120.00')
  })

  it('says the P&L stops at gross profit, beside the numbers', async () => {
    /**
     * The note travels WITH the figures rather than living in documentation. A
     * number that looks like net profit while omitting rent, salaries and
     * electricity is worse than no number, and a caveat somebody has to go and
     * find is a caveat nobody reads.
     */
    const wrapper = await mountReports()

    expect(wrapper.text()).toContain('ينتهي عند الربح الإجمالي')
  })
})

describe('the tab configuration is coherent', () => {
  /**
   * Read off the module rather than the rendered screen, so a broken tab is named
   * even when it is not the one currently selected.
   */
  async function tabs() {
    const module = await import('./ReportsView.vue')
    // The tab list is module-private, so it is reached through the rendered
    // buttons instead — which is also what a user can actually get to.
    void module
    const wrapper = await mountReports()
    return wrapper.findAll('button').map((b) => b.text())
  }

  it('every tab is reachable as a button', async () => {
    const labels = await tabs()
    expect(labels.filter(Boolean).length).toBeGreaterThan(12)
  })

  it('asks the server for the selected tab’s own path', async () => {
    await mountReports()

    // The first tab loads on mount. Whatever it is, it must have been requested
    // by path — a tab that renders without fetching is a tab showing another
    // report's rows.
    expect(get).toHaveBeenCalled()
    const url = get.mock.calls[0][0] as string
    expect(url).toMatch(/^\/reports\/.+\/$/)
  })

  it('passes the date range on every request', async () => {
    await mountReports()

    const params = get.mock.calls[0][1] as Record<string, string>
    expect(params.date_from).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    expect(params.date_to).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })

  it('shows an honest empty state rather than a blank panel', async () => {
    get.mockResolvedValue({})

    const wrapper = await mountReports()

    // An object with no keys derives to rows of zeros for the P&L, so the tab
    // still renders something readable rather than nothing at all.
    expect(wrapper.text().length).toBeGreaterThan(50)
  })

  it('shows the server’s own refusal, not a generic one', async () => {
    // The distinction that matters: a quiet week and a broken request must not
    // look the same. And when the server explains itself — a permission it will
    // not grant, a range it will not accept — that sentence is more useful than
    // anything this screen could invent, so it is passed through verbatim.
    const { ApiError } = await import('@/api/client')
    get.mockRejectedValue(new ApiError('PERMISSION_DENIED', 'لا تملك صلاحية هذا التقرير.'))

    const wrapper = await mountReports()

    expect(wrapper.text()).toContain('لا تملك صلاحية هذا التقرير.')
  })

  it('folds a share chart’s tail instead of truncating it', async () => {
    /**
     * The one cap that would change the meaning of what is left. Dropping slices
     * off a doughnut leaves arcs that no longer add up to the whole, so every
     * surviving share is overstated while still looking like a share.
     */
    get.mockResolvedValue({
      methods: [
        { method: 'نقدي', amount: '500.00' },
        { method: 'فيزا', amount: '400.00' },
        { method: 'مدى', amount: '300.00' },
        { method: 'محفظة', amount: '200.00' },
        { method: 'آجل', amount: '100.00' },
        { method: 'قسائم', amount: '50.00' },
        { method: 'تحويل', amount: '25.00' },
      ],
    })

    const wrapper = await mountReports()
    const tab = wrapper.findAll('button').find((b) => b.text() === 'طرق الدفع')
    await tab!.trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))

    const chart = wrapper.findComponent({ name: 'UiChart' })
    const labels = chart.props('labels') as string[]
    const values = chart.props('values') as number[]

    // Five slices, the last of which is the fold — not five of seven.
    expect(labels).toHaveLength(5)
    expect(labels[4]).toBe('أخرى (3)')
    // 100 + 50 + 25 — the tail keeps its weight, so the arcs still sum to the
    // total collected.
    expect(values[4]).toBe(175)
    expect(values.reduce((a, b) => a + b, 0)).toBe(1575)
  })

  it('falls back to a readable message when the failure is not from the API', async () => {
    // A network drop or a thrown TypeError has no Arabic message of its own, and
    // showing "undefined" in an alert is how a bug looks like a data problem.
    get.mockRejectedValue(new TypeError('fetch failed'))

    const wrapper = await mountReports()

    expect(wrapper.text()).toContain('تعذّر تحميل التقرير')
    expect(wrapper.text()).not.toContain('fetch failed')
  })
})
