<script setup lang="ts">
/**
 * An empty state explains the next action rather than announcing "no data" —
 * the second tells the user nothing they did not already know.
 */
defineProps<{ icon?: string; title: string; description?: string }>()
</script>

<template>
  <div class="flex flex-col items-center justify-center px-6 py-14 text-center">
    <div class="mb-3 text-4xl" aria-hidden="true">{{ icon ?? '📭' }}</div>
    <h3 class="text-base font-semibold text-slate-900">{{ title }}</h3>
    <p v-if="description" class="mt-1.5 max-w-sm text-sm text-slate-500">{{ description }}</p>
    <div v-if="$slots.default" class="mt-5"><slot /></div>
  </div>
</template>
