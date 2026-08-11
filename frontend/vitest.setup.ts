/**
 * Guarantee a working `localStorage` in the DOM test environment.
 *
 * The till keeps its device credential in `localStorage`, so `stores/terminal`
 * cannot be tested without one — and on Node 26 there is none to be had. Node 26
 * defines its own `globalThis.localStorage`, which stays `undefined` unless the
 * process was started with `--localstorage-file`; happy-dom's `GlobalWindow`
 * inherits that shadowed property, so `window.localStorage` comes out undefined
 * too, and vitest's global population — which skips any key Node already defines
 * — leaves the empty stub in place.
 *
 * The failure was version-dependent in the worst way: green inside the container
 * (`node:22-alpine`, which has no such global) and nine red tests on a
 * developer's own machine, reported as `Cannot read properties of undefined` at
 * the line that touched storage rather than at the Node version responsible.
 *
 * So rather than depend on which Node is in front of us, the environment gets a
 * storage of its own. It is a faithful stand-in and not a stub: the real
 * `Storage` coerces both keys and values to strings, and code that round-trips a
 * number and gets a number back in tests would pass here and break in a browser.
 *
 * Installed only when the environment did not supply one, so on a Node that has
 * real web storage the genuine implementation is still what gets tested.
 */
class MemoryStorage implements Storage {
  private entries = new Map<string, string>()

  get length(): number {
    return this.entries.size
  }

  key(index: number): string | null {
    return [...this.entries.keys()][index] ?? null
  }

  getItem(key: string): string | null {
    return this.entries.get(String(key)) ?? null
  }

  setItem(key: string, value: string): void {
    this.entries.set(String(key), String(value))
  }

  removeItem(key: string): void {
    this.entries.delete(String(key))
  }

  clear(): void {
    this.entries.clear()
  }

  // `localStorage.someKey` — index access is part of the interface, though this
  // app only ever goes through the named methods.
  [name: string]: unknown
}

if (typeof window !== 'undefined' && !globalThis.localStorage) {
  const storage = new MemoryStorage()
  for (const target of [globalThis, window] as unknown as Record<string, unknown>[]) {
    Object.defineProperty(target, 'localStorage', {
      value: storage,
      configurable: true,
      writable: true,
    })
  }
}
