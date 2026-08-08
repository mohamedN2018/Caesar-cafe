/**
 * Where the chairs go.
 *
 * The floor view draws the room rather than a grid of status squares, which
 * means it has to decide, for a given table, where each seat physically sits.
 * That is arithmetic, not presentation, so it lives here with tests rather than
 * inside a component where it cannot be checked.
 *
 * The rules are the ones a person laying out furniture would use:
 *
 *   * **Round tables** space their chairs evenly around the circle.
 *   * **Rectangles** put chairs on the long sides first and only use the ends
 *     when the sides are full — six chairs around a 2×1 table is 3+3, not
 *     2+2+1+1, because nobody seats two people at the end of a narrow table
 *     while the sides have room.
 *   * **Booths** are against a wall: chairs on three sides, never the back.
 *   * **Bars** are a single row facing the counter.
 *
 * Occupancy fills seats in order rather than at random. A party of two at a
 * six-top sits together, and a diagram that scattered them would be drawing
 * something that does not happen.
 */

export type TableShape = 'ROUND' | 'SQUARE' | 'RECT' | 'BOOTH' | 'BAR'

export interface Seat {
  /** Fraction of the table's own width, measured from its centre. */
  x: number
  /** Fraction of the table's own height, measured from its centre. */
  y: number
  /** Degrees the chair is turned, so its back faces away from the table. */
  angle: number
  occupied: boolean
}

/** How far outside the table edge a chair sits, as a fraction of the table. */
const GAP = 0.78

function ring(count: number, occupied: number): Seat[] {
  return Array.from({ length: count }, (_, index) => {
    // Start at the top and go clockwise, so seat 1 is where a person would say
    // "the one facing the door" rather than an arbitrary point.
    const angle = (index / count) * 360 - 90
    const radians = (angle * Math.PI) / 180
    return {
      x: Math.cos(radians) * GAP,
      y: Math.sin(radians) * GAP,
      angle: angle + 90,
      occupied: index < occupied,
    }
  })
}

/**
 * How many chairs fit along one edge that is `span` grid cells long.
 *
 * A one-cell edge takes two people; each extra cell adds one. That is not a
 * formula from anywhere — it is what a 60 cm chair against a 70 cm table edge
 * comes to, and it produces the layouts people actually recognise: four around
 * a small square, six around a 2×1.
 */
function edgeCapacity(span: number): number {
  return Math.max(2, span + 1)
}

/**
 * Distribute `count` chairs around a rectangle, long sides first.
 *
 * The ends are used only once the long sides are full, because nobody seats two
 * people at the narrow end of a table while the sides still have room. Six
 * around a 2×1 is 3+3, not 2+2+1+1.
 */
function perimeter(count: number, spanX: number, spanY: number, occupied: number): Seat[] {
  const horizontalIsLong = spanX >= spanY
  const longSpan = horizontalIsLong ? spanX : spanY
  const shortSpan = horizontalIsLong ? spanY : spanX

  const perLongSide = edgeCapacity(longSpan)
  const onSides = Math.min(count, perLongSide * 2)
  const firstSide = Math.ceil(onSides / 2)
  const secondSide = onSides - firstSide

  // Whatever will not fit on the sides goes to the ends, split evenly and
  // capped by how wide the end actually is.
  const overflow = count - onSides
  const perEndCap = edgeCapacity(shortSpan)
  const firstEnd = Math.min(perEndCap, Math.ceil(overflow / 2))
  const secondEnd = Math.min(perEndCap, overflow - firstEnd)

  const seats: Seat[] = []
  const place = (n: number, side: 'top' | 'bottom' | 'left' | 'right') => {
    for (let index = 0; index < n; index += 1) {
      // Evenly spaced along the side, inset from the corners.
      const t = (index + 1) / (n + 1) - 0.5
      if (side === 'top') seats.push({ x: t * 1.5, y: -GAP, angle: 180, occupied: false })
      if (side === 'bottom') seats.push({ x: t * 1.5, y: GAP, angle: 0, occupied: false })
      if (side === 'left') seats.push({ x: -GAP, y: t * 1.5, angle: 90, occupied: false })
      if (side === 'right') seats.push({ x: GAP, y: t * 1.5, angle: 270, occupied: false })
    }
  }

  if (horizontalIsLong) {
    place(firstSide, 'top')
    place(secondSide, 'bottom')
    place(firstEnd, 'left')
    place(secondEnd, 'right')
  } else {
    place(firstSide, 'left')
    place(secondSide, 'right')
    place(firstEnd, 'top')
    place(secondEnd, 'bottom')
  }

  return seats.map((seat, index) => ({ ...seat, occupied: index < occupied }))
}

export function seatsFor(
  shape: TableShape,
  count: number,
  occupied = 0,
  spanX = 1,
  spanY = 1,
): Seat[] {
  const seated = Math.max(0, Math.min(occupied, count))
  if (count <= 0) return []

  switch (shape) {
    case 'ROUND':
      return ring(count, seated)

    case 'BAR':
      // One row facing the counter. A bar with chairs behind it is a table.
      return Array.from({ length: count }, (_, index) => ({
        x: ((index + 1) / (count + 1) - 0.5) * 1.7,
        y: GAP,
        angle: 0,
        occupied: index < seated,
      }))

    case 'BOOTH': {
      // Against a wall: three sides, never the back.
      const perSide = Math.ceil(count / 3)
      const seats: Seat[] = []
      for (let index = 0; index < count; index += 1) {
        const side = Math.floor(index / perSide)
        const withinSide = index % perSide
        const t = (withinSide + 1) / (perSide + 1) - 0.5
        if (side === 0) seats.push({ x: t * 1.5, y: GAP, angle: 0, occupied: false })
        else if (side === 1) seats.push({ x: -GAP, y: t * 1.5, angle: 90, occupied: false })
        else seats.push({ x: GAP, y: t * 1.5, angle: 270, occupied: false })
      }
      return seats.map((seat, index) => ({ ...seat, occupied: index < seated }))
    }

    case 'RECT':
    case 'SQUARE':
    default:
      return perimeter(count, spanX, spanY, seated)
  }
}

/** Pixel footprint of a table, before rotation. */
export function footprint(shape: TableShape, spanX: number, spanY: number, cell: number) {
  const width = spanX * cell
  const height = spanY * cell
  // A bar is a counter: long and shallow, whatever its span says.
  if (shape === 'BAR') return { width, height: Math.round(cell * 0.42) }
  return { width, height }
}

/**
 * Split the seats into the ones behind the table and the ones in front.
 *
 * The room is drawn tilted, so a chair at the bottom of a table is NEARER the
 * viewer and must be painted over the table top, while one at the top is behind
 * it and must be painted under. Drawing them all in one pass — which is what
 * the first version did — puts the near chairs behind the furniture and the
 * whole scene stops reading as three-dimensional.
 */
export function splitByDepth(seats: Seat[]): { behind: Seat[]; infront: Seat[] } {
  return {
    behind: seats.filter((seat) => seat.y < 0),
    infront: seats.filter((seat) => seat.y >= 0),
  }
}

export type Fullness = 'free' | 'light' | 'busy' | 'full'

/**
 * How full a table is, as a word.
 *
 * Used for colour AND stated in the label. "4 seats, 2 seated" is the thing a
 * waiter seating a walk-in actually needs; a table that merely reads "occupied"
 * hides four empty chairs.
 */
export function fullness(seats: number, seated: number): Fullness {
  if (seated <= 0) return 'free'
  if (seated >= seats) return 'full'
  return seated / seats >= 0.6 ? 'busy' : 'light'
}
