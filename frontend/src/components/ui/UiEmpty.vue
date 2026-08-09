<script setup lang="ts">
/**
 * An empty state explains the next action rather than announcing "no data" —
 * the second tells the user nothing they did not already know.
 *
 * The mark is a line icon in a muted disc, not a 4xl emoji. An emoji at that
 * size dominates the panel and pulls the eye away from the sentence that is
 * doing the actual work; a quiet grey glyph frames the text instead of
 * competing with it.
 */
import UiIcon from './UiIcon.vue'

withDefaults(defineProps<{ icon?: string; title: string; description?: string }>(), {
  icon: 'empty',
  description: undefined,
})
</script>

<template>
  <div class="flex flex-col items-center justify-center px-6 py-14 text-center">
    <div
      class="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-slate-100 text-slate-400"
    >
      <UiIcon :name="icon" size="1.5rem" />
    </div>
    <h3 class="text-base font-semibold text-slate-900">{{ title }}</h3>
    <p v-if="description" class="mt-1.5 max-w-sm text-sm text-slate-500">{{ description }}</p>
    <div v-if="$slots.default" class="mt-5"><slot /></div>
  </div>
</template>
