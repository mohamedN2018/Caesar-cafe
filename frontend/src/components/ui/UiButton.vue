<script setup lang="ts">
/**
 * Minimum height is 44px everywhere. The Web Admin is used on a tablet in the
 * back office as often as on a desktop, and a 32px button is a miss on glass.
 *
 * The primary button is a gradient with a brand-tinted shadow under it. That is
 * not decoration: on a page of cream cards there is exactly one thing the user
 * came to do, and a flat burgundy rectangle competes with every other burgundy
 * rectangle on the screen for that job. The depth is what makes it read as the
 * action rather than as a coloured label.
 *
 * It presses. `translateY(1px)` with the shadow collapsing is the cheapest
 * possible confirmation that a tap landed, and on a touch screen there is no
 * hover state to have confirmed it beforehand — the alternative is a cashier
 * tapping twice.
 */
withDefaults(
  defineProps<{
    variant?: 'primary' | 'secondary' | 'danger' | 'ghost' | 'gold'
    size?: 'sm' | 'md' | 'lg'
    loading?: boolean
    disabled?: boolean
    type?: 'button' | 'submit'
    block?: boolean
  }>(),
  { variant: 'primary', size: 'md', type: 'button' },
)

const variants = {
  primary: 'btn-primary text-white',
  secondary: 'bg-surface text-ink border border-line-strong hover:bg-surface-muted shadow-xs',
  danger: 'btn-danger text-white',
  ghost: 'bg-transparent text-ink hover:bg-surface-sunken',
  // For the one confirming action on a brand-coloured surface, where burgundy
  // on burgundy would disappear.
  gold: 'btn-gold text-ink',
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
    class="ui-btn inline-flex items-center justify-center gap-2 rounded-lg font-semibold
           disabled:cursor-not-allowed disabled:opacity-50 disabled:shadow-none
           focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2
           focus-visible:outline-focus"
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

<style scoped>
.ui-btn {
  transition:
    background-color var(--duration-fast) var(--ease-out),
    box-shadow var(--duration-fast) var(--ease-out),
    transform var(--duration-fast) var(--ease-out),
    filter var(--duration-fast) var(--ease-out);
}

/* The press. Applies to every variant, including ghost — a button that does not
   acknowledge a tap is a button somebody taps again. */
.ui-btn:active:not(:disabled) {
  transform: translateY(1px);
}

.btn-primary {
  background-image: var(--brand-gradient);
  box-shadow: var(--shadow-brand);
}
.btn-primary:hover:not(:disabled) {
  /* Brightening the gradient rather than swapping to a flat hover colour, so
     the surface keeps its shape while it lights up. */
  filter: brightness(1.12);
}
.btn-primary:active:not(:disabled) {
  box-shadow: var(--shadow-xs);
  filter: brightness(0.96);
}

.btn-danger {
  background-color: var(--danger);
  box-shadow: var(--shadow-sm);
}
.btn-danger:hover:not(:disabled) {
  filter: brightness(1.08);
}

.btn-gold {
  background-image: var(--gold-gradient);
  box-shadow: var(--shadow-sm);
}
.btn-gold:hover:not(:disabled) {
  filter: brightness(1.06);
}
</style>
