<script setup lang="ts">
/**
 * Taking the money.
 *
 * Three decisions here, each one a mistake somebody makes at a till:
 *
 *   * **The amount defaults to what is still owed, not to the grand total.**
 *     On a split payment those differ, and defaulting to the total is how a
 *     customer gets charged twice for the half their friend already paid.
 *   * **Change is shown large and computed from tendered.** The cashier reads
 *     it off the screen rather than doing it in their head over a queue.
 *   * **The sheet stays open until the balance is clear.** A split is the
 *     normal case, not an advanced one, so paying part of a bill returns you
 *     to this screen with the remainder already filled in.
 *
 * The quick-cash buttons round up to the notes people actually hand over. The
 * arithmetic is a display convenience only — the server recomputes the change
 * it records.
 */
import { computed, ref } from 'vue'

import { usePosStore } from '@/stores/pos'

const emit = defineEmits<{ close: [] }>()

const pos = usePosStore()

const due = computed(() => Number(pos.order?.balance_due ?? 0))
const method = ref(pos.methods.find((m) => m.counts_as_cash)?.id ?? pos.methods[0]?.id ?? '')
const amount = ref(due.value.toFixed(2))
const tendered = ref('')

const chosen = computed(() => pos.methods.find((m) => m.id === method.value))
const isCash = computed(() => chosen.value?.counts_as_cash ?? false)

const change = computed(() => {
  const given = Number(tendered.value)
  const paying = Number(amount.value)
  if (!Number.isFinite(given) || given <= paying) return 0
  return given - paying
})

/** The notes an Egyptian customer actually hands over. */
const quick = computed(() => {
  const target = Number(amount.value) || due.value
  const notes = [50, 100, 200, 500]
  const options = new Set<number>([Math.ceil(target)])
  for (const note of notes) {
    const up = Math.ceil(target / note) * note
    if (up >= target) options.add(up)
  }
  return [...options].sort((a, b) => a - b).slice(0, 5)
})

const valid = computed(() => {
  const value = Number(amount.value)
  return method.value !== '' && Number.isFinite(value) && value > 0
})

async function submit() {
  if (!valid.value) return

  const given = Number(tendered.value)
  await pos.pay(
    method.value,
    Number(amount.value),
    isCash.value && Number.isFinite(given) && given > 0 ? given : undefined,
  )

  if (pos.error) return

  if (pos.isSettled) {
    emit('close')
    return
  }
  // A split. Refill with what is left rather than closing — the second half is
  // the whole reason this screen is still open.
  amount.value = Number(pos.order?.balance_due ?? 0).toFixed(2)
  tendered.value = ''
}
</script>

<template>
  <div class="scrim" @click.self="emit('close')">
    <div class="sheet" role="dialog" aria-modal="true">
      <header class="head">
        <span>المطلوب</span>
        <strong class="tabular-nums">{{ due.toFixed(2) }}</strong>
      </header>

      <section>
        <h3 class="label">طريقة الدفع</h3>
        <div class="chips">
          <button
            v-for="option in pos.methods"
            :key="option.id"
            type="button"
            class="chip"
            :class="{ 'is-on': method === option.id }"
            @click="method = option.id"
          >
            {{ option.name_ar }}
          </button>
        </div>
      </section>

      <section>
        <h3 class="label">المبلغ</h3>
        <input v-model="amount" type="number" inputmode="decimal" step="0.01" class="figure" />
      </section>

      <section v-if="isCash">
        <h3 class="label">المدفوع</h3>
        <input
          v-model="tendered"
          type="number"
          inputmode="decimal"
          step="0.01"
          class="figure"
          placeholder="اتركه فارغاً لو بالظبط"
        />
        <div class="chips mt">
          <button
            v-for="note in quick"
            :key="note"
            type="button"
            class="chip"
            @click="tendered = note.toFixed(2)"
          >
            {{ note }}
          </button>
        </div>

        <p v-if="change > 0" class="change">
          الباقي <strong class="tabular-nums">{{ change.toFixed(2) }}</strong>
        </p>
      </section>

      <footer class="foot">
        <button type="button" class="ghost" @click="emit('close')">إلغاء</button>
        <button type="button" class="go" :disabled="!valid || pos.busy" @click="submit">
          تحصيل
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
  width: min(30rem, 100%);
  max-height: 92dvh;
  overflow-y: auto;
  background: var(--surface);
  border-radius: 1rem 1rem 0 0;
  padding: 1.1rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  font-size: 1rem;
  color: var(--ink-muted);
}
.head strong {
  font-size: 1.9rem;
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
.chips.mt {
  margin-top: 0.55rem;
}

.chip {
  min-height: 3rem;
  padding: 0.6rem 1.1rem;
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

.figure {
  width: 100%;
  padding: 0.85rem 1rem;
  font-size: 1.5rem;
  font-weight: 700;
  text-align: center;
  border: 1px solid var(--border-strong);
  border-radius: 0.7rem;
  background: var(--surface);
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}

.change {
  margin-top: 0.75rem;
  padding: 0.75rem;
  border-radius: 0.7rem;
  background: var(--success-bg);
  color: var(--success);
  text-align: center;
  font-size: 1rem;
}
.change strong {
  font-size: 1.7rem;
  font-weight: 800;
  margin-inline-start: 0.4rem;
}

.foot {
  display: flex;
  gap: 0.6rem;
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
