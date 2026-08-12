/**
 * The browser's money agrees with the server's, to the piaster.
 *
 * This runs `backend/tests/fixtures/money_cases.json` — **the server's own file,
 * read across the monorepo, not a copy.** `test_money_golden.py` runs the same
 * file through `apps/core/money.py`. A copy of a fixture is a thing that drifts;
 * a shared read is not, and that is the entire safeguard behind having money
 * implemented twice.
 *
 * The fixture's own header says every expected value was hand-computed from the
 * documented order of operations, not generated from an implementation: "a case
 * derived from the code under test proves only that the code equals itself."
 *
 * The Desktop met this bar by vendoring `money.py` verbatim, which CI checks for
 * drift. That is not available between Python and a browser, so the precedent this
 * follows is the floor geometry — which also exists twice, for CSS and for
 * QPainter, and is tested twice against the same cases.
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import { computeOrder, type OrderLine, type TaxRules } from './money'

/** The SERVER's fixture. Not `./fixtures/…` — that would be a copy. */
const FIXTURE = fileURLToPath(
  new URL('../../../backend/tests/fixtures/money_cases.json', import.meta.url),
)

interface Expected {
  subtotal: string
  discount_total: string
  service_total: string
  tax_total: string
  grand_total: string
  rounding_adjustment: string
}

interface Case {
  id: string
  description: string
  lines: OrderLine[]
  rules?: Partial<TaxRules>
  order_discount_percent?: string
  expected: Expected
}

interface Fixture {
  defaults: { rules: TaxRules; order_discount_percent: string }
  cases: Case[]
}

const fixture: Fixture = JSON.parse(readFileSync(FIXTURE, 'utf8'))

describe('the golden file', () => {
  it('was actually read, and has real cases in it', () => {
    // Guard the guard: a moved fixture or a bad parse would make every test
    // below vacuous, and a money test that silently checks nothing is the worst
    // possible version of this file.
    expect(fixture.cases.length).toBeGreaterThan(15)
    expect(fixture.defaults.rules.vat_percent).toBe('14.00')
  })

  it('is the server’s own file, not a copy in the frontend tree', () => {
    expect(FIXTURE.replace(/\\/g, '/')).toContain('backend/tests/fixtures/money_cases.json')
  })
})

describe.each(fixture.cases)('$id', (testCase: Case) => {
  const rules: TaxRules = { ...fixture.defaults.rules, ...(testCase.rules ?? {}) }
  const discount = testCase.order_discount_percent ?? fixture.defaults.order_discount_percent

  it(testCase.description, () => {
    const totals = computeOrder(testCase.lines, rules, discount)

    // Compared field by field rather than as one object, so a failure names the
    // figure that disagreed instead of printing two totals blocks side by side.
    for (const field of Object.keys(testCase.expected) as (keyof Expected)[]) {
      expect(totals[field], `${field} disagrees with the server`).toBe(testCase.expected[field])
    }
  })
})

describe('the documented example', () => {
  it('comes to 204.29, the same figure as docs/04, the server and the fixture', () => {
    /**
     * 2× cappuccino at 60 + 1× turkish at 40, 12% service, 14% VAT.
     *
     * This number appears in the POS mock-up in docs/04, in the Phase 1 golden
     * fixture, in the server, and in the Desktop's fold. It is the single figure
     * that proves the browser has joined that list rather than started its own.
     */
    const totals = computeOrder(
      [
        { unit_price: '60.00', quantity: '2' },
        { unit_price: '40.00', quantity: '1' },
      ],
      {
        vat_percent: '14.00',
        vat_enabled: true,
        vat_inclusive: false,
        service_percent: '12.00',
        service_enabled: true,
        rounding_step: '0.01',
      },
    )

    expect(totals.grand_total).toBe('204.29')
    expect(totals.service_total).toBe('19.20')
    expect(totals.tax_total).toBe('25.09')
  })
})

describe('float never touches money', () => {
  it('adds a third of a pound three times without losing a piaster', () => {
    // 0.1 + 0.2 !== 0.3 in IEEE 754. A till that computed totals in `number`
    // would be wrong by a piaster occasionally and unpredictably, which is the
    // worst frequency for a money bug: too rare to reproduce, common enough to
    // show up in a month's reconciliation.
    const totals = computeOrder(
      [
        { unit_price: '0.10', quantity: '1' },
        { unit_price: '0.20', quantity: '1' },
      ],
      {
        vat_percent: '0',
        vat_enabled: false,
        vat_inclusive: false,
        service_percent: '0',
        service_enabled: false,
        rounding_step: '0.01',
      },
    )

    expect(totals.grand_total).toBe('0.30')
  })

  it('keeps a quantity of 0.333 honest against a per-kilo price', () => {
    const totals = computeOrder(
      [{ unit_price: '350.00', quantity: '0.333' }],
      {
        vat_percent: '0',
        vat_enabled: false,
        vat_inclusive: false,
        service_percent: '0',
        service_enabled: false,
        rounding_step: '0.01',
      },
    )

    // 350 × 0.333 = 116.55 exactly.
    expect(totals.grand_total).toBe('116.55')
  })
})
