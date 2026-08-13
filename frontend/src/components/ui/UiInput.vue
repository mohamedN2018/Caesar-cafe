<script setup lang="ts">
defineProps<{
  label?: string
  hint?: string
  error?: string
  type?: string
  placeholder?: string
  required?: boolean
  disabled?: boolean
  /** Latin-only values (email, keys, URLs) read wrong when mirrored in an RTL page. */
  ltr?: boolean
}>()

const model = defineModel<string | number | null>()
</script>

<template>
  <label class="block">
    <span v-if="label" class="mb-1.5 block text-sm font-medium text-ink">
      {{ label }}
      <span v-if="required" class="text-danger" aria-hidden="true">*</span>
    </span>
    <input
      v-model="model"
      :type="type ?? 'text'"
      :placeholder="placeholder"
      :required="required"
      :disabled="disabled"
      :dir="ltr ? 'ltr' : undefined"
      class="ui-input w-full rounded-lg border-line bg-surface px-3.5 py-2.5 text-[15px] min-h-[44px]
             placeholder:text-ink-faint disabled:bg-surface-muted disabled:text-ink-faint
             focus:outline-none"
      :class="error ? 'border-danger' : 'border-line-strong'"
    />
    <p v-if="error" class="mt-1.5 text-sm text-danger">{{ error }}</p>
    <p v-else-if="hint" class="mt-1.5 text-sm text-ink-muted">{{ hint }}</p>
  </label>
</template>

<style scoped>
/*
   The focus state, in CSS rather than as Tailwind opacity utilities.

   It was `focus:ring-2 focus:ring-brand-700/30` plus a border swap. Two problems.
   The ring drew OUTSIDE the border while the border changed colour underneath it,
   so a focused field grew a two-tone halo; and the `/30` opacity notation on a
   `var()`-backed colour only works because Tailwind rewrites it, which quietly
   fails for any token defined as anything other than a bare hex.

   One inset ring that thickens the existing border, plus a soft outer glow. The
   field looks like it has been picked up rather than outlined twice.
*/
.ui-input {
  transition:
    border-color var(--duration-fast) var(--ease-out),
    box-shadow var(--duration-fast) var(--ease-out);
}

.ui-input:hover:not(:disabled):not(:focus) {
  border-color: var(--brand-300);
}

.ui-input:focus {
  border-color: var(--brand-700);
  box-shadow:
    inset 0 0 0 1px var(--brand-700),
    0 0 0 4px rgba(123, 30, 40, 0.12);
}

.ui-input.border-danger:focus {
  border-color: var(--danger);
  box-shadow:
    inset 0 0 0 1px var(--danger),
    0 0 0 4px rgba(179, 38, 30, 0.14);
}
</style>
