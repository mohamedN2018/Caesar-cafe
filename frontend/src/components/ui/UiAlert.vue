<script setup lang="ts">
withDefaults(defineProps<{ tone?: 'error' | 'warning' | 'info' | 'success' }>(), { tone: 'error' })

const tones = {
  error: 'bg-danger-bg text-danger border-danger',
  warning: 'bg-warning-bg text-warning border-warning',
  info: 'bg-info-bg text-info border-info',
  success: 'bg-success-bg text-success border-success',
}
const icons = { error: '⛔', warning: '⚠️', info: 'ℹ️', success: '✅' }
</script>

<template>
  <div class="flex items-start gap-3 rounded-lg border px-4 py-3 text-sm" :class="tones[tone]" role="alert">
    <span aria-hidden="true">{{ icons[tone] }}</span>
    <div class="flex-1"><slot /></div>
  </div>
</template>
