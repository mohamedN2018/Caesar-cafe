<script setup lang="ts">
/** Skeletons, never a spinner over a blank page: the shape tells the user what is coming. */
withDefaults(defineProps<{ rows?: number }>(), { rows: 5 })
</script>

<template>
  <div class="space-y-3" role="status" aria-label="جارٍ التحميل">
    <div
      v-for="row in rows"
      :key="row"
      class="h-11 animate-pulse rounded-lg bg-surface-sunken"
      :style="{ animationDelay: `${row * 60}ms` }"
    />
  </div>
</template>
