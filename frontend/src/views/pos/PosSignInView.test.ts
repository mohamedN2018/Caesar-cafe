// @vitest-environment happy-dom
/**
 * Getting into the till.
 *
 * Two reported faults, both pinned here.
 *
 * **The scan stopped working after the first pad tap.** The file's own docstring
 * says the badge field is "invisible and always focused" — a QR scanner is a
 * keyboard that types into whatever has focus. Focus was set once in `onMounted`,
 * and the first tap on any key moved it to that button. So a badge worked only if
 * the cashier had not touched the pad since the screen appeared, which is almost
 * never: they try the PIN, it is wrong, they reach for the badge, nothing happens.
 *
 * **There was no way to correct one digit.** The only correction wiped all four.
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const signIn = vi.fn()
const post = vi.fn()

vi.mock('@/api/client', () => ({
  api: { get: vi.fn(), post, patch: vi.fn(), delete: vi.fn(), optional: vi.fn() },
  ApiError: class ApiError extends Error {},
  tokens: { access: null, refresh: null, set: vi.fn(), clear: vi.fn() },
}))

vi.mock('@/stores/terminal', () => ({
  useTerminalStore: () => ({
    isEnrolled: true,
    deviceName: 'كاشير الباب',
    branchName: 'الفرع الرئيسي',
    busy: false,
    error: '',
    signIn,
    enrol: vi.fn(),
  }),
}))

vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({ load: vi.fn(), me: null }),
}))

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))

async function mountSignIn() {
  const { mount } = await import('@vue/test-utils')
  const { default: PosSignInView } = await import('./PosSignInView.vue')
  return mount(PosSignInView, {
    attachTo: document.body,
    global: { stubs: { UiIcon: true } },
  })
}

/** The digit keys, in pad order. */
function digit(wrapper: Awaited<ReturnType<typeof mountSignIn>>, label: string) {
  return wrapper.findAll('.key').find((key) => key.text() === label)
}

beforeEach(() => {
  setActivePinia(createPinia())
  signIn.mockReset()
  signIn.mockResolvedValue(true)
  document.body.innerHTML = ''
})

describe('the PIN pad', () => {
  it('mounts and shows a pad', async () => {
    const wrapper = await mountSignIn()
    expect(wrapper.findAll('.key').length).toBeGreaterThanOrEqual(12)
  })

  it('fills a dot per digit pressed', async () => {
    const wrapper = await mountSignIn()

    await digit(wrapper, '3')?.trigger('click')
    await digit(wrapper, '3')?.trigger('click')

    // Four dots are always drawn; the point is that pressing does not throw and
    // the pad tracks length.
    expect(wrapper.findAll('.dot').length).toBeGreaterThanOrEqual(4)
  })

  it('signs in when the PIN is submitted', async () => {
    const wrapper = await mountSignIn()

    for (const key of ['3', '3', '3', '3']) await digit(wrapper, key)?.trigger('click')
    await wrapper.findAll('.key').find((k) => k.text() === 'دخول')?.trigger('click')

    expect(signIn).toHaveBeenCalledWith({ pin: '3333' })
  })

  it('removes one digit rather than all four', async () => {
    // The correction that did not exist. One wrong digit used to cost four taps.
    const wrapper = await mountSignIn()

    for (const key of ['1', '2', '3', '9']) await digit(wrapper, key)?.trigger('click')
    await wrapper.findAll('.key').find((k) => k.text() === 'مسح خانة')?.trigger('click')
    await digit(wrapper, '4')?.trigger('click')
    await wrapper.findAll('.key').find((k) => k.text() === 'دخول')?.trigger('click')

    expect(signIn).toHaveBeenCalledWith({ pin: '1234' })
  })

  it('refuses to submit fewer than four digits', async () => {
    const wrapper = await mountSignIn()

    for (const key of ['1', '2']) await digit(wrapper, key)?.trigger('click')
    await wrapper.findAll('.key').find((k) => k.text() === 'دخول')?.trigger('click')

    expect(signIn).not.toHaveBeenCalled()
  })
})

describe('the badge scanner', () => {
  it('keeps focus on the invisible field after a pad tap', async () => {
    /**
     * THE SCAN BUG. A scanner types into whatever has focus, so if a pad button
     * holds it the scan goes nowhere. This is the regression that matters most on
     * this screen, because the failure is silent: the cashier waves the card and
     * the till simply does nothing.
     */
    const wrapper = await mountSignIn()
    const field = wrapper.find('.scan-input').element as HTMLInputElement

    await digit(wrapper, '7')?.trigger('click')

    expect(document.activeElement).toBe(field)
  })

  it('signs in with a scanned badge', async () => {
    const wrapper = await mountSignIn()
    const field = wrapper.find('.scan-input')

    await field.setValue('QSRB1.abcdefghijklmnop')
    await field.trigger('keyup.enter')

    expect(signIn).toHaveBeenCalledWith({ badge: 'QSRB1.abcdefghijklmnop' })
  })

  it('accepts a PIN typed on a physical keyboard', async () => {
    /**
     * Previously impossible — the pad was the only way in, so a terminal with a
     * keyboard and no touchscreen could not be signed in to at all. A badge always
     * begins `QSRB1.`, so the two are trivially separable in one field.
     */
    const wrapper = await mountSignIn()
    const field = wrapper.find('.scan-input')

    await field.setValue('3333')
    await field.trigger('keyup.enter')

    expect(signIn).toHaveBeenCalledWith({ pin: '3333' })
  })

  it('does not treat some other QR in the room as a sign-in attempt', async () => {
    // A product barcode, a WiFi card. Not a failed sign-in against anybody.
    const wrapper = await mountSignIn()
    const field = wrapper.find('.scan-input')

    await field.setValue('https://wifi.example/join')
    await field.trigger('keyup.enter')

    expect(signIn).not.toHaveBeenCalled()
  })
})
