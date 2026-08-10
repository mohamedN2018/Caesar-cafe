<script setup lang="ts">
/**
 * The drawer, from the till.
 *
 * This was the gap that mattered most: a cashier could sell but not open or
 * close their own shift, so starting a day meant walking to the admin app —
 * which is exactly the screen the whole PIN design exists to keep them out of.
 *
 * Three things happen here and they are deliberately not equal in weight:
 *
 *   * **Opening** is one number and a button, because it happens at the start
 *     of a rush.
 *   * **A movement** — money out for a supplier, change brought in — is a small
 *     form that only exists while a drawer is open. Every pound that leaves
 *     untracked shows up as a shortage against the person counting.
 *   * **Closing** is the careful one, and the only place with a confirmation.
 *
 * **The count is blind.** The expected figure is withheld while the cashier
 * types theirs (`shifts.blind_close`, honoured by the server — a manager with
 * `reports.financial` does see it). A cashier who can read the target is not
 * counting a drawer, they are reproducing a number, and the variance stops
 * meaning anything. This screen never asks for the expected figure before the
 * close, so it cannot leak it even by accident.
 */
import { computed, onMounted, ref } from 'vue'

import { ApiError, api } from '@/api/client'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiIcon from '@/components/ui/UiIcon.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import { dateTime, money } from '@/lib/format'
import { useAuthStore } from '@/stores/auth'
import { usePosStore } from '@/stores/pos'

interface Report {
  opening_cash: string
  cash_sales: string
  non_cash_sales: string
  cash_in: string
  cash_out: string
  /** `null` under a blind close — the whole point. */
  expected_cash: string | null
  order_count: number
}

interface Closed {
  counted_cash: string
  expected_cash: string
  variance: string
}

const MOVEMENTS = [
  { value: 'IN', label: 'دخول نقدية' },
  { value: 'OUT', label: 'خروج نقدية' },
  { value: 'EXPENSE', label: 'مصروف' },
  { value: 'DROP', label: 'توريد للخزنة' },
] as const

const pos = usePosStore()
const auth = useAuthStore()

const loading = ref(true)
const busy = ref('')
const error = ref('')
const report = ref<Report | null>(null)
const closed = ref<Closed | null>(null)

const openingCash = ref('')
const counted = ref('')
const closingReason = ref('')
const movement = ref({ movement_type: 'EXPENSE', amount: '', reason: '' })

const shift = computed(() => pos.shift)
const mayMove = computed(() => auth.can('shifts.cash_movement'))
const mayClose = computed(() => auth.can('shifts.close'))
const mayOpen = computed(() => auth.can('shifts.open'))

function fail(e: unknown, fallback: string) {
  error.value = e instanceof ApiError ? e.message : fallback
}

async function loadReport() {
  if (!shift.value) {
    report.value = null
    return
  }
  report.value = await api.optional<Report>(`/shifts/${shift.value.id}/x-report/`)
}

async function refresh() {
  loading.value = true
  await pos.loadShift()
  await loadReport()
  loading.value = false
}

async function openShift() {
  busy.value = 'open'
  error.value = ''
  try {
    await api.post('/shifts/open/', { opening_cash: Number(openingCash.value || 0).toFixed(2) })
    openingCash.value = ''
    await refresh()
  } catch (e) {
    fail(e, 'تعذّر فتح الوردية.')
  } finally {
    busy.value = ''
  }
}

async function addMovement() {
  const amount = Number(movement.value.amount)
  if (!shift.value || !Number.isFinite(amount) || amount <= 0) return
  if (!movement.value.reason.trim()) return

  busy.value = 'movement'
  error.value = ''
  try {
    await api.post(`/shifts/${shift.value.id}/cash-movements/`, {
      movement_type: movement.value.movement_type,
      amount: amount.toFixed(2),
      reason: movement.value.reason.trim(),
    })
    movement.value = { movement_type: 'EXPENSE', amount: '', reason: '' }
    await loadReport()
  } catch (e) {
    fail(e, 'تعذّر تسجيل الحركة.')
  } finally {
    busy.value = ''
  }
}

async function closeShift() {
  const value = Number(counted.value)
  if (!shift.value || !Number.isFinite(value) || value < 0) return
  if (!window.confirm('إغلاق الوردية بالمبلغ المعدود؟ لا يمكن التراجع.')) return

  busy.value = 'close'
  error.value = ''
  try {
    closed.value = await api.post<Closed>(`/shifts/${shift.value.id}/close/`, {
      counted_cash: value.toFixed(2),
      reason: closingReason.value.trim(),
    })
    counted.value = ''
    closingReason.value = ''
    await refresh()
  } catch (e) {
    fail(e, 'تعذّر إغلاق الوردية.')
  } finally {
    busy.value = ''
  }
}

onMounted(refresh)
</script>

<template>
  <div class="page">
    <UiSkeleton v-if="loading" :rows="5" />

    <template v-else>
      <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>

      <!--
        The variance, once, right after closing. Shown here rather than as a
        toast because it is the number the cashier is accountable for and the
        one they may need to explain — it should not vanish on a timer.
      -->
      <section v-if="closed" class="closed">
        <p class="closed-title">تم إغلاق الوردية</p>
        <dl class="closed-figures">
          <div><dt>المعدود</dt><dd class="tabular-nums">{{ money(closed.counted_cash) }}</dd></div>
          <div><dt>المتوقع</dt><dd class="tabular-nums">{{ money(closed.expected_cash) }}</dd></div>
          <div :class="Number(closed.variance) === 0 ? 'is-level' : 'is-off'">
            <dt>الفرق</dt>
            <dd class="tabular-nums">{{ money(closed.variance) }}</dd>
          </div>
        </dl>
      </section>

      <!-- No open drawer. -->
      <section v-if="!shift" class="card">
        <div class="head">
          <UiIcon name="cash" size="1.2rem" />
          <h2>افتح وردية</h2>
        </div>
        <p class="lead">
          البيع بدون وردية بينتج مبيعات مالهاش درج تتقارن بيه — والكاشير بيكتشف ده وقت التقفيل،
          وهي اللحظة الوحيدة اللي مش ممكن يتصلح فيها.
        </p>

        <template v-if="mayOpen">
          <label class="field">
            <span>العهدة — الفلوس اللي في الدرج فعلاً</span>
            <input
              v-model="openingCash"
              type="number"
              inputmode="decimal"
              step="0.01"
              class="figure"
              placeholder="0.00"
            />
          </label>
          <p class="warn">
            اكتب الرقم الحقيقي مش المفروض. التقفيل بيقارن بيه، ولو بدأت برقم غلط الفرق هيظهر
            على اسمك.
          </p>
          <button type="button" class="go" :disabled="busy === 'open'" @click="openShift">
            افتح الوردية
          </button>
        </template>
        <p v-else class="lead">لا تملك صلاحية فتح وردية — اطلب من المدير.</p>
      </section>

      <!-- An open drawer. -->
      <template v-else>
        <section class="card">
          <div class="head">
            <UiIcon name="cash" size="1.2rem" />
            <h2>الوردية الحالية</h2>
          </div>
          <p class="lead">
            {{ shift.opened_by_name }} · فُتحت {{ dateTime(shift.opened_at) }}
          </p>

          <dl v-if="report" class="figures">
            <div><dt>العهدة</dt><dd class="tabular-nums">{{ money(report.opening_cash) }}</dd></div>
            <div><dt>مبيعات نقدية</dt><dd class="tabular-nums">{{ money(report.cash_sales) }}</dd></div>
            <div><dt>مبيعات غير نقدية</dt><dd class="tabular-nums">{{ money(report.non_cash_sales) }}</dd></div>
            <div><dt>داخل</dt><dd class="tabular-nums">{{ money(report.cash_in) }}</dd></div>
            <div><dt>خارج</dt><dd class="tabular-nums">{{ money(report.cash_out) }}</dd></div>
            <div><dt>عدد الطلبات</dt><dd class="tabular-nums">{{ report.order_count }}</dd></div>
            <!--
              Present only when the server chose to send it. Under a blind close
              it is null, and this row simply is not there — a cashier who can
              read the target is reproducing a number, not counting a drawer.
            -->
            <div v-if="report.expected_cash !== null" class="is-expected">
              <dt>المتوقع في الدرج</dt>
              <dd class="tabular-nums">{{ money(report.expected_cash) }}</dd>
            </div>
          </dl>
        </section>

        <section v-if="mayMove" class="card">
          <div class="head">
            <UiIcon name="history" size="1.2rem" />
            <h2>حركة نقدية</h2>
          </div>
          <p class="lead">
            فلوس داخلة أو خارجة مش بيع. كل جنيه يخرج بدون تسجيل بيظهر عجز على اسمك.
          </p>

          <div class="kinds">
            <button
              v-for="kind in MOVEMENTS"
              :key="kind.value"
              type="button"
              class="kind"
              :class="{ 'is-on': movement.movement_type === kind.value }"
              @click="movement.movement_type = kind.value"
            >
              {{ kind.label }}
            </button>
          </div>

          <label class="field">
            <span>المبلغ</span>
            <input
              v-model="movement.amount"
              type="number"
              inputmode="decimal"
              step="0.01"
              class="figure"
            />
          </label>
          <label class="field">
            <span>السبب — مطلوب</span>
            <input v-model="movement.reason" type="text" class="text" placeholder="شراء مياه…" />
          </label>
          <button
            type="button"
            class="secondary"
            :disabled="busy === 'movement' || !movement.amount || !movement.reason.trim()"
            @click="addMovement"
          >
            تسجيل الحركة
          </button>
        </section>

        <section v-if="mayClose" class="card">
          <div class="head">
            <UiIcon name="shield" size="1.2rem" />
            <h2>إغلاق الوردية</h2>
          </div>
          <p class="lead">عُدّ الدرج فعلاً واكتب الرقم. النظام بيقارن ويطلع الفرق.</p>

          <label class="field">
            <span>المبلغ المعدود</span>
            <input
              v-model="counted"
              type="number"
              inputmode="decimal"
              step="0.01"
              class="figure"
              placeholder="0.00"
            />
          </label>
          <label class="field">
            <span>ملاحظة (لو فيه فرق)</span>
            <input
              v-model="closingReason"
              type="text"
              class="text"
              placeholder="سبب الفرق إن وُجد…"
            />
          </label>
          <p class="warn">
            الفرق اللي معاه سبب مكتوب بيتقفل من نفسه؛ واللي من غير سبب بيفضل سؤال عليك بكرة.
          </p>
          <button type="button" class="danger" :disabled="busy === 'close' || !counted" @click="closeShift">
            إغلاق الوردية
          </button>
        </section>
      </template>
    </template>
  </div>
</template>

<style scoped>
.page {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
  width: min(34rem, 100%);
  margin-inline: auto;
}

.card {
  padding: 1.1rem;
  border-radius: 0.9rem;
  background: var(--surface);
  border: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 0.7rem;
}

.head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  color: var(--ink);
}
.head h2 {
  font-size: 1.05rem;
  font-weight: 700;
}

.lead {
  font-size: 0.85rem;
  color: var(--ink-muted);
  line-height: 1.6;
}

.warn {
  font-size: 0.78rem;
  color: var(--warning);
  line-height: 1.6;
}

.figures,
.closed-figures {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(8rem, 1fr));
  gap: 0.5rem;
}
.figures > div,
.closed-figures > div {
  padding: 0.6rem 0.75rem;
  border-radius: 0.6rem;
  background: var(--surface-muted);
}
.figures dt,
.closed-figures dt {
  font-size: 0.72rem;
  color: var(--ink-muted);
}
.figures dd,
.closed-figures dd {
  margin-top: 0.15rem;
  font-size: 1.05rem;
  font-weight: 650;
  color: var(--ink);
}
.figures .is-expected {
  background: var(--info-bg);
}

.closed {
  padding: 1.1rem;
  border-radius: 0.9rem;
  background: var(--success-bg);
  border: 1px solid var(--success);
}
.closed-title {
  margin-bottom: 0.6rem;
  font-weight: 700;
  color: var(--success);
}
.closed-figures .is-level dd {
  color: var(--success);
}
.closed-figures .is-off dd {
  color: var(--danger);
}

.field {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.field span {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--ink-muted);
}

.figure,
.text {
  width: 100%;
  padding: 0.8rem 1rem;
  border: 1px solid var(--border-strong);
  border-radius: 0.7rem;
  background: var(--surface);
  color: var(--ink);
}
.figure {
  font-size: 1.35rem;
  font-weight: 700;
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.kinds {
  display: flex;
  flex-wrap: wrap;
  gap: 0.45rem;
}
.kind {
  min-height: 2.75rem;
  padding: 0.5rem 0.95rem;
  border-radius: 0.65rem;
  border: 1px solid var(--border-strong);
  background: var(--surface);
  color: var(--ink);
  font-size: 0.88rem;
  font-weight: 600;
}
.kind.is-on {
  background: var(--brand-700);
  border-color: var(--brand-700);
  color: var(--fg-on-brand);
}

.go,
.secondary,
.danger {
  min-height: 3.2rem;
  border-radius: 0.7rem;
  font-size: 1rem;
  font-weight: 700;
}
.go {
  background: var(--brand-700);
  color: var(--fg-on-brand);
}
.secondary {
  background: var(--surface-sunken);
  color: var(--ink);
}
.danger {
  background: var(--danger);
  color: #fff;
}
.go:disabled,
.secondary:disabled,
.danger:disabled {
  opacity: 0.45;
}
</style>
