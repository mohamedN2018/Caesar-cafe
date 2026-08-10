/// <reference types="vitest" />
import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  test: {
    /**
     * `node` by default, a DOM only where one is genuinely needed.
     *
     * The guards in `components/ui` read files off disk and never touch a
     * document; giving them a DOM would cost a few hundred milliseconds each
     * run to build an environment they ignore. The stores are the opposite —
     * `terminal` keeps the device credential in `localStorage`, so it cannot be
     * tested without one.
     */
    environment: 'node',
    environmentMatchGlobs: [['src/stores/**', 'happy-dom']],
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    // Required for HMR to work through the Docker port mapping.
    watch: { usePolling: true },
  },
})
