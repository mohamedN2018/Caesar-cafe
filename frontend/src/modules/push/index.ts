/**
 * Turning notifications on, from the page's side.
 *
 * The sequence matters and most of it is about not burning the one chance you
 * get. A browser gives a site **one** permission prompt; if the owner dismisses
 * it, the only way back is through browser settings that nobody finds. So the
 * prompt is never fired on page load — it happens when somebody presses a
 * button that says what it is for, which is the only moment they will say yes.
 */

import { api } from '@/api/client'

export type PushState =
  | 'unsupported'
  | 'unconfigured'
  | 'denied'
  | 'available'
  | 'subscribed'

/** The browser has everything Web Push needs. iOS below 16.4 does not. */
export function supported(): boolean {
  return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window
}

/**
 * Installed to the home screen.
 *
 * On iOS this is not cosmetic: Safari refuses Web Push entirely until the site
 * has been added to the home screen, so "enable notifications" has to say
 * "install first" rather than failing with a permissions error nobody can act on.
 */
export function installed(): boolean {
  return (
    window.matchMedia('(display-mode: standalone)').matches ||
    // Safari's own flag, which predates the standard media query.
    (window.navigator as { standalone?: boolean }).standalone === true
  )
}

export async function register(): Promise<ServiceWorkerRegistration | null> {
  if (!('serviceWorker' in navigator)) return null
  try {
    return await navigator.serviceWorker.register('/sw.js', { scope: '/' })
  } catch {
    // A failed registration costs offline support and notifications, not the
    // app. Nothing else should notice.
    return null
  }
}

/**
 * base64url → the bytes `applicationServerKey` insists on.
 *
 * Built on an explicit `ArrayBuffer` rather than `Uint8Array.from`: TypeScript
 * types the latter as possibly backed by a `SharedArrayBuffer`, which the DOM
 * signature does not accept.
 */
function decodeKey(value: string): Uint8Array<ArrayBuffer> {
  const padded = value.padEnd(value.length + ((4 - (value.length % 4)) % 4), '=')
  const raw = atob(padded.replace(/-/g, '+').replace(/_/g, '/'))

  const bytes = new Uint8Array(new ArrayBuffer(raw.length))
  for (let index = 0; index < raw.length; index += 1) {
    bytes[index] = raw.charCodeAt(index)
  }
  return bytes
}

function keyOf(subscription: PushSubscription, name: 'p256dh' | 'auth'): string {
  const key = subscription.getKey(name)
  if (!key) throw new Error(`the subscription has no ${name} key`)
  return btoa(String.fromCharCode(...new Uint8Array(key)))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '')
}

/** A name the owner will recognise in a list of their own devices. */
function describeDevice(): string {
  const agent = navigator.userAgent
  if (/iPhone|iPad/i.test(agent)) return 'iPhone / iPad'
  if (/Android/i.test(agent)) return 'أندرويد'
  if (/Windows/i.test(agent)) return 'ويندوز'
  if (/Mac/i.test(agent)) return 'ماك'
  return 'متصفح'
}

export async function currentState(): Promise<PushState> {
  if (!supported()) return 'unsupported'

  const { configured } = await api.get<{ configured: boolean; public_key: string | null }>(
    '/notifications/vapid-key/',
  )
  if (!configured) return 'unconfigured'

  if (Notification.permission === 'denied') return 'denied'

  const registration = await navigator.serviceWorker.getRegistration()
  const existing = await registration?.pushManager.getSubscription()
  return existing ? 'subscribed' : 'available'
}

/**
 * Ask, subscribe, and tell the server.
 *
 * Returns the resulting state rather than throwing for a refusal: being told no
 * is an ordinary outcome of asking, and the caller renders it rather than
 * catching it.
 */
export async function enable(): Promise<PushState> {
  if (!supported()) return 'unsupported'

  const { public_key: publicKey } = await api.get<{ public_key: string | null }>(
    '/notifications/vapid-key/',
  )
  if (!publicKey) return 'unconfigured'

  const permission = await Notification.requestPermission()
  if (permission !== 'granted') return permission === 'denied' ? 'denied' : 'available'

  const registration = (await navigator.serviceWorker.getRegistration()) ?? (await register())
  if (!registration) return 'unsupported'

  const subscription = await registration.pushManager.subscribe({
    // Required by every browser: a push that cannot be shown to the user is
    // not allowed to be delivered silently.
    userVisibleOnly: true,
    applicationServerKey: decodeKey(publicKey),
  })

  await api.post('/notifications/subscriptions/', {
    endpoint: subscription.endpoint,
    p256dh: keyOf(subscription, 'p256dh'),
    auth: keyOf(subscription, 'auth'),
    label: describeDevice(),
  })

  return 'subscribed'
}

/**
 * Stop this browser being told.
 *
 * Unsubscribed locally AND deleted on the server. Doing only the first leaves
 * the server pushing to an endpoint that silently discards, and the owner
 * wondering why "off" did not take.
 */
export async function disable(subscriptionId?: string): Promise<void> {
  const registration = await navigator.serviceWorker.getRegistration()
  const existing = await registration?.pushManager.getSubscription()
  await existing?.unsubscribe()

  if (subscriptionId) {
    await api.delete(`/notifications/subscriptions/${subscriptionId}/`)
  }
}

/**
 * Re-register after the push service rotates a subscription.
 *
 * The service worker cannot call our API (it has no session), so it posts a
 * message and the page does it. Without this the phone goes quiet and nothing
 * says why.
 */
export function watchForRotation(): void {
  if (!('serviceWorker' in navigator)) return
  navigator.serviceWorker.addEventListener('message', (event) => {
    if (event.data?.type === 'resubscribe') {
      enable().catch(() => undefined)
    }
  })
}
