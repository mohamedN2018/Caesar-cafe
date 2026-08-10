/**
 * Every colour on screen comes from the brand, not from Tailwind's defaults.
 *
 * `brand.css` defines a warm palette — burgundy, gold, cream surfaces, brown
 * ink — and 37 screens were painted in Tailwind's stock `slate`, a grey with a
 * blue cast. Side by side that reads as two different products: warm cards with
 * cool text on them, and a different amber on every screen that needed a
 * warning, because each one reached for whichever step looked right that day.
 *
 * 561 of those classes were swapped for tokens. This is what stops the 562nd,
 * and it is a test rather than a review note because the drift never arrives as
 * a decision — it arrives as one `text-slate-500` in a hurry.
 *
 * The escape hatch is deliberate and narrow: `text-white` and `bg-white` stay
 * legal, because white on a brand fill is not a palette choice.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

const SRC = new URL('../..', import.meta.url).pathname

/**
 * The families that have a brand token and must use it. Tailwind's full default
 * palette is much larger; these are the ones this product actually reached for,
 * and adding a new one is a decision worth making on purpose.
 */
const BANNED = /\b(?:text|bg|border|ring|divide|placeholder|from|to|via)-(?:slate|gray|zinc|neutral|stone|amber|emerald|green|red|rose|sky|blue|indigo)-\d{2,3}\b/

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry)
    if (statSync(path).isDirectory()) return sourceFiles(path)
    return /\.vue$/.test(entry) ? [path] : []
  })
}

describe('colour comes from the brand tokens', () => {
  it('finds the components it is meant to be checking', () => {
    // Guard the guard: a moved directory would make the sweep vacuous.
    expect(sourceFiles(SRC).length).toBeGreaterThan(30)
  })

  it('uses no default-palette colour classes', () => {
    const offenders = sourceFiles(SRC).flatMap((path) =>
      readFileSync(path, 'utf8')
        .split('\n')
        .map((line, index) => ({ line, number: index + 1 }))
        .filter(({ line }) => BANNED.test(line))
        .map(({ line, number }) => `${path.replace(SRC, '')}:${number}  ${line.trim()}`),
    )

    expect(
      offenders,
      `use the brand tokens — ink / surface / line / brand / gold / success / warning / danger / info:\n  ${offenders.join('\n  ')}`,
    ).toEqual([])
  })
})
