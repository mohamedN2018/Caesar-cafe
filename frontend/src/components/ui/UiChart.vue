<script setup lang="ts">
/**
 * Chart.js, wrapped so every chart in the reports looks like the same product.
 *
 * The wrapper exists because Chart.js defaults are generic — grey gridlines
 * that compete with the data, a legend on a single series, a value on every
 * point, tooltips in English number format. Setting those per chart is how a
 * report ends up with six charts that look like six libraries.
 *
 * The rules it enforces are the same ones the dashboard's own columns follow:
 *
 *   * **One series means no legend.** A legend box with a single swatch just
 *     restates the title.
 *   * **Bars are capped and rounded at the data end only**, so the mark grows
 *     out of the axis rather than floating.
 *   * **Gridlines are hairline and one step off the surface**, drawn on the
 *     value axis only — a grid on the category axis separates nothing.
 *   * **Text wears text tokens, never the series colour.** A burgundy bar
 *     beside dark-brown ink is identity; burgundy *text* is just hard to read.
 *
 * Only the pieces actually used are registered. Importing `chart.js/auto`
 * pulls every controller and scale into the bundle — around three times the
 * size — for charts this product does not draw.
 */
import {
  ArcElement,
  BarController,
  BarElement,
  CategoryScale,
  Chart,
  DoughnutController,
  Legend,
  LineController,
  LineElement,
  LinearScale,
  PointElement,
  Tooltip,
} from 'chart.js'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { ARABIC_LATIN_DIGITS } from '@/lib/format'

Chart.register(
  BarController,
  BarElement,
  LineController,
  LineElement,
  PointElement,
  // Share-of-total only. A doughnut is the wrong form for comparing magnitudes —
  // people read angles badly — so it is used ONLY where the question is "what
  // fraction of the whole", and never for more than a handful of slices.
  DoughnutController,
  ArcElement,
  CategoryScale,
  LinearScale,
  Tooltip,
  Legend,
)

/**
 * One measure per chart, however many series.
 *
 * `series` carries two or more measures that share ONE scale — revenue, cost and
 * profit are all money, so they belong together. A measure on a different scale
 * (a percentage beside a currency) gets its own chart: a second y-axis is the
 * single most common charting mistake, because the crossing point of two lines on
 * two scales is an artefact of where the axes were set and means nothing.
 */
export interface Series {
  label: string
  values: number[]
}

const props = withDefaults(
  defineProps<{
    labels: string[]
    /** A single series. Mutually exclusive with `series`. */
    values?: number[]
    /** Two or more series sharing one scale. Gets a legend automatically. */
    series?: Series[]
    kind?: 'bar' | 'horizontal' | 'line' | 'share'
    /** Formats the tooltip and the value axis. */
    format?: (value: number) => string
    height?: number
  }>(),
  {
    values: undefined,
    series: undefined,
    kind: 'bar',
    format: (value: number) => value.toLocaleString('en-EG'),
    height: 260,
  },
)

const canvas = ref<HTMLCanvasElement | null>(null)
let chart: Chart | null = null

/** Read off the stylesheet so a palette change reaches the charts too. */
function token(name: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

/**
 * The categorical series colours, in FIXED ORDER.
 *
 * Assigned by position and never cycled: colour follows the entity, so a filter
 * that drops a series must not repaint the survivors. A fifth series is not a
 * generated hue — fold it into "أخرى" upstream.
 *
 * The values are validated, not chosen: see the note beside `--chart-*` in
 * brand.css for the checks the obvious brand palette failed and the numbers this
 * one scores.
 */
function seriesColours(): string[] {
  return [
    token('--chart-1', '#c44553'),
    token('--chart-2', '#cf9a1c'),
    token('--chart-3', '#0b87ad'),
    token('--chart-4', '#57a02a'),
  ]
}

function build() {
  if (!canvas.value) return
  chart?.destroy()

  const palette = seriesColours()
  const grid = token('--chart-grid', 'rgba(42, 26, 22, 0.08)')
  const inkMuted = token('--ink-muted', '#6b5a52')
  const surface = token('--surface', '#ffffff')
  const horizontal = props.kind === 'horizontal'
  const share = props.kind === 'share'

  const multi = props.series ?? []
  const single = props.values ?? []

  // A share chart splits ONE total across categories, so each slice is a
  // category and takes its own hue. Everything else is one measure per series.
  const datasets = share
    ? [
        {
          data: single,
          backgroundColor: props.labels.map((_, i) => palette[i % palette.length]),
          // A 2px surface gap between fills, so adjacent arcs read as separate
          // marks rather than one shape that changed colour.
          borderColor: surface,
          borderWidth: 2,
        },
      ]
    : (multi.length ? multi : [{ label: '', values: single }]).map((s, index) => {
        const colour = palette[index % palette.length]
        return {
          label: s.label,
          data: s.values,
          backgroundColor: colour,
          borderColor: colour,
          borderWidth: props.kind === 'line' ? 2 : 0,
          // Rounded at the data end, square at the baseline.
          borderRadius: props.kind === 'line' ? 0 : { topLeft: 4, topRight: 4 },
          borderSkipped: 'start' as const,
          maxBarThickness: 24,
          pointRadius: 4,
          pointBackgroundColor: colour,
          // The surface ring, so a point stays legible where it crosses a line.
          pointBorderColor: surface,
          pointBorderWidth: 2,
          tension: 0.25,
          fill: false,
        }
      })

  chart = new Chart(canvas.value, {
    type: share ? 'doughnut' : props.kind === 'line' ? 'line' : 'bar',
    data: { labels: props.labels, datasets },
    options: {
      indexAxis: horizontal ? 'y' : 'x',
      responsive: true,
      maintainAspectRatio: false,
      // Arabic UI: Chart.js mirrors its own layout from this.
      locale: ARABIC_LATIN_DIGITS,
      plugins: {
        // A legend for two or more series, never for one — with a single swatch it
        // just restates the title. For ≥ 2 it is the only thing that makes identity
        // readable without relying on colour alone.
        legend: {
          display: share || datasets.length > 1,
          position: share ? 'right' : 'top',
          align: 'start',
          labels: {
            // Text wears TEXT tokens, never the series colour. The swatch beside
            // it carries the identity; burgundy text is just hard to read.
            color: inkMuted,
            font: { size: 11 },
            boxWidth: 10,
            boxHeight: 10,
            usePointStyle: true,
            pointStyle: 'rectRounded',
          },
        },
        tooltip: {
          rtl: true,
          textDirection: 'rtl',
          backgroundColor: token('--ink', '#2a1a16'),
          padding: 10,
          // A colour chip only when there is more than one thing it could be.
          displayColors: share || datasets.length > 1,
          callbacks: {
            label: (ctx) => {
              const value = share
                ? Number(ctx.parsed)
                : Number(ctx.parsed[horizontal ? 'x' : 'y'])
              const name = ctx.dataset.label
              return name ? `${name}: ${props.format(value)}` : props.format(value)
            },
          },
        },
      },
      // A doughnut has no axes. Passing scale config to one is harmless but
      // misleading to read, so it is left off entirely.
      scales: share
        ? {}
        : {
        x: {
          grid: {
            // A grid on the CATEGORY axis separates nothing; on the value axis
            // it carries the numbers that were not directly labelled.
            display: horizontal,
            color: grid,
            drawTicks: false,
          },
          border: { display: false },
          ticks: {
            color: inkMuted,
            font: { size: 11 },
            callback: horizontal
              ? (value) => props.format(Number(value))
              : function (value) {
                  return this.getLabelForValue(Number(value))
                },
          },
        },
        y: {
          grid: { display: !horizontal, color: grid, drawTicks: false },
          border: { display: false },
          beginAtZero: true,
          ticks: {
            color: inkMuted,
            font: { size: 11 },
            callback: horizontal
              ? function (value) {
                  return this.getLabelForValue(Number(value))
                }
              : (value) => props.format(Number(value)),
          },
        },
      },
    },
  })
}

/**
 * The rows behind the table view, for one series or many.
 *
 * `props.values` is undefined whenever `series` is used, so this cannot read it
 * directly — `values[index]` threw on every multi-series chart, and the type did
 * not catch it because `withDefaults` widens an optional prop to its declared
 * type even when the default is `undefined`.
 */
const tableSeries = computed<Series[]>(() =>
  props.series?.length ? props.series : [{ label: '', values: props.values ?? [] }],
)

onMounted(build)
watch(() => [props.labels, props.values, props.series, props.kind], build, { deep: true })
onBeforeUnmount(() => chart?.destroy())
</script>

<template>
  <div class="chart" :style="{ height: `${height}px` }">
    <canvas ref="canvas" />
  </div>

  <!--
    The table view. Not a fallback — it is how a screen reader, a printout and
    anybody who wants the exact figures reads the same data, which is what lets
    the chart itself stay sparse.
  -->
  <details class="table-view">
    <summary>عرض الأرقام</summary>
    <table>
      <!-- A header row only when there is more than one column to name. -->
      <thead v-if="tableSeries.length > 1">
        <tr>
          <td />
          <th v-for="s in tableSeries" :key="s.label" scope="col">{{ s.label }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(label, index) in labels" :key="label">
          <th scope="row">{{ label }}</th>
          <td v-for="s in tableSeries" :key="s.label" class="tabular-nums">
            {{ format(s.values[index] ?? 0) }}
          </td>
        </tr>
      </tbody>
    </table>
  </details>
</template>

<style scoped>
.chart {
  position: relative;
  width: 100%;
}

.table-view {
  margin-top: 0.6rem;
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
