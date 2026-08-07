/**
 * Session and permissions.
 *
 * `permissions` shapes the UI — which sidebar entries exist, which buttons are
 * enabled. It is NOT a security boundary: the server re-checks every request.
 * Hiding what someone cannot use keeps the interface honest about what it is
 * for; it does not make anything safe.
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { ApiError, api, tokens } from '@/api/client'

export interface Me {
  id: string
  email: string
  full_name_ar: string
  full_name_en: string
  organization_id: string | null
  branch_id: string | null
  kind: string
  is_superuser: boolean
  mfa_enabled: boolean
  has_pin: boolean
  permissions: string[]
  roles: string[]
}

interface TokenPair {
  access: string
  refresh: string
  access_expires_in: number
  refresh_expires_in: number
}

export type LoginOutcome =
  | { status: 'ok' }
  | { status: 'mfa_required' }
  | { status: 'enrolment_required'; enrollmentToken: string }

export const useAuthStore = defineStore('auth', () => {
  const me = ref<Me | null>(null)
  const loading = ref(false)
  const ready = ref(false)

  const isAuthenticated = computed(() => me.value !== null)
  const permissions = computed(() => new Set(me.value?.permissions ?? []))

  /** Superusers hold everything; everyone else needs the explicit code. */
  function can(code: string): boolean {
    if (!me.value) return false
    return me.value.is_superuser || permissions.value.has(code)
  }

  function canAny(...codes: string[]): boolean {
    return codes.some(can)
  }

  async function login(
    email: string,
    password: string,
    mfaCode?: string,
  ): Promise<LoginOutcome> {
    loading.value = true
    try {
      const pair = await api.post<TokenPair>('/auth/login/', {
        email,
        password,
        ...(mfaCode ? { mfa_code: mfaCode } : {}),
      })
      tokens.set(pair.access, pair.refresh)
      await load()
      return { status: 'ok' }
    } catch (error) {
      if (error instanceof ApiError) {
        if (error.code === 'MFA_REQUIRED') return { status: 'mfa_required' }
        if (error.code === 'MFA_ENROLLMENT_REQUIRED') {
          // The server hands back a scoped token so enrolment is reachable —
          // otherwise mandatory MFA would be a deadlock.
          return {
            status: 'enrolment_required',
            enrollmentToken: String(error.detail.enrollment_token ?? ''),
          }
        }
      }
      throw error
    } finally {
      loading.value = false
    }
  }

  async function load(): Promise<void> {
    if (!tokens.access) {
      me.value = null
      ready.value = true
      return
    }
    try {
      me.value = await api.get<Me>('/auth/me/')
    } catch {
      me.value = null
      tokens.clear()
    } finally {
      ready.value = true
    }
  }

  async function logout(): Promise<void> {
    const refresh = tokens.refresh
    try {
      if (refresh) await api.post('/auth/logout/', { refresh })
    } catch {
      // Already invalid server-side is the desired end state.
    } finally {
      tokens.clear()
      me.value = null
    }
  }

  return { me, loading, ready, isAuthenticated, permissions, can, canAny, login, load, logout }
})
