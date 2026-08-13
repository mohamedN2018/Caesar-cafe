/**
 * One numbering system across the product.
 *
 * `format.ts` has always opened by saying Western digits (0-9) are the default,
 * "that is what Egyptian receipts and accounting use". Money, quantities and
 * percentages honoured it via `en-EG`. Dates and times did not: they used plain
 * `'ar-EG'`, which selects Eastern Arabic numerals.
 *
 * The result was both systems in one line. The POS header showed the clock as
 * ١١:٥١ next to a total of 45.00, and an order list showed Western money beside
 * Eastern timestamps. It was reported simply as "a problem with the numbers",
 * which is exactly what it looks like to somebody using it.
 *
 * `ar-EG-u-nu-latn` keeps the LANGUAGE Arabic — month names, the ص/م marker —
 * and pins the digits to Western. The guard at the bottom is what stops the next
 * `toLocaleString('ar-EG')` from reintroducing it, because this is not the kind
 * of thing anybody notices in review.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import { dateTime, money, percent, quantity, time } from './format'

/** Eastern Arabic-Indic digits, U+0660–U+0669. */
const EASTERN = /[٠-٩]/

const SRC = fileURLToPath(new URL('..', import.meta.url))

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry)
    if (statSync(path).isDirectory()) return sourceFiles(path)
    return /\.(?:vue|ts)$/.test(entry) ? [path] : []
  })
}

/**
 * Blank out comments, keeping the line count so numbers still point somewhere.
 *
 * The same helper `tokens.test.ts` needed, for the same reason: this file and
 * `format.ts` both have to NAME the locale they are warning about, and a guard
 * that flags the paragraph explaining it is a guard somebody switches off.
 */
function withoutComments(source: string): string {
  const blank = (match: string) => match.replace(/[^\n]/g, ' ')
  return source
    .replace(/\/\*[\s\S]*?\*\//g, blank)
    .replace(/<!--[\s\S]*?-->/g, blank)
    .replace(/(^|[^:])\/\/[^\n]*/g, (match, lead) => lead + blank(match.slice(lead.length)))
}

describe('every formatter uses Western digits', () => {
  const AT = '2026-08-12T09:51:00Z'

  it('money', () => {
    expect(money('45.5')).not.toMatch(EASTERN)
    expect(money('45.5')).toContain('45.50')
  })

  it('quantity and percent', () => {
    expect(quantity('18')).not.toMatch(EASTERN)
    expect(percent('12.5')).not.toMatch(EASTERN)
  })

  it('time — the one on the till header, which showed ١١:٥١', () => {
    const rendered = time(AT)
    expect(rendered).not.toMatch(EASTERN)
    expect(rendered).toMatch(/\d/)
  })

  it('dateTime', () => {
    const rendered = dateTime(AT)
    expect(rendered).not.toMatch(EASTERN)
    expect(rendered).toMatch(/\d/)
  })

  it('keeps the Arabic, and only swaps the digits', () => {
    // Arabic letters still present — this is not `en-EG`, which would render the
    // month and the meridiem in English and lose the point.
    expect(dateTime(AT)).toMatch(/[؀-ۿ]/)
  })

  it('renders a null as a dash rather than throwing or printing NaN', () => {
    for (const rendered of [money(null), quantity(null), time(null), dateTime(null)]) {
      expect(rendered).toBe('—')
    }
  })
})

describe('the guard', () => {
  it('finds the source it is meant to be checking', () => {
    expect(sourceFiles(SRC).length).toBeGreaterThan(30)
  })

  it('has no bare ar-EG locale left anywhere', () => {
    /**
     * `'ar-EG'` without the `-u-nu-latn` extension is the bug. Matched on the
     * quoted literal so the explanatory prose in `format.ts` — which has to name
     * the thing it is warning about — does not trip its own guard.
     */
    const offenders = sourceFiles(SRC).flatMap((path) =>
      withoutComments(readFileSync(path, 'utf8'))
        .split('\n')
        .map((line, index) => ({ line, number: index + 1 }))
        .filter(({ line }) => /(['"`])ar-EG\1/.test(line))
        .map(({ number, line }) => `${path.replace(SRC, '')}:${number}  ${line.trim()}`),
    )

    // The message deliberately does not quote the locale it is looking for —
    // this guard scans real code as well as prose, and its own error string is
    // real code.
    expect(
      offenders,
      'import ARABIC_LATIN_DIGITS from @/lib/format instead — the bare Arabic ' +
        'locale renders Eastern digits:\n  ' +
        offenders.join('\n  '),
    ).toEqual([])
  })
})
