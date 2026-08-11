<script setup lang="ts">
/**
 * Wide content scrolls inside its own container so the page body never scrolls
 * horizontally — a report table must not break the whole layout.
 */
defineProps<{ columns: { key: string; label: string; align?: 'start' | 'end' }[] }>()
</script>

<template>
  <div class="overflow-x-auto">
    <table class="w-full min-w-full text-sm">
      <thead class="border-b border-line bg-surface-muted">
        <tr>
          <th
            v-for="column in columns"
            :key="column.key"
            scope="col"
            class="whitespace-nowrap px-4 py-3 font-semibold text-ink-muted"
            :class="column.align === 'end' ? 'text-end' : 'text-start'"
          >
            {{ column.label }}
          </th>
        </tr>
      </thead>
      <tbody class="divide-y divide-line">
        <slot />
      </tbody>
    </table>
  </div>
</template>
