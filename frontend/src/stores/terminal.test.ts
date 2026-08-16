/**
 * @vitest-environment happy-dom
 *
 * Declared HERE and not as an `environmentMatchGlobs` entry in `vite.config.ts`.
 * The glob was `src/stores/**`, which matched under Linux and silently did not
 * match on Windows, where vitest hands the matcher a backslashed absolute path —
 * so this file ran without a DOM and all nine tests died on `localStorage` the
 * first time anyone ran the suite outside the container. A file that needs a DOM
 * is the file that should say so; it cannot fall out of step with a path pattern
 * kept somewhere else.
 *
 * The pragma has to be a block comment, and the first one in the file: vitest
 * reads it out of the leading docblock and ignores a `//` line.
 */

/**
 * The browser as a till.
 *
 * The rule everything here defends: **a PIN is only ever accepted from an
 * enrolled device.** So the interesting behaviour is not "does a PIN work" —
 * the server decides that — but the sequencing around it, where a mistake is
 * silent and expensive:
 *
 *   * signing in must mint a fresh DEVICE session first, or the previous
 *     cashier's token is what the request goes out on;
 *   * signing out must end the person and keep the machine;
 *   * a corrupt store must read as "not enrolled", not crash the till on boot.
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'

const { post, tokenSet, tokenClear } = vi.hoisted(() => ({
  post: vi.fn(),
  tokenSet: vi.fn(),
  tokenClear: vi.fn(),
}))

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return {
    ...actual,
    api: { post },
    tokens: { set: tokenSet, clear: tokenClear, access: null, refresh: null },
  }
})

const ENROLLED = {
  device_id: 'dev-1',
  device_secret: 'secret-1',
  branch_name: 'الفرع الرئيسي',
  device_name: 'كاشير الباب',
}

async function store() {
  const { useTerminalStore } = await import('@/stores/terminal')
  return useTerminalStore()
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  vi.resetModules()
  post.mockReset()
  tokenSet.mockReset()
  tokenClear.mockReset()
})

describe('enrolling the machine', () => {
  it('remembers the device so the licence key is asked for once', async () => {
    post.mockResolvedValue(ENROLLED)

    const terminal = await store()
    const ok = await terminal.enrol({
      license_key: 'QSR-AAAA-BBBB-CCCC-DDDD',
      device_name: 'كاشير الباب',
    })

    expect(ok).toBe(true)
    expect(terminal.isEnrolled).toBe(true)
    // Persisted, not merely in memory: a till that forgot on refresh would ask
    // for the licence key every morning, and that key ends up on a sticky note
    // beside the screen.
    expect(JSON.parse(localStorage.getItem('caesar.device') ?? '{}')).toMatchObject({
      device_id: 'dev-1',
    })
  })

  it('says why when the key is refused, and stays un-enrolled', async () => {
    post.mockRejectedValue(new ApiError('LICENSE_INVALID', 'مفتاح غير صالح', {}, 400))

    const terminal = await store()
    const ok = await terminal.enrol({
      license_key: 'nope',
      device_name: 'كاشير',
    })

    expect(ok).toBe(false)
    expect(terminal.isEnrolled).toBe(false)
    expect(terminal.error).toBe('مفتاح غير صالح')
  })

  it('treats an unreadable store as not enrolled rather than crashing', async () => {
    // Re-enrolling costs a minute. A white screen costs the shift.
    localStorage.setItem('caesar.device', '{not json')

    expect((await store()).isEnrolled).toBe(false)
  })
})

describe('signing a person in', () => {
  beforeEach(() => {
    localStorage.setItem('caesar.device', JSON.stringify(ENROLLED))
  })

  it('mints a device session before presenting the PIN', async () => {
    post
      .mockResolvedValueOnce({ access: 'device-access', refresh: 'device-refresh' })
      .mockResolvedValueOnce({ access: 'pos-access', refresh: 'pos-refresh' })

    const terminal = await store()
    const ok = await terminal.signIn({ pin: '4417' })

    expect(ok).toBe(true)
    // The order is the point. Without the first call the PIN would ride out on
    // whatever token was already in the store — the last cashier's — and be
    // authorised as them for that request.
    expect(post.mock.calls[0][0]).toBe('/licensing/device-token/')
    expect(post.mock.calls[1][0]).toBe('/auth/pos-login/')
    expect(post.mock.calls[1][1]).toEqual({ pin: '4417' })
  })

  it('never sends the PIN when the device session is refused', async () => {
    // A revoked till. The PIN must not go out at all — a failed attempt against
    // a dead terminal is still a failed attempt counted against the person.
    post.mockRejectedValueOnce(new ApiError('DEVICE_REVOKED', 'هذا الجهاز غير مفعّل', {}, 401))

    const terminal = await store()
    const ok = await terminal.signIn({ pin: '4417' })

    expect(ok).toBe(false)
    expect(post).toHaveBeenCalledTimes(1)
    expect(terminal.error).toBe('هذا الجهاز غير مفعّل')
  })

  it('sends a badge the same way a PIN goes', async () => {
    post
      .mockResolvedValueOnce({ access: 'device-access', refresh: 'device-refresh' })
      .mockResolvedValueOnce({ access: 'pos-access', refresh: 'pos-refresh' })

    const terminal = await store()
    await terminal.signIn({ badge: 'QSRB1.abc' })

    expect(post.mock.calls[1][1]).toEqual({ badge: 'QSRB1.abc' })
  })

  it('refuses to try at all on a browser that is not a till', async () => {
    localStorage.clear()

    const terminal = await store()

    expect(await terminal.signIn({ pin: '4417' })).toBe(false)
    expect(post).not.toHaveBeenCalled()
  })
})

describe('leaving', () => {
  beforeEach(() => {
    localStorage.setItem('caesar.device', JSON.stringify(ENROLLED))
  })

  it('signing out ends the person and keeps the machine', async () => {
    // The distinction the whole screen exists for: a cashier hands the terminal
    // to the next one, and clearing the enrolment here would mean typing a
    // licence key between shifts.
    const terminal = await store()
    terminal.signOut()

    expect(tokenClear).toHaveBeenCalled()
    expect(terminal.isEnrolled).toBe(true)
  })

  it('forgetting the device is a separate, rarer act', async () => {
    const terminal = await store()
    terminal.forget()

    expect(terminal.isEnrolled).toBe(false)
    expect(localStorage.getItem('caesar.device')).toBeNull()
  })
})
