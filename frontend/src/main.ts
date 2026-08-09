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

createApp(App).use(createPinia()).use(router).use(i18n).mount('#app')

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
