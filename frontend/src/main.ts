import './assets/main.css'

import { createPinia } from 'pinia'
import { createApp } from 'vue'
import { createI18n } from 'vue-i18n'

import App from './App.vue'
import ar from './locales/ar.json'
import en from './locales/en.json'
import { register as registerServiceWorker, watchForRotation } from './modules/push'
import router from './router'

const i18n = createI18n({
  legacy: false,
  locale: 'ar',
  fallbackLocale: 'en',
  messages: { ar, en },
})

/*
 * Mount only once the router has finished its first navigation.
 *
 * This was `createApp(App).use(createPinia()).use(router).use(i18n).mount('#app')`
 * on one line, and it is why the till rendered an empty body.
 *
 * `use(router)` STARTS the initial navigation; it does not finish it. The guard in
 * `router/index.ts` is async and awaits `auth.load()` — a real network round trip
 * to `/auth/me/` — so at the moment `.mount()` ran, the router was still mid
 * install. Vue rendered anyway, `inject(routerKey)` came back empty, and the
 * console said so three times:
 *
 *     [Vue warn]: injection "Symbol(router)" not found.        (useRouter() → undefined)
 *     [Vue warn]: resolveComponent can only be used in render() or setup().
 *     TypeError: Cannot read properties of undefined (reading 'push')
 *
 * The second one is `<RouterView>` and `<RouterLink>` failing to resolve, which is
 * exactly the blank body: the layout's own markup drew fine, and the one element
 * that renders the page could not be found. The third is `leave()` calling
 * `router.push` on an undefined router, so the exit button threw as well.
 *
 * `isReady()` resolves after the first navigation settles, including any awaits
 * inside the guard. `.then()` rather than a top-level `await` so this does not
 * depend on the build target supporting one.
 */
const app = createApp(App).use(createPinia()).use(router).use(i18n)

router.isReady().then(() => app.mount('#app'))

/*
 * Register the worker, but never ask for notification permission here.
 *
 * Registering is invisible and buys the offline shell. ASKING is a one-shot: a
 * browser gives a site one prompt, and a prompt that appears before the owner
 * has seen the app is a prompt they dismiss — after which the only way back is
 * through browser settings nobody finds. The ask lives behind a button on the
 * notifications screen, next to a list of what it will actually send.
 *
 * Mounted first, so a slow registration never delays the first paint.
 */
registerServiceWorker()
watchForRotation()
