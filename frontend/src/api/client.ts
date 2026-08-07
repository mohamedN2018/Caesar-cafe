/**
 * Axios client for the Caesar API.
 *
 * Every response is enveloped ({success, data, meta}), so unwrapping happens in
 * exactly one place. Callers receive `data` and never think about the envelope.
 */
import axios, { AxiosError } from 'axios'

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
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '/api/v1',
  timeout: 30_000,
  headers: { 'Content-Type': 'application/json' },
})

http.interceptors.response.use(
  (response) => response,
  (error: AxiosError<ApiFailure>) => {
    const payload = error.response?.data

    if (payload && payload.success === false) {
      return Promise.reject(
        new ApiError(
          payload.code,
          payload.message,
          payload.errors ?? {},
          error.response?.status,
          payload.meta?.request_id,
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

export const api = {
  get: <T>(url: string, params?: Record<string, unknown>) =>
    unwrap<T>(http.get(url, { params })),
  post: <T>(url: string, body?: unknown) => unwrap<T>(http.post(url, body)),
  patch: <T>(url: string, body?: unknown) => unwrap<T>(http.patch(url, body)),
  delete: <T>(url: string) => unwrap<T>(http.delete(url)),
  /** Escape hatch for endpoints where `meta` matters (pagination cursors). */
  raw: http,
}
