<script setup lang="ts">
/**
 * Minimum height is 44px everywhere. The Web Admin is used on a tablet in the
 * back office as often as on a desktop, and a 32px button is a miss on glass.
 */
withDefaults(
  defineProps<{
    variant?: 'primary' | 'secondary' | 'danger' | 'ghost'
    size?: 'sm' | 'md' | 'lg'
    loading?: boolean
    disabled?: boolean
    type?: 'button' | 'submit'
    block?: boolean
  }>(),
  { variant: 'primary', size: 'md', type: 'button' },
)

const variants = {
  primary: 'bg-brand-700 text-white hover:bg-brand-800 active:bg-brand-900',
  secondary: 'bg-surface-sunken text-ink hover:bg-surface-sunken border border-line-strong',
  danger: 'bg-danger text-white hover:bg-danger',
  ghost: 'bg-transparent text-ink hover:bg-surface-sunken',
}

const sizes = {
  sm: 'text-sm px-3 min-h-[36px]',
  md: 'text-[15px] px-4 min-h-[44px]',
  lg: 'text-base px-6 min-h-[52px]',
}
</script>

<template>
  <button
    :type="type"
    :disabled="disabled || loading"
    class="inline-flex items-center justify-center gap-2 rounded-lg font-semibold transition
           disabled:cursor-not-allowed disabled:opacity-50
           focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2
           focus-visible:outline-brand-700"
    :class="[variants[variant], sizes[size], block && 'w-full']"
  >
    <svg
      v-if="loading"
      class="h-4 w-4 animate-spin"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
      <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
    </svg>
    <slot />
  </button>
</template>
