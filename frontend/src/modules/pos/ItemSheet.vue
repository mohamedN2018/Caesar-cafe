<script setup lang="ts">
/**
 * Size, extras, quantity — shown only when there is genuinely a choice.
 *
 * A sheet on every tap would cost two taps for the ninety percent of orders
 * that are "one of that", which at a queue is the difference between serving
 * and holding people up. So `PosBoardView` opens this only for products with
 * more than one live variant; everything else goes straight onto the bill.
 *
 * The running total at the bottom is computed here **for display only** — the
 * bill's figures always come back from the server's fold. Showing an estimate
 * before committing is worth it (a customer asks "how much?" before you ring
 * it), but it is deliberately never written anywhere.
 */
import { computed, ref } from 'vue'

import { type Product, usePosStore } from '@/stores/pos'

const props = defineProps<{ product: Product }>()
const emit = defineEmits<{ close: [] }>()

const pos = usePosStore()

const live = computed(() => props.product.variants.filter((v) => v.is_active))
const chosen = ref(live.value.find((v) => v.is_default)?.id ?? live.value[0]?.id ?? '')
const quantity = ref(1)
const picked = ref<string[]>([])
const note = ref('')

/** Every modifier the branch offers. Grouping is the server's; this flattens. */
const extras = computed(() => pos.modifierGroups.flatMap((g) => g.modifiers))

const estimate = computed(() => {
  const variant = live.value.find((v) => v.id === chosen.value)
  const base = Number(variant?.price ?? 0)
  const add = picked.value.reduce((sum, id) => {
    const modifier = extras.value.find((m) => m.id === id)
    return sum + Number(modifier?.price_delta ?? 0)
  }, 0)
  return ((base + add) * quantity.value).toFixed(2)
})

function toggle(id: string) {
  picked.value = picked.value.includes(id)
    ? picked.value.filter((m) => m !== id)
    : [...picked.value, id]
}

async function confirm() {
  if (!chosen.value) return
  await pos.addItem(chosen.value, quantity.value, picked.value, note.value.trim())
  emit('close')
}
</script>

<template>
  <div class="scrim" @click.self="emit('close')">
    <div class="sheet" role="dialog" aria-modal="true">
      <h2 class="title">{{ product.name_ar }}</h2>

      <section v-if="live.length > 1">
        <h3 class="label">الحجم</h3>
        <div class="chips">
          <button
            v-for="variant in live"
            :key="variant.id"
            type="button"
            class="chip"
            :class="{ 'is-on': chosen === variant.id }"
            @click="chosen = variant.id"
          >
            {{ variant.name_ar || 'عادي' }}
            <small class="tabular-nums">{{ variant.price }}</small>
          </button>
        </div>
      </section>

      <section v-if="extras.length">
        <h3 class="label">إضافات</h3>
        <div class="chips">
          <button
            v-for="modifier in extras"
            :key="modifier.id"
            type="button"
            class="chip"
            :class="{ 'is-on': picked.includes(modifier.id) }"
            @click="toggle(modifier.id)"
          >
            {{ modifier.name_ar }}
            <small v-if="Number(modifier.price_delta)" class="tabular-nums">
              +{{ modifier.price_delta }}
            </small>
          </button>
        </div>
      </section>

      <section>
        <h3 class="label">الكمية</h3>
        <div class="stepper">
          <button type="button" @click="quantity = Math.max(1, quantity - 1)">−</button>
          <span class="tabular-nums">{{ quantity }}</span>
          <button type="button" @click="quantity += 1">+</button>
        </div>
      </section>

      <section>
        <h3 class="label">ملاحظة للمطبخ</h3>
        <input v-model="note" type="text" class="note" placeholder="بدون سكر…" />
      </section>

      <footer class="foot">
        <button type="button" class="ghost" @click="emit('close')">إلغاء</button>
        <button type="button" class="go" :disabled="pos.busy || !chosen" @click="confirm">
          إضافة · <span class="tabular-nums">{{ estimate }}</span>
        </button>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  background: rgb(0 0 0 / 0.45);
  display: flex;
  align-items: flex-end;
  justify-content: center;
  z-index: 40;
}

.sheet {
  width: min(34rem, 100%);
  max-height: 88dvh;
  overflow-y: auto;
  background: var(--surface);
  border-radius: 1rem 1rem 0 0;
  padding: 1.1rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.title {
  font-size: 1.3rem;
  font-weight: 800;
  color: var(--ink);
}

.label {
  font-size: 0.85rem;
  font-weight: 700;
  color: var(--ink-muted);
  margin-bottom: 0.45rem;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.chip {
  display: flex;
  align-items: baseline;
  gap: 0.4rem;
  min-height: 3rem;
  padding: 0.6rem 1rem;
  border-radius: 0.7rem;
  border: 1px solid var(--border-strong);
  background: var(--surface);
  color: var(--ink);
  font-weight: 600;
}
.chip.is-on {
  background: var(--brand-700);
  border-color: var(--brand-700);
  color: var(--fg-on-brand);
}
.chip small {
  opacity: 0.75;
  font-size: 0.8rem;
}

.stepper {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}
.stepper button {
  width: 3.25rem;
  height: 3.25rem;
  border-radius: 0.7rem;
  background: var(--surface-sunken);
  font-size: 1.4rem;
  font-weight: 700;
  color: var(--ink);
}
.stepper span {
  min-width: 2.5rem;
  text-align: center;
  font-size: 1.3rem;
  font-weight: 800;
}

.note {
  width: 100%;
  padding: 0.75rem;
  border: 1px solid var(--border-strong);
  border-radius: 0.6rem;
  background: var(--surface);
  color: var(--ink);
}

.foot {
  display: flex;
  gap: 0.6rem;
  padding-top: 0.25rem;
}
.foot button {
  min-height: 3.4rem;
  border-radius: 0.7rem;
  font-size: 1rem;
  font-weight: 700;
}
.ghost {
  flex: 1 1 auto;
  background: var(--surface-sunken);
  color: var(--ink);
}
.go {
  flex: 2 1 auto;
  background: var(--brand-700);
  color: var(--fg-on-brand);
}
.go:disabled {
  opacity: 0.5;
}
</style>
