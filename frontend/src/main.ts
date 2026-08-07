import './assets/main.css'

import { createPinia } from 'pinia'
import { createApp } from 'vue'
import { createI18n } from 'vue-i18n'

import App from './App.vue'
import ar from './locales/ar.json'
import en from './locales/en.json'
import router from './router'

const i18n = createI18n({
  legacy: false,
  locale: 'ar',
  fallbackLocale: 'en',
  messages: { ar, en },
})

createApp(App).use(createPinia()).use(router).use(i18n).mount('#app')
