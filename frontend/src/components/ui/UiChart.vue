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
  BarController,
  BarElement,
  CategoryScale,
  Chart,
  Legend,
  LineController,
  LineElement,
  LinearScale,
  PointElement,
  Tooltip,
} from 'chart.js'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ARABIC_LATIN_DIGITS } from '@/lib/format'

Chart.register(
  BarController,
  BarElement,
  LineController,
  LineElement,
  PointElement,
  CategoryScale,
  LinearScale,
  Tooltip,
  Legend,
)

const props = withDefaults(
  defineProps<{
    labels: string[]
    values: number[]
    kind?: 'bar' | 'horizontal' | 'line'
    /** Formats the tooltip and the value axis. */
    format?: (value: number) => string
    height?: number
  }>(),
  {
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

function build() {
  if (!canvas.value) return
  chart?.destroy()

  const brand = token('--brand-700', '#7b1e28')
  const line = token('--border', '#e7ddcc')
  const inkMuted = token('--ink-muted', '#6b5a52')
  const surface = token('--surface', '#ffffff')
  const horizontal = props.kind === 'horizontal'

  chart = new Chart(canvas.value, {
    type: props.kind === 'line' ? 'line' : 'bar',
    data: {
      labels: props.labels,
      datasets: [
        {
          data: props.values,
          backgroundColor: brand,
          borderColor: brand,
          borderWidth: props.kind === 'line' ? 2 : 0,
          // Rounded at the data end, square at the baseline.
          borderRadius: props.kind === 'line' ? 0 : { topLeft: 4, topRight: 4 },
          borderSkipped: 'start',
          maxBarThickness: 24,
          pointRadius: 4,
          pointBackgroundColor: brand,
          // The surface ring, so a point stays legible where it crosses a line.
          pointBorderColor: surface,
          pointBorderWidth: 2,
          tension: 0.25,
          fill: false,
        },
      ],
    },
    options: {
      indexAxis: horizontal ? 'y' : 'x',
      responsive: true,
      maintainAspectRatio: false,
      // Arabic UI: Chart.js mirrors its own layout from this.
      locale: ARABIC_LATIN_DIGITS,
      plugins: {
        // One series — the card's title already says what is plotted.
        legend: { display: false },
        tooltip: {
          rtl: true,
          textDirection: 'rtl',
          backgroundColor: token('--ink', '#2a1a16'),
          padding: 10,
          displayColors: false,
          callbacks: {
            label: (ctx) => props.format(Number(ctx.parsed[horizontal ? 'x' : 'y'])),
          },
        },
      },
      scales: {
        x: {
          grid: {
            // A grid on the CATEGORY axis separates nothing; on the value axis
            // it carries the numbers that were not directly labelled.
            display: horizontal,
            color: line,
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
          grid: { display: !horizontal, color: line, drawTicks: false },
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

onMounted(build)
watch(() => [props.labels, props.values, props.kind], build, { deep: true })
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
      <tbody>
        <tr v-for="(label, index) in labels" :key="label">
          <th scope="row">{{ label }}</th>
          <td class="tabular-nums">{{ format(values[index] ?? 0) }}</td>
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
