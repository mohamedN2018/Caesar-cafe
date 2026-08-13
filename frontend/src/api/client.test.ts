// @vitest-environment happy-dom
/**
 * What the client says when a request does not come back.
 *
 * These were one message: "تعذر الاتصال بالخادم. تحقق من الإنترنت ثم أعد المحاولة."
 * — shown for every failure with no envelope.
 *
 * It sent somebody to check a working connection while the server was restarting.
 * A 502 means the reverse proxy answered, which is proof the network is fine, so
 * pointing at the internet is not merely unhelpful: it names the one component
 * that is demonstrably working, and the real remedy (wait, or look at the server)
 * goes unmentioned.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

type Handler = (error: unknown) => Promise<unknown>

let onRejected: Handler

vi.mock('axios', () => {
  const instance = {
    interceptors: {
      request: { use: vi.fn() },
      response: {
        use: (_ok: unknown, err: Handler) => {
          onRejected = err
        },
      },
    },
    get: vi.fn(),
    post: vi.fn(),
  }
  return { default: { create: () => instance } }
})

/** An axios-shaped rejection with no success envelope in the body. */
function failure(options: { status?: number; code?: string } = {}) {
  return {
    code: options.code,
    response: options.status ? { status: options.status, data: undefined } : undefined,
  }
}

async function messageFor(error: unknown): Promise<{ code: string; message: string }> {
  await import('./client')
  try {
    await onRejected(error)
    throw new Error('the interceptor resolved; it must always reject')
  } catch (thrown) {
    const apiError = thrown as { code: string; message: string }
    return { code: apiError.code, message: apiError.message }
  }
}

beforeEach(() => {
  vi.resetModules()
  Object.defineProperty(navigator, 'onLine', { value: true, configurable: true })
})

describe('a failure with no envelope', () => {
  it('blames the connection only when the browser is actually offline', async () => {
    Object.defineProperty(navigator, 'onLine', { value: false, configurable: true })

    const { code, message } = await messageFor(failure())

    expect(code).toBe('OFFLINE')
    expect(message).toContain('لا يوجد اتصال')
  })

  it('says the server is restarting on a 502, and does not mention the internet', async () => {
    /**
     * The case that caused this. A 502 came back from the proxy while the api
     * container was still starting, and the screen said to check the internet.
     */
    const { code, message } = await messageFor(failure({ status: 502 }))

    expect(code).toBe('SERVER_UNAVAILABLE')
    expect(message).toContain('إعادة التشغيل')
    expect(message, 'a 502 proves the network works — never point at it').not.toContain('الإنترنت')
  })

  it('treats 503 and 504 the same way', async () => {
    for (const status of [503, 504]) {
      const { code } = await messageFor(failure({ status }))
      expect(code, `status ${status}`).toBe('SERVER_UNAVAILABLE')
    }
  })

  it('names a timeout as a timeout', async () => {
    const { code, message } = await messageFor(failure({ code: 'ECONNABORTED' }))

    expect(code).toBe('TIMEOUT')
    expect(message).toContain('وقتاً أطول')
  })

  it('falls back without claiming to know the cause', async () => {
    // No status and the browser thinks it is online: the request did not arrive
    // and this code cannot say why. It should not guess.
    const { code, message } = await messageFor(failure())

    expect(code).toBe('NETWORK_ERROR')
    expect(message).toContain('تعذّر الوصول')
    expect(message).not.toContain('الإنترنت')
  })

  it('always rejects, never resolves', async () => {
    // A response interceptor that resolves on error hands a caller `undefined`
    // where a payload was expected, and the failure surfaces somewhere unrelated.
    await import('./client')

    await expect(onRejected(failure({ status: 500 }))).rejects.toBeDefined()
  })
})
