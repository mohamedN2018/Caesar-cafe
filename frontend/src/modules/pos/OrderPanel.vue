<script setup lang="ts">
/**
 * The bill.
 *
 * Two things it never does, both learned from the Desktop panel:
 *
 *   * **It never hides a voided line.** The line stays, struck through, so the
 *     cashier can see what was taken off and so can the customer looking at the
 *     screen. A line that vanishes is an argument at the counter.
 *   * **It never computes a total.** Every figure here came from the server's
 *     fold. A panel that added its own subtotal would be the one place the
 *     screen and the receipt are free to disagree.
 *
 * The manual price and the discount are separate buttons on purpose. A discount
 * is a percentage off a known price and reports as one; an override is a
 * different price entirely — the damaged cake, the staff meal. Merging them
 * makes the discount rate meaningless, and the discount rate is what an owner
 * watches for loss.
 */
import { computed, ref } from 'vue'

import UiIcon from '@/components/ui/UiIcon.vue'
import { usePosStore } from '@/stores/pos'
import { useAuthStore } from '@/stores/auth'

const emit = defineEmits<{ pay: [] }>()

const pos = usePosStore()
const auth = useAuthStore()

const editing = ref<string | null>(null)

const totals = computed(() => pos.order)
const canFire = computed(() => pos.unfired.length > 0)
const mayOverride = computed(() => auth.can('orders.change_price'))
const mayDiscount = computed(() => auth.can('orders.discount'))

/**
 * Hidden, not disabled, and not merely permission-checked.
 *
 * `payments.take` is the permission, but the methods list can also be empty
 * because nobody has configured a tender yet — and a pay button that opens a
 * sheet with no buttons in it is the same dead end by a different route. Both
 * cases mean the same thing to the cashier: settling is not available here.
 */
const mayPay = computed(() => auth.can('payments.take') && pos.methods.length > 0)

function trim(quantity: string): string {
  return String(Number(quantity))
}

async function step(lineId: string, current: string, delta: number) {
  const next = Number(current) + delta
  // Stepping to zero is a void, not a quantity — a zero-quantity line on a bill
  // is a line the customer is not being charged for and cannot see why.
  if (next <= 0) {
    await remove(lineId)
    return
  }
  await pos.setQuantity(lineId, next)
}

async function remove(lineId: string) {
  const item = pos.activeItems.find((i) => i.line_id === lineId)
  let reason = ''

  // Before the kitchen has seen it, removing is routine. After, it is a real
  // loss — the food was started — so it needs a reason on the record.
  if (item?.fired_at) {
    const given = window.prompt('الصنف أُرسل للمطبخ — سبب الإلغاء:')
    if (!given?.trim()) return
    reason = given.trim()
  }
  await pos.voidItem(lineId, reason)
  editing.value = null
}

async function note(lineId: string, current: string) {
  const given = window.prompt('ملاحظة للمطبخ:', current)
  if (given === null) return
  await pos.setNote(lineId, given.trim())
}

async function overridePrice(lineId: string) {
  const item = pos.activeItems.find((i) => i.line_id === lineId)
  if (!item) return

  if (item.price_override !== null) {
    if (window.confirm(`السعر اليدوي ${item.price_override}. ترجعه لسعر المنيو؟`)) {
      await pos.overridePrice(lineId, null, '')
      return
    }
  }

  const raw = window.prompt(
    `سعر المنيو ${item.unit_price_snapshot} — السعر الجديد:`,
    item.price_override ?? item.unit_price_snapshot,
  )
  if (raw === null) return

  const price = Number(raw)
  if (!Number.isFinite(price) || price < 0) return

  // Required, not optional. An override with no reason is the one an auditor
  // cannot tell apart from theft, and asking now costs a sentence.
  const reason = window.prompt('السبب (مطلوب):')
  if (!reason?.trim()) return

  await pos.overridePrice(lineId, price, reason.trim())
}

async function discount() {
  const raw = window.prompt('نسبة الخصم %:', '0')
  if (raw === null) return
  const percent = Number(raw)
  if (!Number.isFinite(percent) || percent < 0 || percent > 100) return

  const reason = window.prompt('سبب الخصم:') ?? ''
  await pos.applyDiscount(percent, null, reason.trim())
}
</script>

<template>
  <div class="panel">
    <header class="panel-head">
      <span class="number">{{ pos.order?.local_number || 'طلب جديد' }}</span>
      <span v-if="pos.order" class="kind">{{ pos.order.order_type === 'DINE_IN' ? 'صالة' : pos.order.order_type === 'DELIVERY' ? 'توصيل' : 'تيك أواي' }}</span>
    </header>

    <div class="lines">
      <!--
        The idle state is the first thing anybody sees on this screen, including
        a customer standing at the counter. "اختر صنفاً للبدء" as bare grey text
        read as something broken; a framed glyph and a line of guidance reads as
        a till waiting.
      -->
      <div v-if="!pos.order?.items.length" class="idle">
        <span class="idle-mark"><UiIcon name="receipt" size="1.4rem" /></span>
        <p class="idle-title">لا توجد أصناف بعد</p>
        <p class="idle-hint">اختر صنفاً من المنيو ليبدأ الطلب</p>
      </div>

      <div
        v-for="item in pos.order?.items ?? []"
        :key="item.line_id"
        class="line"
        :class="{ 'is-void': item.status === 'VOIDED', 'is-open': editing === item.line_id }"
        @click="editing = editing === item.line_id ? null : item.line_id"
      >
        <div class="line-main">
          <span class="qty tabular-nums">{{ trim(item.quantity) }}×</span>
          <span class="name">
            {{ item.name_snapshot }}
            <small v-if="item.modifiers.length" class="mods">
              {{ item.modifiers.map((m) => m.name_snapshot).join('، ') }}
            </small>
            <small v-if="item.note" class="mods">
              <UiIcon name="note" size="0.85rem" /> {{ item.note }}
            </small>
            <small v-if="item.price_override !== null" class="manual">
              سعر يدوي{{ item.price_override_reason ? ` — ${item.price_override_reason}` : '' }}
            </small>
            <small v-if="item.void_reason" class="voided">{{ item.void_reason }}</small>
          </span>
          <span class="total tabular-nums">
            {{ item.status === 'VOIDED' ? '—' : item.line_total }}
          </span>
        </div>

        <div v-if="editing === item.line_id && item.status === 'ACTIVE'" class="line-actions" @click.stop>
          <button type="button" @click="step(item.line_id, item.quantity, -1)">−</button>
          <button type="button" @click="step(item.line_id, item.quantity, 1)">+</button>
          <button type="button" @click="note(item.line_id, item.note)">ملاحظة</button>
          <button v-if="mayOverride" type="button" @click="overridePrice(item.line_id)">سعر</button>
          <button type="button" class="danger" @click="remove(item.line_id)">حذف</button>
        </div>
      </div>
    </div>

    <footer v-if="totals" class="totals">
      <div class="row"><span>المجموع</span><span class="tabular-nums">{{ totals.subtotal }}</span></div>
      <!-- Zero rows are omitted: a receipt that lists "الخصم 0.00" on every sale
           trains the eye to skip the block where a real one would appear. -->
      <div v-if="Number(totals.discount_total)" class="row">
        <span>الخصم</span><span class="tabular-nums">−{{ totals.discount_total }}</span>
      </div>
      <div v-if="Number(totals.service_total)" class="row">
        <span>الخدمة</span><span class="tabular-nums">{{ totals.service_total }}</span>
      </div>
      <div v-if="Number(totals.tax_total)" class="row">
        <span>ضريبة القيمة المضافة</span><span class="tabular-nums">{{ totals.tax_total }}</span>
      </div>
      <div class="row grand">
        <span>الإجمالي</span><span class="tabular-nums">{{ totals.grand_total }}</span>
      </div>
      <div v-if="Number(totals.paid_total)" class="row due">
        <span>المتبقي</span><span class="tabular-nums">{{ totals.balance_due }}</span>
      </div>
    </footer>

    <div class="actions">
      <!--
        `v-if` on the permission, `:disabled` on the state. The distinction is
        the product's rule: a control you may never use is not drawn at all,
        because a greyed button you can never ungrey is a promise the app has no
        intention of keeping. A control you may use but not YET — nothing on the
        bill, nothing new to fire — is drawn and disabled, because that one
        turns on by itself in a moment.
      -->
      <button
        v-if="mayDiscount"
        type="button"
        class="secondary"
        :disabled="!pos.hasItems || pos.busy"
        @click="discount"
      >
        خصم
      </button>
      <button type="button" class="secondary" :disabled="!canFire || pos.busy" @click="pos.fire()">
        للمطبخ
      </button>
      <button
        v-if="mayPay"
        type="button"
        class="pay"
        :disabled="!pos.hasItems || pos.isSettled || pos.busy"
        @click="emit('pay')"
      >
        دفع
      </button>
    </div>
  </div>
</template>

<style scoped>
.panel {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
}

.panel-head {
  flex: 0 0 auto;
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: 0.9rem 1rem 0.6rem;
  border-bottom: 1px solid var(--border);
}
.number {
  font-size: 1.2rem;
  font-weight: 800;
  color: var(--ink);
}
.kind {
  font-size: 0.85rem;
  color: var(--ink-muted);
}

.lines {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}

.idle {
  padding: 3rem 1rem;
  text-align: center;
}
.idle-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 3rem;
  height: 3rem;
  margin-bottom: 0.75rem;
  border-radius: 999px;
  background: var(--surface-sunken);
  color: var(--ink-faint);
}
.idle-title {
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--ink-muted);
}
.idle-hint {
  margin-top: 0.25rem;
  font-size: 0.82rem;
  color: var(--ink-faint);
}

.line {
  padding: 0.6rem 1rem;
  border-bottom: 1px solid var(--surface-sunken);
  cursor: pointer;
}
.line.is-open {
  background: var(--surface-muted);
}
.line.is-void {
  opacity: 0.5;
}
.line.is-void .name {
  text-decoration: line-through;
}

.line-main {
  display: flex;
  align-items: flex-start;
  gap: 0.6rem;
}
.qty {
  font-weight: 700;
  color: var(--brand-700);
  min-width: 2rem;
}
.name {
  flex: 1 1 auto;
  min-width: 0;
  font-size: 0.98rem;
  color: var(--ink);
}
.mods,
.manual,
.voided {
  display: block;
  font-size: 0.75rem;
  line-height: 1.4;
}
.mods {
  color: var(--ink-muted);
}
.manual {
  color: var(--warning);
}
.voided {
  color: var(--danger);
}
.total {
  font-weight: 600;
  color: var(--ink);
}

.line-actions {
  display: flex;
  gap: 0.35rem;
  margin-top: 0.55rem;
}
.line-actions button {
  flex: 1 1 auto;
  padding: 0.55rem 0.3rem;
  border-radius: 0.5rem;
  background: var(--surface-sunken);
  color: var(--ink);
  font-size: 0.85rem;
  font-weight: 600;
}
.line-actions button.danger {
  background: var(--danger-bg);
  color: var(--danger);
}

.totals {
  flex: 0 0 auto;
  padding: 0.7rem 1rem;
  border-top: 1px solid var(--border);
  background: var(--surface-muted);
}
.row {
  display: flex;
  justify-content: space-between;
  font-size: 0.9rem;
  color: var(--ink-muted);
  padding: 0.15rem 0;
}
.row.grand {
  font-size: 1.25rem;
  font-weight: 800;
  color: var(--ink);
  padding-top: 0.45rem;
  margin-top: 0.35rem;
  border-top: 1px solid var(--border);
}
.row.due {
  color: var(--warning);
  font-weight: 700;
}

.actions {
  flex: 0 0 auto;
  display: flex;
  gap: 0.5rem;
  padding: 0.75rem;
  border-top: 1px solid var(--border);
}
.actions button {
  flex: 1 1 auto;
  min-height: 3.25rem;
  border-radius: 0.7rem;
  font-size: 1rem;
  font-weight: 700;
}
.actions .secondary {
  background: var(--surface-sunken);
  color: var(--ink);
}
.actions .pay {
  flex: 1.4 1 auto;
  background: var(--brand-700);
  color: var(--fg-on-brand);
}
.actions button:disabled {
  opacity: 0.45;
}
</style>
