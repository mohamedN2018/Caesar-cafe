<script setup lang="ts">
/**
 * A column chart for one series.
 *
 * **One series, so no legend.** A legend box with a single swatch restates the
 * title and costs space; the heading already says what is plotted.
 *
 * The specs here are fixed rather than chosen per chart, which is what keeps
 * every chart in the product looking like one system:
 *
 *   * columns capped at 24px — never filling the slot, so the band's leftover
 *     stays as air;
 *   * a 4px rounded cap and a square baseline, so the mark grows FROM the axis
 *     rather than floating;
 *   * a 2px gap in the surface colour between neighbours, which is what
 *     separates them — never a stroke, which would add ink that is not data;
 *   * hairline solid gridlines one step off the surface, recessive.
 *
 * Labels are selective on purpose. A value on every column is chaos and goes
 * unread, so only the peak is labelled; the axis and the tooltip carry the rest.
 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    /** `{ label, value }` in display order. */
    points: { label: string; value: number }[]
    /** Rendered into the tooltip and the peak label. */
    format?: (value: number) => string
    height?: number
  }>(),
  { format: (value: number) => String(Math.round(value)), height: 180 },
)

const BAR_MAX = 24
const GAP = 2
const GRID_LINES = 4

const peak = computed(() => Math.max(1, ...props.points.map((p) => p.value)))

/** Rounded up to a clean number, so the axis reads 0 / 500 / 1,000. */
const ceiling = computed(() => {
  const raw = peak.value
  const magnitude = 10 ** Math.floor(Math.log10(raw))
  return Math.ceil(raw / magnitude) * magnitude
})

const ticks = computed(() =>
  Array.from({ length: GRID_LINES + 1 }, (_, i) => (ceiling.value / GRID_LINES) * i).reverse(),
)

const peakIndex = computed(() => props.points.findIndex((p) => p.value === peak.value))

function heightOf(value: number): string {
  return `${Math.max(value > 0 ? 2 : 0, (value / ceiling.value) * 100)}%`
}
</script>

<template>
  <figure class="chart">
    <div class="plot" :style="{ height: `${height}px` }">
      <!-- Gridlines sit behind the marks and carry the values not labelled. -->
      <div class="grid" aria-hidden="true">
        <span v-for="(tick, index) in ticks" :key="index" class="grid-line">
          <em class="grid-tick tabular-nums">{{ format(tick) }}</em>
        </span>
      </div>

      <div class="columns" :style="{ gap: `${GAP}px` }">
        <div
          v-for="(point, index) in points"
          :key="point.label"
          class="slot"
          :title="`${point.label} — ${format(point.value)}`"
        >
          <span
            v-if="index === peakIndex && point.value > 0"
            class="peak tabular-nums"
            aria-hidden="true"
          >
            {{ format(point.value) }}
          </span>
          <span
            class="bar"
            :style="{ height: heightOf(point.value), maxWidth: `${BAR_MAX}px` }"
          />
        </div>
      </div>
    </div>

    <div class="axis" :style="{ gap: `${GAP}px` }">
      <span v-for="point in points" :key="point.label" class="axis-label">{{ point.label }}</span>
    </div>

    <!--
      The table view. Not a fallback — it is how a screen reader, a printout and
      anybody who wants the exact figures reads the same data, which is why the
      chart itself can stay sparse.
    -->
    <details class="table-view">
      <summary>عرض الأرقام</summary>
      <table>
        <tbody>
          <tr v-for="point in points" :key="point.label">
            <th scope="row">{{ point.label }}</th>
            <td class="tabular-nums">{{ format(point.value) }}</td>
          </tr>
        </tbody>
      </table>
    </details>
  </figure>
</template>

<style scoped>
.chart {
  margin: 0;
}

.plot {
  position: relative;
}

.grid {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

.grid-line {
  position: relative;
  display: block;
  /* Hairline, solid, one step off the surface. Never dashed — a dashed grid
     competes with the data for attention. */
  border-top: 1px solid var(--border);
}

.grid-tick {
  position: absolute;
  top: -0.55rem;
  inset-inline-end: 100%;
  padding-inline-end: 0.4rem;
  font-size: 0.68rem;
  font-style: normal;
  color: var(--ink-faint);
}

.columns {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: flex-end;
}

.slot {
  position: relative;
  flex: 1 1 0;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  height: 100%;
}

.bar {
  width: 100%;
  background: var(--brand-700);
  /* Rounded at the data end, square at the baseline: the mark grows out of the
     axis instead of floating above it. */
  border-radius: 4px 4px 0 0;
  transition: opacity 0.12s ease;
}
.slot:hover .bar {
  opacity: 0.78;
}

.peak {
  position: absolute;
  bottom: 100%;
  margin-bottom: 0.2rem;
  font-size: 0.68rem;
  font-weight: 600;
  /* A text token, never the series colour — the bar beside it carries identity. */
  color: var(--ink-muted);
  white-space: nowrap;
}

.axis {
  display: flex;
  margin-top: 0.4rem;
}

.axis-label {
  flex: 1 1 0;
  text-align: center;
  font-size: 0.66rem;
  color: var(--ink-faint);
  overflow: hidden;
}

.table-view {
  margin-top: 0.8rem;
  font-size: 0.8rem;
  color: var(--ink-muted);
}
.table-view summary {
  cursor: pointer;
  color: var(--ink-faint);
}
.table-view table {
  margin-top: 0.5rem;
  width: 100%;
}
.table-view th {
  text-align: start;
  font-weight: 500;
  padding: 0.15rem 0;
}
.table-view td {
  text-align: end;
}
</style>
