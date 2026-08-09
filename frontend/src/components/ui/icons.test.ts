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

import { describe, expect, it } from 'vitest'

const SRC = new URL('../..', import.meta.url).pathname

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry)
    if (statSync(path).isDirectory()) return sourceFiles(path)
    return /\.(vue|ts)$/.test(entry) && !entry.endsWith('.test.ts') ? [path] : []
  })
}

/**
 * Astral-plane characters, which is where essentially every emoji lives.
 *
 * Deliberately NOT a curated list of emoji to ban: a list is a thing somebody
 * has to keep adding to, and the next emoji nobody thought of is exactly the
 * one that gets committed. Arabic and the punctuation this product uses are all
 * in the basic plane, so a surrogate pair is a reliable signal on its own.
 */
const ASTRAL = /[\u{10000}-\u{10FFFF}]/u

describe('the interface uses drawn icons, not emoji', () => {
  it('finds the source it is meant to be checking', () => {
    // Guard the guard: a moved directory would make the sweep below pass by
    // examining nothing at all.
    const files = sourceFiles(SRC)
    expect(files.length).toBeGreaterThan(30)
  })

  it('has no emoji anywhere in the app source', () => {
    const offenders = sourceFiles(SRC)
      .map((path) => {
        const lines = readFileSync(path, 'utf8').split('\n')
        const hits = lines
          .map((line, index) => ({ line, number: index + 1 }))
          .filter(({ line }) => ASTRAL.test(line))
          .map(({ line, number }) => `${path.replace(SRC, '')}:${number}  ${line.trim()}`)
        return hits
      })
      .flat()

    expect(offenders, `use <UiIcon> instead:\n  ${offenders.join('\n  ')}`).toEqual([])
  })
})
