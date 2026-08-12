<script setup lang="ts">
/**
 * A card: the unit this whole admin is built out of.
 *
 * Three deliberate choices about depth.
 *
 * **The shadow is warm, not black.** It comes from `--shadow-*`, tinted with the
 * ink colour. Pure black over a cream surface greys it — the card stops looking
 * like paper on a warm desk and starts looking like paper behind glass.
 *
 * **`raised` is opt-in, and most cards should not use it.** If everything is
 * lifted, nothing is: elevation is a way of saying "this one first", and a page
 * of twelve equally-floating panels has thrown that away. The default is a
 * hairline and the faintest contact shadow, which is enough to separate a card
 * from the page without competing with the content on it.
 *
 * **`interactive` is only for a card that is actually a link or a button.** It
 * lifts on hover, and a lift that leads nowhere is a promise the interface does
 * not keep — the cursor says "press me" and nothing happens.
 */
withDefaults(
  defineProps<{
    title?: string
    subtitle?: string
    /** Lift it off the page. For the one card that leads. */
    raised?: boolean
    /** Hover feedback. Only when the whole card is clickable. */
    interactive?: boolean
    /** Remove the body padding — for a card whose content is a full-bleed table. */
    flush?: boolean
  }>(),
  // `title` and `subtitle` are genuinely absent on most cards, and `undefined`
  // is what "absent" means — but it has to be stated, or `require-default-prop`
  // warns and two warnings is how a lint script becomes one people ignore.
  { title: undefined, subtitle: undefined, raised: false, interactive: false, flush: false },
)
</script>

<template>
  <section
    class="ui-card rounded-xl border border-line bg-surface"
    :class="[raised ? 'shadow-md' : 'shadow-sm', interactive && 'ui-card--interactive']"
  >
    <header
      v-if="title || $slots.actions"
      class="flex items-center justify-between gap-4 border-b border-line px-5 py-4"
    >
      <div class="min-w-0">
        <h2 v-if="title" class="truncate text-base font-semibold text-ink">{{ title }}</h2>
        <p v-if="subtitle" class="mt-0.5 text-sm text-ink-muted">{{ subtitle }}</p>
      </div>
      <div v-if="$slots.actions" class="flex flex-none items-center gap-2">
        <slot name="actions" />
      </div>
    </header>
    <div :class="flush ? '' : 'p-5'">
      <slot />
    </div>
  </section>
</template>

<style scoped>
.ui-card {
  /* Transition the shadow and the position, never `all`. `all` animates colour
     and layout too, so a card whose contents change mid-transition crossfades
     its own text, which reads as a rendering fault rather than as motion. */
  transition:
    box-shadow var(--duration-base) var(--ease-out),
    transform var(--duration-base) var(--ease-out),
    border-color var(--duration-base) var(--ease-out);
}

.ui-card--interactive {
  cursor: pointer;
}

.ui-card--interactive:hover {
  /* One pixel. Enough to register as a response, small enough that a grid of
     them does not jump around under a moving cursor. */
  transform: translateY(-1px);
  box-shadow: var(--shadow-lg);
  border-color: var(--border-strong);
}

.ui-card--interactive:active {
  transform: translateY(0);
  box-shadow: var(--shadow-sm);
}
</style>
