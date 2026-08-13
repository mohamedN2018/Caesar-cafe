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
     * document; giving them a DOM would cost a few hundred milliseconds each run
     * to build an environment they ignore. The one file that does need a browser
     * — `stores/terminal`, which keeps the device credential in `localStorage` —
     * asks for it with a `// @vitest-environment happy-dom` docblock of its own.
     *
     * That used to be an `environmentMatchGlobs: [['src/stores/**', …]]` entry
     * here, and it matched only on Linux: on Windows the matcher sees a
     * backslashed absolute path, the glob missed, and the file ran DOM-less. A
     * per-file docblock cannot drift out of step with the file it applies to.
     */
    environment: 'node',
    // Bridges happy-dom's `localStorage` past Node 26's empty stub. See the file.
    setupFiles: ['./vitest.setup.ts'],
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    // Required for HMR to work through the Docker port mapping.
    watch: { usePolling: true },
    /*
     * The browser reaches this through a published port, not the container's.
     *
     * compose maps `${FRONTEND_PORT}:5173`, and this machine sets 5183. Vite's
     * HMR client takes its port from `server.port` unless told otherwise, so it
     * dialled `ws://localhost:5173` from a page served on 5183 and failed:
     *
     *     [vite] failed to connect to websocket.
     *     (browser) localhost:5183/ <--[WebSocket (failing)]--> localhost:5173/
     *
     * The cost was not just the console noise — it is why every change needed a
     * manual reload, which is how a fix gets tested against a stale bundle and
     * reported as not working.
     */
    hmr: { clientPort: Number(process.env.FRONTEND_PORT ?? 5173) },
    /*
     * `/api` and `/media` are proxied to the api container, so the browser only
     * ever talks to this origin.
     *
     * This used to be an absolute `VITE_API_BASE_URL=http://localhost:8010/api/v1`,
     * which meant the API had to be published on a known host port. That is what
     * broke the Dokploy deploy: a fixed host port collides on a shared server, and
     * with one `.env` for both environments there is no value that is guaranteed
     * free in both. Nothing needs a published API port now — Vite reaches
     * `api:8000` over the compose network, server-side.
     *
     * It is also closer to production, which serves the SPA and the API from one
     * origin through Caddy. Same-origin in development means a CORS or cookie
     * problem shows up here rather than after a deploy.
     */
    proxy: {
      '/api': { target: 'http://api:8000', changeOrigin: true },
      '/media': { target: 'http://api:8000', changeOrigin: true },
    },
  },
})
