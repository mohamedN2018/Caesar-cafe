/**
 * No emoji in the interface.
 *
 * Emoji were the single thing that made this product look unserious, and they
 * came back three times while they were being removed — a placeholder here, a
 * note prefix there — because each one looks harmless on its own. So the rule is
 * enforced rather than remembered.
 *
 * What is actually wrong with them, beyond taste: they render as a different
 * picture on every platform, they are colourful when nothing around them is,
 * they cannot inherit the colour of the text they sit beside, and their weight
 * and baseline come from a font this project does not control — so they never
 * line up with anything.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

/** See the note in `tokens.test.ts`: `.pathname` breaks on Windows paths. */
const SRC = fileURLToPath(new URL('../..', import.meta.url))

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry)
    if (statSync(path).isDirectory()) return sourceFiles(path)
    return /\.(vue|ts)$/.test(entry) && !entry.endsWith('.test.ts') ? [path] : []
  })
}

/**
 * Where the pictographs live.
 *
 * This used to be `[\u{10000}-\u{10FFFF}]` alone, on the reasoning that "every
 * emoji is a surrogate pair, and Arabic is not". The first half of that is
 * false, and the guard proved it: `UiAlert` — the banner component on every
 * screen in the app — carried ⛔ ⚠️ ℹ️ ✅ through the entire emoji sweep and
 * for weeks after it, because all four sit in the basic plane and the test was
 * only ever looking above U+FFFF. A guard that passes on the worst instance of
 * the thing it guards against is worse than no guard, because it is also a
 * claim that somebody checked.
 *
 * So: the astral plane, plus the basic-plane blocks that are pictures rather
 * than text. What is deliberately NOT banned, because each is real typography
 * this product uses on purpose and banning it would train people to add
 * exceptions until the guard means nothing:
 *
 *   * arrows (U+2190–U+21FF) — "قبل → بعد" in the audit diff, "المشاركة ←" in
 *     the iOS install steps. Direction inside a sentence, not an icon.
 *   * box drawing (U+2500–U+257F) — the `── section ──` rules in comments.
 *   * the em dash, ellipsis, bullet, and Arabic punctuation.
 */
const PICTOGRAPHS = new RegExp(
  [
    '[\\u{10000}-\\u{10FFFF}]', // astral plane: almost all modern emoji
    '[\\u{2460}-\\u{24FF}]', // enclosed alphanumerics — ⓘ ①, the "info" prefix
    '[\\u{25A0}-\\u{25FF}]', // geometric shapes — ▲ ▼ ● ■, delta arrows
    '[\\u{2600}-\\u{27BF}]', // misc symbols + dingbats — ⚠ ⛔ ★ ✅ ✓ ✗
    '[\\u{2B00}-\\u{2BFF}]', // misc symbols and arrows — ⬆ ⭐
    '\\u{FE0F}', // the emoji-presentation selector: a giveaway on its own
    '\\u{20E3}', // combining enclosing keycap — the 1️⃣ family
  ].join('|'),
  'u',
)

describe('the interface uses drawn icons, not emoji', () => {
  it('finds the source it is meant to be checking', () => {
    // Guard the guard: a moved directory would make the sweep below pass by
    // examining nothing at all.
    const files = sourceFiles(SRC)
    expect(files.length).toBeGreaterThan(30)
  })

  it('bans the block that hid ⛔ ⚠ ℹ ✅, and still allows real punctuation', () => {
    // The regression, pinned. `UiAlert`'s four emoji were basic-plane, and the
    // old astral-only pattern called every one of them clean.
    for (const emoji of ['⛔', '⚠️', 'ℹ️', '✅', '▲', '▼', 'ⓘ', '★', '✓']) {
      expect(PICTOGRAPHS.test(emoji), `${emoji} should be caught`).toBe(true)
    }
    // And the typography that must keep working, or the guard gets exceptions
    // bolted onto it until it stops being enforced.
    for (const ok of ['—', '…', '«القيصر»', 'قبل → بعد', '← رجوع', '── قسم ──', '٪', '•']) {
      expect(PICTOGRAPHS.test(ok), `${ok} should be allowed`).toBe(false)
    }
  })

  it('has no emoji anywhere in the app source', () => {
    const offenders = sourceFiles(SRC)
      .map((path) => {
        const lines = readFileSync(path, 'utf8').split('\n')
        const hits = lines
          .map((line, index) => ({ line, number: index + 1 }))
          .filter(({ line }) => PICTOGRAPHS.test(line))
          .map(({ line, number }) => `${path.replace(SRC, '')}:${number}  ${line.trim()}`)
        return hits
      })
      .flat()

    expect(offenders, `use <UiIcon> instead:\n  ${offenders.join('\n  ')}`).toEqual([])
  })
})
