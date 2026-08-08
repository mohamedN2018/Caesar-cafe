/**
 * Axios client for the Caesar API.
 *
 * Every response is enveloped ({success, data, meta}), so unwrapping happens in
 * exactly one place. Callers receive `data` and never think about the envelope.
 */
import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios'

export interface ApiMeta {
  request_id?: string
  next?: string | null
  previous?: string | null
  count?: number
}

export interface ApiSuccess<T> {
  success: true
  data: T
  meta?: ApiMeta
}

export interface ApiFailure {
  success: false
  /** Localized and safe to display. */
  message: string
  /** Stable machine-readable code — branch on this, never on `message`. */
  code: string
  errors?: Record<string, string[]>
  detail?: Record<string, unknown>
  meta?: ApiMeta
}

/** Thrown for any non-2xx response, carrying the server's stable error code. */
export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly fieldErrors: Record<string, string[]> = {},
    readonly status?: number,
    readonly requestId?: string,
    readonly detail: Record<string, unknown> = {},
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

const ACCESS_KEY = 'caesar.access'
const REFRESH_KEY = 'caesar.refresh'

export const tokens = {
  get access() {
    return localStorage.getItem(ACCESS_KEY)
  },
  get refresh() {
    return localStorage.getItem(REFRESH_KEY)
  },
  set(access: string, refresh: string) {
    localStorage.setItem(ACCESS_KEY, access)
    localStorage.setItem(REFRESH_KEY, refresh)
  },
  clear() {
    localStorage.removeItem(ACCESS_KEY)
    localStorage.removeItem(REFRESH_KEY)
  },
}

const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '/api/v1',
  timeout: 30_000,
  headers: { 'Content-Type': 'application/json' },
})

http.interceptors.request.use((config) => {
  const access = tokens.access
  if (access) config.headers.Authorization = `Bearer ${access}`
  return config
})

/**
 * Single-flight refresh.
 *
 * Several requests can 401 at once when an access token expires. Without this,
 * each would rotate the refresh token independently — and the server treats a
 * superseded refresh token as theft, revoking every session. One shared promise
 * means exactly one rotation.
 */
let refreshing: Promise<string> | null = null

async function rotate(): Promise<string> {
  const refresh = tokens.refresh
  if (!refresh) throw new ApiError('NO_REFRESH_TOKEN', 'انتهت الجلسة')

  const { data } = await axios.post<ApiSuccess<{ access: string; refresh: string }>>(
    `${http.defaults.baseURL}/auth/refresh/`,
    { refresh },
    { headers: { 'Content-Type': 'application/json' } },
  )
  tokens.set(data.data.access, data.data.refresh)
  return data.data.access
}

type RetriableConfig = InternalAxiosRequestConfig & { _retried?: boolean }

http.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<ApiFailure>) => {
    const payload = error.response?.data
    const config = error.config as RetriableConfig | undefined

    const isExpired =
      error.response?.status === 401 &&
      payload?.code === 'TOKEN_EXPIRED' &&
      config &&
      !config._retried

    if (isExpired) {
      config._retried = true
      try {
        refreshing = refreshing ?? rotate().finally(() => (refreshing = null))
        const access = await refreshing
        config.headers.Authorization = `Bearer ${access}`
        return http(config)
      } catch {
        tokens.clear()
        window.dispatchEvent(new CustomEvent('caesar:session-expired'))
      }
    }

    if (payload && payload.success === false) {
      return Promise.reject(
        new ApiError(
          payload.code,
          payload.message,
          payload.errors ?? {},
          error.response?.status,
          payload.meta?.request_id,
          payload.detail ?? {},
        ),
      )
    }

    // No envelope: the request never reached the app (network down, proxy error).
    return Promise.reject(
      new ApiError(
        error.code === 'ECONNABORTED' ? 'TIMEOUT' : 'NETWORK_ERROR',
        'تعذر الاتصال بالخادم. تحقق من الإنترنت ثم أعد المحاولة.',
        {},
        error.response?.status,
      ),
    )
  },
)

async function unwrap<T>(promise: Promise<{ data: ApiSuccess<T> }>): Promise<T> {
  const response = await promise
  return response.data.data
}

/** Generate an Idempotency-Key. Required by every money-moving endpoint. */
export function idempotencyKey(): string {
  return crypto.randomUUID()
}

/**
 * A GET whose absence is a normal outcome.
 *
 * Returns `null` instead of throwing when the server refuses for permission
 * reasons. This exists because of a rule the product takes seriously: **a user
 * is never shown a refusal for something they were never offered.** A screen
 * that renders "ليس لديك صلاحية: inventory.view" is telling somebody off for a
 * request the interface made on their behalf, and the honest answer is simply
 * not to show that section.
 *
 * Use it for the secondary panels of a screen — the stock strip on the
 * dashboard, the stations on the floor. The primary resource of a page belongs
 * behind a route guard instead, so the page never opens at all.
 *
 * Every other failure still throws. An outage is not the same as a "no", and
 * quietly blanking a section during one hides a problem worth showing.
 */
async function optional<T>(promise: Promise<T>): Promise<T | null> {
  try {
    return await promise
  } catch (error) {
    if (error instanceof ApiError && PERMISSION_CODES.has(error.code)) return null
    throw error
  }
}

const PERMISSION_CODES = new Set(['PERMISSION_DENIED', 'FORBIDDEN', 'NOT_AUTHENTICATED'])

export const api = {
  get: <T>(url: string, params?: Record<string, unknown>) => unwrap<T>(http.get(url, { params })),
  post: <T>(url: string, body?: unknown, headers?: Record<string, string>) =>
    unwrap<T>(http.post(url, body, { headers })),
  patch: <T>(url: string, body?: unknown) => unwrap<T>(http.patch(url, body)),
  delete: <T>(url: string) => unwrap<T>(http.delete(url)),
  /** `null` when the server says this caller may not see it. See above. */
  optional: <T>(url: string, params?: Record<string, unknown>) =>
    optional<T>(unwrap<T>(http.get(url, { params }))),
  /** Escape hatch for endpoints where `meta` matters (pagination cursors). */
  raw: http,
}

/** True when this error is the server declining, rather than failing. */
export function isPermissionError(error: unknown): boolean {
  return error instanceof ApiError && PERMISSION_CODES.has(error.code)
}
