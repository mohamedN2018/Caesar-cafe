/**
 * Money arithmetic for the browser — the till's authority on an order total.
 *
 * **Why this file exists at all.** The Desktop client is cancelled, so the
 * browser is now the offline point of sale. An offline till has to compute the
 * total it prints on a receipt and hands to a customer, which means the
 * arithmetic has to exist here — it cannot be a server round trip when the whole
 * point is that the server is unreachable.
 *
 * **Why it is a PORT and not a vendored copy.** `apps/core/money.py` is copied
 * verbatim into the Desktop by `scripts/vendor_shared.py`, and `--check` fails CI
 * if the copies drift. That is the right discipline and it is not available here:
 * there is no artefact to vendor between Python and a browser. The project has
 * already met this exact problem once — the floor geometry exists twice, in
 * TypeScript for CSS and in Python for QPainter, "because there is no artefact to
 * vendor between a browser and Qt", and is tested twice against the same cases.
 *
 * So this follows that precedent, and the safeguard is the same one: **the golden
 * fixture is read across the monorepo, not copied.** `money.test.ts` runs
 * `backend/tests/fixtures/money_cases.json` — the server's own file, the same one
 * `test_money_golden.py` runs — through this implementation. A copy of a fixture
 * is a thing that drifts; a shared read is not.
 *
 * **float never touches money.** `decimal.js`, not `number`. This is the reason
 * the dependency is here rather than integer piasters: the inclusive-VAT path
 * divides by `1 + rate`, and expressing that in integers either loses precision
 * or reimplements a decimal library badly.
 *
 * The order of operations below is fixed and mirrors money.py line for line. Do
 * not reorder it; the fixture was hand-computed against this sequence.
 *
 *     line_gross     = round(unit_price_with_modifiers × quantity)
 *     line_discount  = round(line_gross × line_discount_pct / 100)
 *     line_net       = line_gross − line_discount
 *     order_net      = Σ line_net
 *     order_discount = round(order_net × order_discount_pct / 100)
 *     after_discount = order_net − order_discount
 *     service        = round(after_discount × service_pct / 100)
 *     tax            = f(after_discount, service, taxable_ratio, vat_pct, inclusive)
 *     grand_total    = after_discount + service + tax   (+ rounding adjustment)
 */
import Decimal from 'decimal.js'

/**
 * 28 significant digits, matching Python's default Decimal context.
 *
 * Not cosmetic. `net_of_tax = taxable_base / (1 + vat/100)` is a division whose
 * intermediate precision decides the piaster: decimal.js defaults to 20 digits
 * and Python to 28, and a fixture case can land either side of a half-piaster
 * boundary between the two. Set once, globally, because a per-call precision is
 * one call site away from being forgotten.
 *
 * `ROUND_HALF_UP` is the library default for `toDP`, but stated anyway — this is
 * the rounding mode docs/02 specifies and the one the golden file was computed
 * with, and a default that goes unstated is a default somebody changes.
 */
Decimal.set({ precision: 28, rounding: Decimal.ROUND_HALF_UP })

export const ZERO = new Decimal('0.00')
const HUNDRED = new Decimal(100)

/** A money value, as a string. Strings in, strings out — see `money.py`. */
export type Money = string

export interface TaxRules {
  vat_percent: Money
  vat_enabled: boolean
  vat_inclusive: boolean
  service_percent: Money
  service_enabled: boolean
  rounding_step: Money
}

export interface OrderLine {
  unit_price: Money
  quantity?: Money
  discount_percent?: Money
  modifier_deltas?: Money[]
  tax_exempt?: boolean
}

export interface LineTotals {
  gross: Money
  discount: Money
  net: Money
}

export interface OrderTotals {
  subtotal: Money
  discount_total: Money
  service_total: Money
  tax_total: Money
  grand_total: Money
  /** The difference `rounding_step` introduced. Reported, never hidden. */
  rounding_adjustment: Money
  lines: LineTotals[]
}

/** Round to 2 decimal places, half away from zero. */
export function quantizeMoney(value: Decimal): Decimal {
  return value.toDecimalPlaces(2, Decimal.ROUND_HALF_UP)
}

/**
 * Round to the nearest multiple of `step`.
 *
 * `finance.rounding_step` — a cafe that has abolished small coins sets 0.25 and
 * every total lands on a quarter pound. A step of 0.01 is a no-op beyond normal
 * quantization.
 */
export function roundToStep(value: Decimal, step: Decimal): Decimal {
  if (step.lte(0)) return quantizeMoney(value)
  const steps = value.div(step).toDecimalPlaces(0, Decimal.ROUND_HALF_UP)
  return quantizeMoney(steps.mul(step))
}

function effectiveVat(rules: TaxRules): Decimal {
  return rules.vat_enabled ? new Decimal(rules.vat_percent) : ZERO
}

function effectiveService(rules: TaxRules): Decimal {
  return rules.service_enabled ? new Decimal(rules.service_percent) : ZERO
}

function effectiveUnitPrice(line: OrderLine): Decimal {
  return (line.modifier_deltas ?? []).reduce(
    (total, delta) => total.plus(new Decimal(delta)),
    new Decimal(line.unit_price),
  )
}

export function computeLine(line: OrderLine): LineTotals {
  const gross = quantizeMoney(effectiveUnitPrice(line).mul(new Decimal(line.quantity ?? '1')))
  const discount = quantizeMoney(gross.mul(new Decimal(line.discount_percent ?? '0')).div(HUNDRED))
  return {
    gross: gross.toFixed(2),
    discount: discount.toFixed(2),
    net: gross.minus(discount).toFixed(2),
  }
}

/**
 * Tax on a mixed order is APPORTIONED.
 *
 * When some lines are exempt, VAT applies to the taxable share of the
 * post-discount amount — service included, because service is itself taxable.
 * Apportioning by value is the defensible reading and keeps a fully-exempt or
 * fully-taxable order exact.
 */
function computeTax(
  base: Decimal,
  taxableRatio: Decimal,
  vatPercent: Decimal,
  inclusive: boolean,
): Decimal {
  if (vatPercent.lte(0) || base.lte(0) || taxableRatio.lte(0)) return ZERO

  const taxableBase = quantizeMoney(base.mul(taxableRatio))

  if (inclusive) {
    // Extract the VAT already contained in the price.
    const netOfTax = taxableBase.div(new Decimal(1).plus(vatPercent.div(HUNDRED)))
    return quantizeMoney(taxableBase.minus(netOfTax))
  }

  return quantizeMoney(taxableBase.mul(vatPercent).div(HUNDRED))
}

export function computeOrder(
  lines: OrderLine[],
  rules: TaxRules,
  orderDiscountPercent: Money = '0',
): OrderTotals {
  const lineTotals = lines.map(computeLine)

  const orderNet = lineTotals.reduce((total, lt) => total.plus(new Decimal(lt.net)), ZERO)
  const lineDiscountTotal = lineTotals.reduce(
    (total, lt) => total.plus(new Decimal(lt.discount)),
    ZERO,
  )

  const orderDiscount = quantizeMoney(orderNet.mul(new Decimal(orderDiscountPercent)).div(HUNDRED))
  const afterDiscount = orderNet.minus(orderDiscount)

  const service = quantizeMoney(afterDiscount.mul(effectiveService(rules)).div(HUNDRED))

  const taxableNet = lineTotals.reduce(
    (total, lt, index) => (lines[index].tax_exempt ? total : total.plus(new Decimal(lt.net))),
    ZERO,
  )
  const taxableRatio = orderNet.isZero() ? ZERO : taxableNet.div(orderNet)

  const tax = computeTax(
    afterDiscount.plus(service),
    taxableRatio,
    effectiveVat(rules),
    rules.vat_inclusive,
  )

  // Inclusive prices already contain VAT, so tax is disclosed rather than added.
  const rawTotal = rules.vat_inclusive
    ? afterDiscount.plus(service)
    : afterDiscount.plus(service).plus(tax)

  const grandTotal = roundToStep(rawTotal, new Decimal(rules.rounding_step))

  return {
    subtotal: quantizeMoney(orderNet).toFixed(2),
    discount_total: quantizeMoney(lineDiscountTotal.plus(orderDiscount)).toFixed(2),
    service_total: service.toFixed(2),
    tax_total: tax.toFixed(2),
    grand_total: grandTotal.toFixed(2),
    rounding_adjustment: quantizeMoney(grandTotal.minus(rawTotal)).toFixed(2),
    lines: lineTotals,
  }
}
