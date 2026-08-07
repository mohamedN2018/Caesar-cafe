<script setup lang="ts">
withDefaults(defineProps<{ tone?: 'error' | 'warning' | 'info' | 'success' }>(), { tone: 'error' })

const tones = {
  error: 'bg-red-50 text-red-800 border-red-200',
  warning: 'bg-amber-50 text-amber-900 border-amber-200',
  info: 'bg-sky-50 text-sky-900 border-sky-200',
  success: 'bg-emerald-50 text-emerald-900 border-emerald-200',
}
const icons = { error: '⛔', warning: '⚠️', info: 'ℹ️', success: '✅' }
</script>

<template>
  <div class="flex items-start gap-3 rounded-lg border px-4 py-3 text-sm" :class="tones[tone]" role="alert">
    <span aria-hidden="true">{{ icons[tone] }}</span>
    <div class="flex-1"><slot /></div>
  </div>
</template>
