<script setup lang="ts">
/**
 * One number, and what it is compared to.
 *
 * Three details that look like fussiness and are the difference between a tile
 * that reads and one that is decoration:
 *
 * **Proportional figures, not tabular.** `tabular-nums` gives every digit the
 * width of a zero so columns line up — correct in a table, wrong here. At 30px
 * a standalone `121` rendered tabular looks loose and badly spaced. Tabular is
 * reserved for the table views.
 *
 * **The delta's colour is direction × whether up is good.** Sales up is green
 * and refunds up is not, so `goodDirection` says which way is which rather than
 * the component assuming every number wants to grow. Getting that wrong paints
 * a bad morning green.
 *
 * **The delta always names its period.** "12%+" alone is unreadable — up on
 * what? Every delta carries "أمس" or "الأسبوع الماضي", because a comparison
 * without a baseline is a number pretending to be information.
 */
import { computed } from 'vue'

import UiIcon from './UiIcon.vue'

const props = withDefaults(
  defineProps<{
    label: string
    value: string
    /** Signed percentage. `null` when there is nothing to compare against. */
    delta?: number | null
    /** What the delta is measured against — "أمس", "الأسبوع الماضي". */
    deltaLabel?: string
    /** Whether a RISE is good. False for refunds, voids, waste. */
    goodDirection?: 'up' | 'down'
    hint?: string
  }>(),
  { delta: null, deltaLabel: '', goodDirection: 'up', hint: '' },
)

const tone = computed(() => {
  if (props.delta === null || props.delta === 0) return 'flat'
  const rising = props.delta > 0
  const good = props.goodDirection === 'up' ? rising : !rising
  return good ? 'good' : 'bad'
})

/** `''` when flat — a delta of exactly zero has no direction to point in. */
const arrow = computed(() => {
  if (props.delta === null || props.delta === 0) return ''
  return props.delta > 0 ? 'arrow-up' : 'arrow-down'
})
</script>

<template>
  <div class="stat">
    <p class="stat-label">{{ label }}</p>
    <p class="stat-value">{{ value }}</p>

    <p v-if="delta !== null" class="stat-delta" :class="`is-${tone}`">
      <UiIcon v-if="arrow" :name="arrow" size="0.72rem" />
      {{ Math.abs(delta).toFixed(1) }}%
      <span v-if="deltaLabel" class="stat-since">عن {{ deltaLabel }}</span>
    </p>
    <p v-else-if="hint" class="stat-hint">{{ hint }}</p>
  </div>
</template>

<style scoped>
.stat {
  padding: 1rem 1.15rem;
  border-radius: 0.85rem;
  border: 1px solid var(--border);
  background: var(--surface);
}

.stat-label {
  font-size: 0.82rem;
  font-weight: 500;
  color: var(--ink-muted);
}

.stat-value {
  margin-top: 0.3rem;
  font-size: 1.85rem;
  font-weight: 650;
  line-height: 1.15;
  color: var(--ink);
  /* Deliberately NOT tabular — see the note above. */
  font-variant-numeric: proportional-nums;
}

.stat-delta,
.stat-hint {
  margin-top: 0.4rem;
  font-size: 0.78rem;
}

.stat-delta.is-good {
  color: var(--success);
}
.stat-delta.is-bad {
  color: var(--danger);
}
.stat-delta.is-flat {
  color: var(--ink-faint);
}

.stat-since,
.stat-hint {
  color: var(--ink-faint);
}
</style>
