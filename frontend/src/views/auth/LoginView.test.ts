// @vitest-environment happy-dom
/**
 * The demo panel on the sign-in screen.
 *
 * It exists because the seed's ten accounts were printed to a terminal that
 * nobody deploying through a dashboard reads — a whole café behind a login with
 * no way in.
 *
 * The credentials are shown in full, deliberately. Hiding them and only filling
 * the form on click is the right instinct for a password and the wrong one here:
 * the panel renders ONLY when the server says DEMO_MODE, that same server already
 * hands the entire list to an unauthenticated GET, and somebody demonstrating
 * needs to read an address aloud or type it into a second browser.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.fn()

vi.mock('@/api/client', () => ({
  api: { get: (...args: unknown[]) => get(...args), post: vi.fn() },
  ApiError: class ApiError extends Error {},
  tokens: { access: null, refresh: null, set: vi.fn(), clear: vi.fn() },
}))

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))

vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({ login: vi.fn(), loading: false }),
}))

const ACCOUNTS = [
  { email: 'admin@caesar.deplois.net', password: '', name: 'مدير النظام', role: 'مدير عام', pin: '' },
  {
    email: 'cashier@caesar.test',
    password: 'caesar-demo-2026',
    name: 'منى سعيد',
    role: 'كاشير',
    pin: '3333',
  },
]

async function mountLogin() {
  const { mount } = await import('@vue/test-utils')
  const { default: LoginView } = await import('./LoginView.vue')
  const wrapper = mount(LoginView, { global: { stubs: { UiIcon: true } } })
  await new Promise((resolve) => setTimeout(resolve, 0))
  return wrapper
}

beforeEach(() => {
  get.mockReset()
  get.mockResolvedValue({ demo_mode: true, demo_accounts: ACCOUNTS })
})

describe('the demo panel', () => {
  it('shows the email, in full', async () => {
    // The whole point of the request that added this: an operator has to be able
    // to READ the address, not just have it typed into a field for them.
    const wrapper = await mountLogin()

    expect(wrapper.text()).toContain('admin@caesar.deplois.net')
    expect(wrapper.text()).toContain('cashier@caesar.test')
  })

  it('shows the password beside it', async () => {
    const wrapper = await mountLogin()

    expect(wrapper.text()).toContain('caesar-demo-2026')
  })

  it('says where the password comes from when the server did not send one', async () => {
    // The superuser's password is set by `demo_admin` and can be rotated, so the
    // server sends an empty string rather than a guess. A blank gap would read as
    // "no password"; it needs to say why.
    const wrapper = await mountLogin()

    expect(wrapper.text()).toContain('كلمة المرور من إعدادات الخادم')
  })

  it('shows the PIN for the till', async () => {
    const wrapper = await mountLogin()

    expect(wrapper.text()).toContain('3333')
  })

  it('renders nothing at all when the server says demo mode is off', async () => {
    // The gate that matters. A real install must never render this, and the
    // decision belongs to the server — not to this component.
    get.mockResolvedValue({ demo_mode: false, demo_accounts: [] })

    const wrapper = await mountLogin()

    expect(wrapper.text()).not.toContain('حسابات التجربة')
  })

  it('still renders the login form when the info request fails', async () => {
    // A sign-in screen that will not render because a convenience endpoint is
    // down is worse than one with no demo panel.
    get.mockRejectedValue(new Error('boom'))

    const wrapper = await mountLogin()

    expect(wrapper.text()).toContain('تسجيل الدخول')
  })
})
