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
  neutral: 'bg-slate-100 text-slate-700 ring-slate-200',
  success: 'bg-emerald-50 text-emerald-800 ring-emerald-200',
  warning: 'bg-amber-50 text-amber-800 ring-amber-200',
  danger: 'bg-red-50 text-red-800 ring-red-200',
  info: 'bg-sky-50 text-sky-800 ring-sky-200',
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
