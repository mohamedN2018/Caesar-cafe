// @vitest-environment happy-dom
/**
 * The recycle bin screen.
 *
 * Deleting deactivates rather than removes, and deactivated rows were invisible
 * everywhere — recoverable in principle, unreachable in practice. A category
 * switched off by accident was gone from every screen with no route back that
 * did not involve a shell.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.fn()
const post = vi.fn()

vi.mock('@/api/client', () => ({
  api: {
    get: (...args: unknown[]) => get(...args),
    post: (...args: unknown[]) => post(...args),
  },
  // Mirrors the real `(code, message)` signature. A one-argument mock passes at
  // runtime and fails typecheck against the real class — and worse, would let a
  // test assert on a message the app never reads.
  ApiError: class ApiError extends Error {
    constructor(
      readonly code: string,
      message: string,
    ) {
      super(message)
    }
  },
}))

function item(over: Record<string, unknown> = {}) {
  return {
    id: 'c1',
    kind: 'catalog.Category',
    kind_label: 'الأقسام',
    title: 'حلويات',
    deactivated_at: '2026-08-16T10:00:00Z',
    ...over,
  }
}

async function mountBin() {
  const { mount } = await import('@vue/test-utils')
  const { default: View } = await import('./DeletedItemsView.vue')
  const wrapper = mount(View, { global: { stubs: { UiIcon: true } } })
  await new Promise((resolve) => setTimeout(resolve, 0))
  return wrapper
}

beforeEach(() => {
  get.mockReset()
  post.mockReset()
  get.mockResolvedValue({ items: [item()] })
  post.mockResolvedValue({})
})

describe('what the bin shows', () => {
  it('names the thing, not its id', async () => {
    // An operator restoring something has to know what it is. A UUID tells them
    // nothing, and this screen exists precisely for somebody who has just made a
    // mistake and is looking for one specific row.
    const wrapper = await mountBin()

    expect(wrapper.text()).toContain('حلويات')
    expect(wrapper.text()).toContain('الأقسام')
  })

  it('says a row has no delete date rather than inventing one', async () => {
    // Rows deactivated before the timestamp existed have none. A fabricated date
    // on a screen about recovering things would be the wrong kind of tidy.
    get.mockResolvedValue({ items: [item({ deactivated_at: null })] })

    const wrapper = await mountBin()

    expect(wrapper.text()).toContain('—')
  })

  it('explains why deleting behaves this way', async () => {
    // The screen is the only place the rule is visible, and somebody meeting a
    // "recycle bin" in a POS is entitled to know why nothing is ever really gone.
    const wrapper = await mountBin()

    expect(wrapper.text()).toContain('تعطيل وليس إزالة')
  })

  it('shows an empty state instead of a blank panel', async () => {
    get.mockResolvedValue({ items: [] })

    const wrapper = await mountBin()

    expect(wrapper.text()).toContain('لا يوجد شيء محذوف')
  })
})

describe('restoring', () => {
  it('sends the kind as well as the id', async () => {
    /**
     * The id alone is not enough: these rows come from fourteen different
     * models, and the server needs to know which table to look in. Sending only
     * an id would make the endpoint guess.
     */
    const wrapper = await mountBin()

    const button = wrapper.findAll('button').find((b) => b.text().includes('استرجاع'))
    await button!.trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(post).toHaveBeenCalledWith('/system/deleted/restore/', {
      kind: 'catalog.Category',
      id: 'c1',
    })
  })

  it('takes the row out of the bin without a reload', async () => {
    // It is not deleted any more, so leaving it listed would invite a second
    // restore of something already restored.
    const wrapper = await mountBin()
    expect(wrapper.text()).toContain('حلويات')

    const button = wrapper.findAll('button').find((b) => b.text().includes('استرجاع'))
    await button!.trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(wrapper.text()).toContain('تم استرجاع')
  })

  it('keeps the row when the restore fails', async () => {
    // The opposite mistake: removing it optimistically would tell somebody their
    // category is back when it is not, and they would go looking for it.
    const { ApiError } = await import('@/api/client')
    post.mockRejectedValue(new ApiError('RESTORE_FAILED', 'تعذّر الاسترجاع.'))

    const wrapper = await mountBin()
    const button = wrapper.findAll('button').find((b) => b.text().includes('استرجاع'))
    await button!.trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(wrapper.text()).toContain('حلويات')
  })
})
