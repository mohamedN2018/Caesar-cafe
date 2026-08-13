/**
 * This browser, as a till.
 *
 * The rule the whole sign-in design rests on is in `accounts/models.py`: **a PIN
 * is only ever accepted from an activated device.** The device proves the
 * request comes from a terminal the branch owns; the PIN only decides which
 * human is standing at it. That is what makes a four-digit secret acceptable —
 * on the open internet it is guessable in an afternoon.
 *
 * So a browser has to become a device before anybody can sign in with a PIN,
 * and it does that once: a manager enters the branch's licence key, the server
 * returns a device id and a secret, and **this machine keeps them**. From then
 * on the tab is "the till by the door" and staff arrive and leave with a PIN.
 *
 * **`localStorage`, deliberately, and it is worth being honest about what that
 * means.** The secret sits in a store any script on this origin can read — so
 * an XSS on the admin app would hand over a device credential. Two things make
 * that an acceptable trade rather than a hole:
 *
 *   * a device credential ALONE takes no money. It can pull the catalogue; it
 *     cannot open an order or take a payment, because those need a human, and
 *     naming a human needs a PIN this store never holds.
 *   * it is revocable from the Devices screen and the middleware re-reads the
 *     device's status on every request, so a stolen credential dies the moment
 *     somebody says so rather than when it expires.
 *
 * `sessionStorage` would be worse, not better: it dies with the tab, and a till
 * that asked for a licence key every morning is a till whose licence key ends
 * up on a sticky note beside it.
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { ApiError, api, tokens } from '@/api/client'

const DEVICE_KEY = 'caesar.device'

interface Enrolment {
  device_id: string
  device_secret: string
  branch_name: string
  device_name: string
}

function load(): Enrolment | null {
  try {
    const raw = localStorage.getItem(DEVICE_KEY)
    return raw ? (JSON.parse(raw) as Enrolment) : null
  } catch {
    // Corrupt or unreadable: treat it as not enrolled rather than crashing the
    // till on boot. Re-enrolling is a minute; a white screen is the shift.
    return null
  }
}

export const useTerminalStore = defineStore('terminal', () => {
  const enrolment = ref<Enrolment | null>(load())
  const busy = ref(false)
  const error = ref('')

  const isEnrolled = computed(() => enrolment.value !== null)
  const branchName = computed(() => enrolment.value?.branch_name ?? '')
  const deviceName = computed(() => enrolment.value?.device_name ?? '')

  /**
   * Turn this browser into a till. Run once, by somebody with the licence key.
   */
  async function enrol(payload: {
    license_key: string
    email: string
    device_name: string
  }): Promise<boolean> {
    busy.value = true
    error.value = ''
    try {
      const data = await api.post<Enrolment & Record<string, unknown>>('/licensing/activate/', {
        ...payload,
        mode: 'POS',
        platform: 'web',
        // Advisory only — the server binds to the issued secret, never to this.
        // Sent so the Devices screen can say which machine a row is.
        fingerprint: navigator.userAgent.slice(0, 120),
      })

      enrolment.value = {
        device_id: data.device_id,
        device_secret: data.device_secret,
        branch_name: data.branch_name,
        device_name: data.device_name,
      }
      localStorage.setItem(DEVICE_KEY, JSON.stringify(enrolment.value))
      return true
    } catch (e) {
      error.value = e instanceof ApiError ? e.message : 'تعذّر تفعيل الجهاز.'
      return false
    } finally {
      busy.value = false
    }
  }

  /**
   * Get a DEVICE session — no human on it yet.
   *
   * This is what `pos-login` authenticates against. It is short-lived by design
   * and re-minted from the stored secret whenever the till needs one, so the
   * long-lived credential is used rarely and the thing on the wire expires.
   */
  async function deviceSession(): Promise<boolean> {
    const held = enrolment.value
    if (!held) return false

    try {
      const pair = await api.post<{ access: string; refresh: string }>(
        '/licensing/device-token/',
        { device_id: held.device_id, device_secret: held.device_secret },
      )
      tokens.set(pair.access, pair.refresh)
      return true
    } catch (e) {
      // A revoked or suspended till lands here. Say so plainly — a cashier
      // staring at a PIN pad that silently refuses has no way to know it is the
      // machine and not their memory.
      error.value = e instanceof ApiError ? e.message : 'هذا الجهاز غير مفعّل.'
      return false
    }
  }

  /** Sign a person in at this till. */
  async function signIn(credential: { pin?: string; badge?: string }): Promise<boolean> {
    busy.value = true
    error.value = ''
    try {
      // Always from a fresh device session: the previous cashier's POS token is
      // in the same store, and signing in on top of it would inherit their
      // permissions for the length of one request.
      if (!(await deviceSession())) return false

      const pair = await api.post<{ access: string; refresh: string }>(
        '/auth/pos-login/',
        credential,
      )
      tokens.set(pair.access, pair.refresh)
      return true
    } catch (e) {
      error.value = e instanceof ApiError ? e.message : 'تعذّر تسجيل الدخول.'
      return false
    } finally {
      busy.value = false
    }
  }

  /**
   * Sign the person out; the TILL stays enrolled.
   *
   * The distinction is the whole point of the screen. A cashier finishing a
   * shift hands the terminal to the next one — clearing the enrolment there
   * would mean typing a licence key between shifts.
   */
  function signOut() {
    tokens.clear()
  }

  /** Un-enrol the machine itself. Rare, and deliberately separate. */
  function forget() {
    localStorage.removeItem(DEVICE_KEY)
    enrolment.value = null
    tokens.clear()
  }

  return {
    enrolment,
    isEnrolled,
    branchName,
    deviceName,
    busy,
    error,
    enrol,
    deviceSession,
    signIn,
    signOut,
    forget,
  }
})
