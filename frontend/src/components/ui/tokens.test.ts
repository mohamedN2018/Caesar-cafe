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
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

/**
 * `fileURLToPath`, not `.pathname`.
 *
 * A file URL's `pathname` is still URL-encoded and still carries the leading
 * slash a Windows path must not have: this repo lives under a directory with a
 * space in it, so `.pathname` produced `/F:/…/Django%20Project/…` and the sweep
 * threw ENOENT on every run outside the Linux container. It failed loudly here
 * only because these guards walk the tree; the danger is a guard that resolves
 * to an EMPTY tree instead and passes while checking nothing.
 */
const SRC = fileURLToPath(new URL('../..', import.meta.url))

/**
 * The families that have a brand token and must use it. Tailwind's full default
 * palette is much larger; these are the ones this product actually reached for,
 * and adding a new one is a decision worth making on purpose.
 */
const BANNED = /\b(?:text|bg|border|ring|divide|placeholder|from|to|via)-(?:slate|gray|zinc|neutral|stone|amber|emerald|green|red|rose|sky|blue|indigo)-\d{2,3}\b/

function filesMatching(dir: string, pattern: RegExp): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry)
    if (statSync(path).isDirectory()) return filesMatching(path, pattern)
    return pattern.test(entry) ? [path] : []
  })
}

function sourceFiles(dir: string): string[] {
  return filesMatching(dir, /\.vue$/)
}

/**
 * Blank out comments, keeping the line count so numbers still point somewhere.
 *
 * Needed because this file's own prose explains the bug by naming the token that
 * caused it, and a guard that flags the paragraph describing it is a guard that
 * gets switched off. Replacing each comment with spaces rather than deleting it
 * keeps every following line on its real number.
 */
function withoutComments(source: string): string {
  const blank = (match: string) => match.replace(/[^\n]/g, ' ')
  return source
    .replace(/\/\*[\s\S]*?\*\//g, blank) // CSS and JS block comments
    .replace(/<!--[\s\S]*?-->/g, blank) // template comments
    .replace(/(^|[^:])\/\/[^\n]*/g, (match, lead) => lead + blank(match.slice(lead.length)))
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

  /**
   * Every `var(--x)` resolves to something.
   *
   * This exists because of a bug that nothing caught: scoped CSS asked for
   * `var(--line)`, which is the TAILWIND colour name, not the custom property —
   * that one is `--border`. An unresolvable `var()` does not fall back and does
   * not warn; it makes the whole declaration `unset`, so `border: 1px solid
   * var(--line)` came out as a heavy ring in the inherited text colour instead
   * of a cream hairline. It looked like a design decision, which is why a
   * screenshot would not have caught it either.
   *
   * A `var()` with a fallback — `var(--x, #fff)` — is exempt: that one has
   * already said what to do when the token is missing.
   */
  it('references only custom properties that are defined', () => {
    const files = filesMatching(SRC, /\.(?:vue|css)$/)
    const code = new Map(files.map((path) => [path, withoutComments(readFileSync(path, 'utf8'))]))

    /**
     * A property counts as defined wherever it is SET — including from a
     * `:style` binding, where it is a quoted object key rather than a CSS
     * declaration: `:style="{ '--ratio': room.ratio }"`.
     *
     * Without the optional quote this reported a component computing its own
     * variable as an undefined token. That is the worst kind of guard failure:
     * the report is wrong, so the habit it teaches is to stop believing it.
     */
    const defined = new Set<string>()
    for (const source of code.values()) {
      for (const match of source.matchAll(/(--[a-z0-9-]+)['"]?\s*:/g)) defined.add(match[1])
    }

    // Guard the guard: if the palette stopped being found, every reference
    // below would look undefined and the failure would be noise, not a finding.
    expect(defined.has('--surface'), 'brand.css was not read').toBe(true)

    const offenders = [...code].flatMap(([path, source]) =>
      source
        .split('\n')
        .flatMap((line, index) =>
          [...line.matchAll(/var\((--[a-z0-9-]+)\s*(,?)/g)]
            .filter(([, name, comma]) => !comma && !defined.has(name))
            .map(([, name]) => `${path.replace(SRC, '')}:${index + 1}  var(${name})`),
        ),
    )

    expect(
      offenders,
      `undefined custom property — check the spelling against brand.css:\n  ${offenders.join('\n  ')}`,
    ).toEqual([])
  })
})
