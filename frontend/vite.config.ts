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
  },
})
