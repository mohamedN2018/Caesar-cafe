/*
 * The service worker.
 *
 * Two jobs, and the second is the one C11 is about.
 *
 * 1. **Keep the shell openable.** The owner checks this on a phone on Egyptian
 *    mobile data. A cached shell means the app opens and says "offline" rather
 *    than showing the browser's dinosaur, which is the difference between an
 *    app they trust and one they assume is broken.
 *
 * 2. **Receive pushes.** A notification arrives whether or not the app is open,
 *    and tapping it lands on the screen that can act on it.
 *
 * What it deliberately does NOT do: **cache API responses.** Ever. A cached
 * `/reports/dashboard/` is yesterday's takings presented as today's, and an
 * owner making a staffing decision on a stale number is worse off than one who
 * cannot load the page. Money is never served from a cache.
 */

const VERSION = 'caesar-v2'
const SHELL = `${VERSION}-shell`

/* The minimum to render "the app is here, the network is not". Everything else
   is fetched normally and fails normally. */
const SHELL_ASSETS = ['/', '/index.html', '/manifest.webmanifest', '/icons/icon-192.png']

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(SHELL)
      // `reload` so a deploy is picked up rather than re-cached from the cache
      // the previous worker left behind.
      .then((cache) => cache.addAll(SHELL_ASSETS.map((url) => new Request(url, { cache: 'reload' }))))
      .then(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) => Promise.all(names.filter((n) => !n.startsWith(VERSION)).map((n) => caches.delete(n))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', (event) => {
  const { request } = event
  if (request.method !== 'GET') return

  const url = new URL(request.url)
  if (url.origin !== self.location.origin) return

  /* Never the API. A cached total is a wrong total presented confidently. */
  if (url.pathname.startsWith('/api/')) return

  /* Navigations: try the network, fall back to the cached shell. The SPA router
     takes over from there, so a deep link opened offline still lands somewhere
     that can explain itself. */
  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).catch(() => caches.match('/index.html')))
    return
  }

  /* Static assets: cache first, because they are content-hashed by the build
     and a hit is always correct. */
  event.respondWith(
    caches.match(request).then(
      (hit) =>
        hit ||
        fetch(request).then((response) => {
          if (response.ok && response.type === 'basic') {
            const copy = response.clone()
            caches.open(SHELL).then((cache) => cache.put(request, copy))
          }
          return response
        }),
    ),
  )
})

/* ── notifications ───────────────────────────────────────────────────────── */

self.addEventListener('push', (event) => {
  let payload = {}
  try {
    payload = event.data ? event.data.json() : {}
  } catch {
    // A push with no readable body still means something happened. Better a
    // vague notification than silence, because silence looks like the feature
    // not working.
    payload = { title: 'كافيه القيصر', body: 'حدث يحتاج انتباهك.' }
  }

  event.waitUntil(
    self.registration.showNotification(payload.title || 'كافيه القيصر', {
      body: payload.body || '',
      icon: '/icons/icon-192.png',
      badge: '/icons/icon-192.png',
      dir: 'rtl',
      lang: 'ar',
      /* Same tag collapses in the tray. Two evaluations of one late ticket
         should occupy one line, not two — the server dedupes as well, and this
         is the belt to that braces. */
      tag: payload.tag || payload.kind || 'caesar',
      renotify: false,
      data: { url: payload.url || '/' },
      /* No vibration pattern and no `requireInteraction`. This is a business
         alert, not an alarm; making it harder to dismiss than a message from a
         person is how an app earns a long-press and "turn off notifications". */
    }),
  )
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const target = (event.notification.data && event.notification.data.url) || '/'

  /* Focus an open tab if there is one rather than opening a second. An owner
     who taps three notifications should not end up with three copies of the
     app. */
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windows) => {
      for (const client of windows) {
        if (new URL(client.url).origin === self.location.origin && 'focus' in client) {
          client.navigate(target)
          return client.focus()
        }
      }
      return self.clients.openWindow(target)
    }),
  )
})

/* A push service may rotate a subscription without warning. The page listens
   for this and re-registers; without it the phone goes quiet and nobody knows
   why. */
self.addEventListener('pushsubscriptionchange', (event) => {
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windows) => {
      windows.forEach((client) => client.postMessage({ type: 'resubscribe' }))
    }),
  )
})
