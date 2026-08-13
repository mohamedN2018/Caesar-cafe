<script setup lang="ts">
/**
 * A banner with a tone.
 *
 * The mark used to be an emoji per tone — a no-entry sign, a warning triangle,
 * an info circle, a green tick. They survived the emoji sweep because the guard
 * only looked at the astral plane and all four sit in the BMP, which is the
 * whole reason a guard is written against the *class* of thing rather than the
 * instances found on the day. They are drawn now, like the rest, so the mark
 * takes the colour of the tone it belongs to instead of being red when the
 * banner is green.
 *
 * (Described in words rather than shown, because the guard checks this file
 * too — and an allowlist for "comments that mention emoji" is the first step
 * to an allowlist for everything.)
 */
import UiIcon from './UiIcon.vue'

withDefaults(defineProps<{ tone?: 'error' | 'warning' | 'info' | 'success' }>(), { tone: 'error' })

const tones = {
  error: 'bg-danger-bg text-danger border-danger',
  warning: 'bg-warning-bg text-warning border-warning',
  info: 'bg-info-bg text-info border-info',
  success: 'bg-success-bg text-success border-success',
}
const icons = { error: 'alert', warning: 'alert', info: 'info', success: 'check' }
</script>

<template>
  <div class="flex items-start gap-3 rounded-lg border px-4 py-3 text-sm" :class="tones[tone]" role="alert">
    <UiIcon :name="icons[tone]" size="1.05rem" class="mt-0.5 flex-none" />
    <div class="flex-1"><slot /></div>
  </div>
</template>
