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
      class="w-full rounded-lg border-line bg-surface px-3.5 py-2.5 text-[15px] min-h-[44px]
             transition placeholder:text-ink-faint disabled:bg-surface-muted
             focus:outline-none focus:ring-2 focus:ring-brand-700/30"
      :class="error ? 'border-danger focus:border-danger' : 'border-line-strong focus:border-brand-700'"
    />
    <p v-if="error" class="mt-1.5 text-sm text-danger">{{ error }}</p>
    <p v-else-if="hint" class="mt-1.5 text-sm text-ink-muted">{{ hint }}</p>
  </label>
</template>
