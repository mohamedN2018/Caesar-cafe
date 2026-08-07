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
    <span v-if="label" class="mb-1.5 block text-sm font-medium text-slate-700">
      {{ label }}
      <span v-if="required" class="text-red-600" aria-hidden="true">*</span>
    </span>
    <input
      v-model="model"
      :type="type ?? 'text'"
      :placeholder="placeholder"
      :required="required"
      :disabled="disabled"
      :dir="ltr ? 'ltr' : undefined"
      class="w-full rounded-lg border bg-white px-3.5 py-2.5 text-[15px] min-h-[44px]
             transition placeholder:text-slate-400 disabled:bg-slate-50
             focus:outline-none focus:ring-2 focus:ring-brand-700/30"
      :class="error ? 'border-red-400 focus:border-red-500' : 'border-slate-300 focus:border-brand-700'"
    />
    <p v-if="error" class="mt-1.5 text-sm text-red-600">{{ error }}</p>
    <p v-else-if="hint" class="mt-1.5 text-sm text-slate-500">{{ hint }}</p>
  </label>
</template>
