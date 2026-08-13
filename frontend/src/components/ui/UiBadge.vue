<script setup lang="ts">
/**
 * Status colour is never the only signal — the label always carries the
 * meaning too, for colour-blind users and for the washed-out screens these
 * dashboards actually run on.
 */
withDefaults(defineProps<{ tone?: 'neutral' | 'success' | 'warning' | 'danger' | 'info' }>(), {
  tone: 'neutral',
})

const tones = {
  // `ring-line`, not a bare `ring`. It was `ring` — a WIDTH utility with no
  // colour — so the ring fell through to Tailwind's default, which is blue-500
  // at half opacity. Every neutral badge in the product had a faint blue outline
  // that belongs to no part of this palette. The token guard could not catch it:
  // it looks for banned colour classes, and this was a MISSING one.
  neutral: 'bg-surface-sunken text-ink ring-line',
  success: 'bg-success-bg text-success ring-success',
  warning: 'bg-warning-bg text-warning ring-warning',
  danger: 'bg-danger-bg text-danger ring-danger',
  info: 'bg-info-bg text-info ring-info',
}
</script>

<template>
  <span
    class="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset"
    :class="tones[tone]"
  >
    <slot />
  </span>
</template>
