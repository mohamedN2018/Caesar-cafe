<script setup lang="ts">
defineProps<{ title?: string; subtitle?: string }>()
</script>

<template>
  <section class="rounded-xl border border-line bg-surface shadow-sm">
    <header v-if="title || $slots.actions" class="flex items-center justify-between gap-4 border-b border-line px-5 py-4">
      <div>
        <h2 v-if="title" class="text-base font-semibold text-ink">{{ title }}</h2>
        <p v-if="subtitle" class="mt-0.5 text-sm text-ink-muted">{{ subtitle }}</p>
      </div>
      <div v-if="$slots.actions" class="flex items-center gap-2">
        <slot name="actions" />
      </div>
    </header>
    <div :class="$slots.actions || title ? 'p-5' : 'p-5'">
      <slot />
    </div>
  </section>
</template>
