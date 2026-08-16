/**
 * Every channel the API knows has a name here.
 *
 * The failure this exists for: the order panel labelled the channel with a
 * ternary chain — three arms and a fallback of "تيك أواي". Adding a fourth
 * channel silently labelled it takeaway on the panel a cashier reads the bill
 * from, and on nothing else, so the two screens disagreed about the same order.
 *
 * The list is read out of the GENERATED api types rather than written here, so
 * this cannot pass by agreeing with itself.
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import { ORDER_TYPE_LABELS, orderTypeLabel } from './orderTypes'

/** The channels `api.d.ts` declares, pulled straight out of the union. */
function channelsFromTheApi(): string[] {
  const path = fileURLToPath(new URL('../types/api.d.ts', import.meta.url))
  const source = readFileSync(path, 'utf8')
  const match = source.match(/OrderTypeEnum:\s*([^;]+);/)
  if (!match) throw new Error('OrderTypeEnum is not in api.d.ts — regenerate the types')
  return [...match[1].matchAll(/"([A-Z_]+)"/g)].map((m) => m[1])
}

describe('channel names', () => {
  it('covers every channel the API declares', () => {
    const missing = channelsFromTheApi().filter((value) => !ORDER_TYPE_LABELS[value])

    expect(
      missing,
      `no Arabic name for ${missing.join(', ')} — these render as a raw code on the bill`,
    ).toEqual([])
  })

  it('finds channels at all', () => {
    // Guard the guard: a rename in api.d.ts would make the sweep vacuous rather
    // than failing, and a check over an empty list passes forever.
    expect(channelsFromTheApi().length).toBeGreaterThanOrEqual(3)
  })

  it('names the external channel as its own thing, not as takeaway', () => {
    expect(orderTypeLabel('EXTERNAL')).toBe('طلب خارجي')
    expect(orderTypeLabel('EXTERNAL')).not.toBe(orderTypeLabel('TAKE_AWAY'))
  })

  it('shows the raw code rather than a blank for a channel it does not know', () => {
    // This build being older than the server is a bug report. A blank is a
    // mystery, and it appears on a receipt somebody is reconciling.
    expect(orderTypeLabel('DRIVE_THROUGH')).toBe('DRIVE_THROUGH')
    expect(orderTypeLabel(null)).toBe('')
  })
})
