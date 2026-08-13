<script setup lang="ts">
/**
 * The cashier's own order list, on the till.
 *
 * **Today by default, and that is the whole design.** A cashier looks at this to
 * answer one of three questions — which tables are still open, did that bill get
 * paid, and what did I ring for the man who came back. All three are about today.
 * A list that opened on "everything ever" would put a fortnight of history in
 * front of somebody who needed the last twenty minutes, and the thing they wanted
 * would be off the bottom of the screen.
 *
 * Other days are a filter, not the default: they exist because "the receipt from
 * yesterday" is a real question, and answering it should not require a manager.
 *
 * **Deliberately less than the admin's order list.** No cost, no margin, no
 * cashier-performance column — a cashier holds `orders.view`, not
 * `reports.financial`, and putting numbers on a screen that the role is not
 * trusted with is how a permission boundary becomes decorative. What is here is
 * what somebody standing at a counter points at: the number, the table, the time,
 * what it came to, and whether it is settled.
 */
import { computed, onMounted, ref } from 'vue'

import { ApiError, api } from '@/api/client'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import { money, time } from '@/lib/format'

interface OrderRow {
  id: string
  local_number: string
  order_type: string
  status: string
  table_number: string | null
  grand_total: string
  paid_total: string
  opened_at: string
  item_count: number
}

const rows = ref<OrderRow[]>([])
const loading = ref(true)
const error = ref('')

/**
 * `YYYY-MM-DD` from the local clock.
 *
 * Not `toISOString()`, which converts to UTC first: Egypt is UTC+3, so from
 * midnight until 03:00 — the tail of every trading night, which is exactly when a
 * cashier is closing up — it returns yesterday and the list silently shows the
 * wrong day.
 */
function localDay(d = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

const day = ref(localDay())
const isToday = computed(() => day.value === localDay())

const TYPES: Record<string, string> = {
  DINE_IN: 'صالة',
  TAKE_AWAY: 'تيك أواي',
  DELIVERY: 'توصيل',
}

const STATUS: Record<string, { label: string; tone: string }> = {
  OPEN: { label: 'مفتوح', tone: 'is-open' },
  IN_KITCHEN: { label: 'في المطبخ', tone: 'is-kitchen' },
  READY: { label: 'جاهز', tone: 'is-ready' },
  SERVED: { label: 'تم التقديم', tone: 'is-ready' },
  PAID: { label: 'مدفوع', tone: 'is-paid' },
  VOID: { label: 'ملغي', tone: 'is-void' },
}

/** Still owing money. The one thing worth counting at the top of this screen. */
const unpaid = computed(() =>
  rows.value.filter((row) => row.status !== 'PAID' && row.status !== 'VOID'),
)

const takenToday = computed(() =>
  rows.value
    .filter((row) => row.status === 'PAID')
    .reduce((total, row) => total + Number(row.paid_total), 0),
)

async function load() {
  loading.value = true
  try {
    // A whole calendar day, inclusive. The server filters on `opened_at`, so the
    // end of the window has to be the last instant of the day rather than
    // midnight — otherwise every order after 00:00 on the chosen date is missing.
    rows.value = await api.get<OrderRow[]>(
      `/orders/?date_from=${day.value}T00:00:00&date_to=${day.value}T23:59:59`,
    )
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تحميل الطلبات.'
  } finally {
    loading.value = false
  }
}

function shiftDay(delta: number) {
  const d = new Date(`${day.value}T12:00:00`)
  d.setDate(d.getDate() + delta)
  day.value = localDay(d)
  load()
}

function backToToday() {
  day.value = localDay()
  load()
}

onMounted(load)
</script>

<template>
  <div class="orders">
    <header class="head">
      <div>
        <h1 class="title">الطلبات</h1>
        <p class="sub">
          <template v-if="isToday">طلبات اليوم</template>
          <template v-else>طلبات يوم {{ day }}</template>
        </p>
      </div>

      <div class="filters">
        <button type="button" class="nav" @click="shiftDay(-1)">اليوم السابق</button>
        <input v-model="day" type="date" class="picker" @change="load" />
        <button type="button" class="nav" :disabled="isToday" @click="shiftDay(1)">
          اليوم التالي
        </button>
        <!-- A way back, because a cashier who wandered into last Tuesday should
             not have to work out today's date to return. -->
        <button v-if="!isToday" type="button" class="today" @click="backToToday">
          رجوع لليوم
        </button>
      </div>
    </header>

    <div class="stats">
      <div class="stat">
        <span class="stat-label">عدد الطلبات</span>
        <span class="stat-value">{{ rows.length }}</span>
      </div>
      <div class="stat">
        <span class="stat-label">لسه مفتوح</span>
        <span class="stat-value" :class="unpaid.length ? 'is-warn' : ''">{{ unpaid.length }}</span>
      </div>
      <div class="stat">
        <span class="stat-label">محصَّل</span>
        <span class="stat-value">{{ money(takenToday) }}</span>
      </div>
    </div>

    <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>

    <UiSkeleton v-if="loading" :rows="6" />

    <p v-else-if="!rows.length" class="empty">
      <template v-if="isToday">لا توجد طلبات اليوم بعد.</template>
      <template v-else>لا توجد طلبات في هذا اليوم.</template>
    </p>

    <ul v-else class="list">
      <li v-for="row in rows" :key="row.id" class="row">
        <div class="row-main">
          <span class="num" dir="ltr">{{ row.local_number }}</span>
          <span class="badge" :class="STATUS[row.status]?.tone">
            {{ STATUS[row.status]?.label ?? row.status }}
          </span>
        </div>
        <div class="row-meta">
          {{ time(row.opened_at) }}
          · {{ TYPES[row.order_type] ?? row.order_type }}
          <template v-if="row.table_number"> · ترابيزة {{ row.table_number }}</template>
          · {{ row.item_count }} صنف
        </div>
        <div class="row-total tabular-nums">{{ money(row.grand_total) }}</div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.orders {
  flex: 1 1 auto;
  overflow-y: auto;
  padding: 1rem 1.25rem 2rem;
}

.head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: space-between;
  gap: 1rem;
}
.title {
  font-size: 1.5rem;
  font-weight: 800;
  color: var(--ink);
}
.sub {
  margin-top: 0.15rem;
  font-size: 0.9rem;
  color: var(--ink-muted);
}

.filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}
/* 44px minimum, like every other control on the till: this is used with a thumb. */
.nav,
.today {
  min-height: 44px;
  padding: 0 1rem;
  border-radius: 0.6rem;
  border: 1px solid var(--border-strong);
  background: var(--surface);
  font-weight: 600;
  color: var(--ink);
}
.nav:disabled {
  opacity: 0.45;
}
.today {
  border-color: transparent;
  color: var(--fg-on-brand);
  background-image: var(--brand-gradient);
}
.picker {
  min-height: 44px;
  padding: 0 0.75rem;
  border-radius: 0.6rem;
  border: 1px solid var(--border-strong);
  background: var(--surface);
}

.stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr));
  gap: 0.75rem;
  margin: 1rem 0;
}
.stat {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  padding: 0.85rem 1rem;
  border: 1px solid var(--border);
  border-radius: 0.75rem;
  background: var(--surface);
  box-shadow: var(--shadow-sm);
}
.stat-label {
  font-size: 0.8rem;
  color: var(--ink-muted);
}
.stat-value {
  font-size: 1.35rem;
  font-weight: 800;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}
.stat-value.is-warn {
  color: var(--warning);
}

.empty {
  padding: 3rem 1rem;
  text-align: center;
  color: var(--ink-muted);
}

.list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.row {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 0.15rem 1rem;
  align-items: center;
  padding: 0.85rem 1rem;
  border: 1px solid var(--border);
  border-radius: 0.75rem;
  background: var(--surface);
}
.row-main {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.num {
  font-weight: 700;
  color: var(--ink);
}
.row-meta {
  grid-column: 1;
  font-size: 0.85rem;
  color: var(--ink-muted);
}
.row-total {
  grid-row: 1 / span 2;
  grid-column: 2;
  font-size: 1.1rem;
  font-weight: 800;
  color: var(--ink);
}

/* Colour is never the only signal — every badge carries its word too. */
.badge {
  padding: 0.15rem 0.6rem;
  border-radius: 999px;
  font-size: 0.75rem;
  font-weight: 700;
  border: 1px solid;
}
.badge.is-open {
  color: var(--warning);
  background: var(--warning-bg);
  border-color: var(--warning);
}
.badge.is-kitchen {
  color: var(--info);
  background: var(--info-bg);
  border-color: var(--info);
}
.badge.is-ready {
  color: var(--success);
  background: var(--success-bg);
  border-color: var(--success);
}
.badge.is-paid {
  color: var(--ink-muted);
  background: var(--surface-sunken);
  border-color: var(--border-strong);
}
.badge.is-void {
  color: var(--danger);
  background: var(--danger-bg);
  border-color: var(--danger);
}
</style>
