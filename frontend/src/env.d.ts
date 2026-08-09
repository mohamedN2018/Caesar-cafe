/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'

  // The three type arguments are props, raw bindings, and data. `unknown` for
  // all three is the honest shim: this declaration exists so TypeScript accepts
  // the import at all, and `vue-tsc` reads the real component through the SFC
  // compiler rather than through this. Writing `any` here would be a claim
  // about the component that this file is in no position to make.
  const component: DefineComponent<unknown, unknown, unknown>
  export default component
}

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string
}
interface ImportMeta {
  readonly env: ImportMetaEnv
}
