/**
 * Where the chairs go.
 *
 * This is arithmetic pretending to be decoration, which is why it is tested.
 * A seat in the wrong place is not a cosmetic bug: the floor view exists so a
 * waiter can seat a walk-in by looking at the room, and a diagram that draws
 * chairs where nobody sits is worse than a list, because a list does not claim
 * to be a picture of the room.
 */
import { describe, expect, it } from 'vitest'

import { footprint, fullness, seatsFor, splitByDepth } from './geometry'

describe('a round table', () => {
  it('spaces its chairs evenly around the circle', () => {
    const seats = seatsFor('ROUND', 4)
    expect(seats).toHaveLength(4)

    // Four seats at the compass points: each roughly a quarter turn on.
    const angles = seats.map((s) => Math.round(Math.atan2(s.y, s.x) * (180 / Math.PI)))
    expect(angles).toEqual([-90, 0, 90, 180])
  })

  it('starts at the top, so seat one is the one facing the door', () => {
    const [first] = seatsFor('ROUND', 6)
    expect(Math.round(first.x * 100)).toBe(0)
    expect(first.y).toBeLessThan(0)
  })

  it('keeps every chair the same distance out', () => {
    const distances = seatsFor('ROUND', 7).map((s) => Math.hypot(s.x, s.y).toFixed(3))
    expect(new Set(distances).size).toBe(1)
  })

  it('turns each chair to face the table', () => {
    // The back of a chair points away from the centre, so no two of six chairs
    // around a circle share a facing.
    const angles = seatsFor('ROUND', 6).map((s) => s.angle)
    expect(new Set(angles).size).toBe(6)
  })
})

describe('a rectangle', () => {
  it('puts six chairs three-a-side, not two per edge', () => {
    /**
     * The rule a person laying out furniture uses. Nobody seats two people at
     * the end of a narrow table while the long sides still have room.
     */
    const seats = seatsFor('RECT', 6, 0, 2, 1)
    const top = seats.filter((s) => s.y < 0)
    const bottom = seats.filter((s) => s.y > 0)

    expect(top).toHaveLength(3)
    expect(bottom).toHaveLength(3)
    expect(seats.filter((s) => Math.abs(s.x) > 0.7)).toHaveLength(0)
  })

  it('uses the long side when the table is taller than it is wide', () => {
    const seats = seatsFor('RECT', 6, 0, 1, 2)
    expect(seats.filter((s) => Math.abs(s.x) > 0.7)).toHaveLength(6)
  })

  it('splits four evenly two and two', () => {
    const seats = seatsFor('SQUARE', 4)
    expect(seats.filter((s) => s.y < 0)).toHaveLength(2)
    expect(seats.filter((s) => s.y > 0)).toHaveLength(2)
  })

  it('spaces chairs along a side without stacking them in the corners', () => {
    const xs = seatsFor('RECT', 6, 0, 2, 1)
      .filter((s) => s.y < 0)
      .map((s) => s.x)
    expect(new Set(xs.map((x) => x.toFixed(3))).size).toBe(3)
    expect(Math.max(...xs.map(Math.abs))).toBeLessThan(0.75)
  })
})

describe('a booth', () => {
  it('never puts a chair against the back wall', () => {
    /**
     * A booth is fixed to a wall. A chair behind it is a chair in the wall.
     *
     * "Behind" means sitting ON the back edge — a side chair near the back
     * corner has a negative y and is perfectly fine, which is why this checks
     * the edge rather than the sign.
     */
    const seats = seatsFor('BOOTH', 4)
    expect(seats.filter((s) => s.y <= -0.7 && Math.abs(s.x) < 0.7)).toHaveLength(0)
    expect(seats).toHaveLength(4)
  })

  it('uses the front and both sides', () => {
    const seats = seatsFor('BOOTH', 6)
    expect(seats.filter((s) => s.y >= 0.7)).not.toHaveLength(0)
    expect(seats.filter((s) => s.x <= -0.7)).not.toHaveLength(0)
    expect(seats.filter((s) => s.x >= 0.7)).not.toHaveLength(0)
  })
})

describe('a bar', () => {
  it('is a single row facing the counter', () => {
    const seats = seatsFor('BAR', 6)
    expect(seats).toHaveLength(6)
    expect(new Set(seats.map((s) => s.y)).size).toBe(1)
    expect(seats.every((s) => s.angle === 0)).toBe(true)
  })

  it('is drawn shallow however its span is set', () => {
    const bar = footprint('BAR', 3, 2, 80)
    expect(bar.width).toBe(240)
    expect(bar.height).toBeLessThan(80)
  })
})

describe('occupancy', () => {
  it('seats a party together rather than scattering them', () => {
    /** Two people at a six-top sit next to each other. */
    const seats = seatsFor('ROUND', 6, 2)
    expect(seats.filter((s) => s.occupied)).toHaveLength(2)
    expect(seats[0].occupied && seats[1].occupied).toBe(true)
    expect(seats[2].occupied).toBe(false)
  })

  it('never fills more chairs than the table has', () => {
    // A guest count above the seat count is a data problem, not a reason to
    // draw a seventh chair on a six-top.
    const seats = seatsFor('ROUND', 4, 9)
    expect(seats.filter((s) => s.occupied)).toHaveLength(4)
  })

  it('treats a negative count as empty', () => {
    expect(seatsFor('ROUND', 4, -3).filter((s) => s.occupied)).toHaveLength(0)
  })

  it('draws nothing for a table with no seats', () => {
    expect(seatsFor('SQUARE', 0)).toEqual([])
  })
})

describe('fullness', () => {
  it('calls an empty table free', () => {
    expect(fullness(4, 0)).toBe('free')
  })

  it('distinguishes two at a six-top from six at a six-top', () => {
    /**
     * The whole reason the view draws chairs. Both are "occupied" on a status
     * board, and only one of them can take a walk-in of four.
     */
    expect(fullness(6, 2)).toBe('light')
    expect(fullness(6, 6)).toBe('full')
  })

  it('calls a mostly-taken table busy', () => {
    expect(fullness(5, 4)).toBe('busy')
  })

  it('never reports beyond full', () => {
    expect(fullness(4, 6)).toBe('full')
  })
})

describe('depth order', () => {
  it('separates the chairs behind the table from the ones in front', () => {
    /**
     * The room is tilted, so a chair at the near edge must be painted OVER the
     * table and one at the far edge under it. Drawing them in one pass tucks
     * the near chairs behind the furniture and the illusion collapses.
     */
    const { behind, infront } = splitByDepth(seatsFor('SQUARE', 4))

    expect(behind).toHaveLength(2)
    expect(infront).toHaveLength(2)
    expect(behind.every((s) => s.y < 0)).toBe(true)
    expect(infront.every((s) => s.y >= 0)).toBe(true)
  })

  it('loses no chairs in the split', () => {
    const seats = seatsFor('ROUND', 7, 3)
    const { behind, infront } = splitByDepth(seats)

    expect(behind.length + infront.length).toBe(seats.length)
    expect([...behind, ...infront].filter((s) => s.occupied)).toHaveLength(3)
  })

  it('puts a bar row entirely in front of its counter', () => {
    const { behind, infront } = splitByDepth(seatsFor('BAR', 5))
    expect(behind).toHaveLength(0)
    expect(infront).toHaveLength(5)
  })
})

describe('footprint', () => {
  it('grows with the span', () => {
    expect(footprint('RECT', 2, 1, 80)).toEqual({ width: 160, height: 80 })
  })

  it('is square for a one-by-one', () => {
    const cell = footprint('SQUARE', 1, 1, 80)
    expect(cell.width).toBe(cell.height)
  })
})
