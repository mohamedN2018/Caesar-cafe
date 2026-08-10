<script setup lang="ts">
/**
 * The dashboard.
 *
 * One request for the numbers — `/reports/dashboard/` — because the owner opens
 * this on a phone over a mobile connection, and eight round-trips to render one
 * screen is the difference between a dashboard they check and one they stop
 * opening (C11).
 *
 * **It was eight identical cards.** Every figure the same size in the same box,
 * so nothing led and the eye had to read all eight to find the one that
 * mattered — which is the same as reading none. A dashboard's whole job is to
 * answer "how is today going" before you have finished looking at it.
 *
 * So it is now four bands, in the order a decision gets made:
 *
 *   1. **Alerts.** Things needing a decision now. Above the numbers, because
 *      numbers describe the past.
 *   2. **The hero.** Today's takings, once, large. Exactly one hero per view —
 *      a second would be two things claiming to be the answer.
 *   3. **Money that qualifies it** — profit, orders, average ticket, the week.
 *   4. **The room right now** — counts, not currency, and deliberately styled
 *      differently so they do not read as more money.
 *
 * Then the two charts, which are for looking into rather than glancing at.
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { api } from '@/api/client'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiColumns from '@/components/ui/UiColumns.vue'
import UiIcon from '@/components/ui/UiIcon.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import UiStat from '@/components/ui/UiStat.vue'
import { money } from '@/lib/format'
import { useAuthStore } from '@/stores/auth'

interface Summary {
  net_sales: string
  gross_sales: string
  discounts: string
  refunds: string
  cogs: string
  gross_profit: string
  margin_percent: string
  order_count: number
  void_count: number
  average_ticket: string
  cash_sales: string
  non_cash_sales: string
}

interface Dashboard {
  business_date: string
  boundary: string
  today: Summary
  yesterday_net: string
  change_percent: string | null
  week: { net_sales: string; order_count: number; average_ticket: string }
  open_orders: number
  open_orders_value: string
  open_tickets: number
  open_shifts: number
  kids_inside: number
  top_products: { variant_id: string; name: string; quantity: string; revenue: string }[]
  by_hour: { hour: number; order_count: number; net_sales: string }[]
}

interface LowStock {
  item_code: string
  item_name: string
  quantity_on_hand: string
  unit_code: string
}

const auth = useAuthStore()
const loading = ref(true)
const failed = ref(false)
const board = ref<Dashboard | null>(null)
const lowStock = ref<LowStock[]>([])

const change = computed(() => {
  const raw = board.value?.change_percent
  return raw === null || raw === undefined ? null : Number(raw)
})

/** Trading hours only. Padding the axis with a silent 4am says nothing. */
const hours = computed(() => {
  const rows = board.value?.by_hour ?? []
  const trading = rows.filter((h) => h.order_count > 0)
  return trading.map((h) => ({ label: `${h.hour}`, value: Number(h.net_sales) }))
})

/** The busiest line, so the bars below it are read as proportions of something. */
const topRevenue = computed(() =>
  Math.max(1, ...(board.value?.top_products ?? []).map((p) => Number(p.revenue))),
)

/**
 * The operations strip. Counts, not money — built from the same board so the
 * template stays a list rather than four near-identical blocks.
 */
const room = computed(() => {
  const data = board.value
  if (!data) return []
  return [
    { icon: 'receipt', label: 'طلبات مفتوحة', value: data.open_orders, note: money(data.open_orders_value), to: '/orders' },
    { icon: 'kitchen', label: 'تذاكر في المطبخ', value: data.open_tickets, note: '', to: '/kitchen' },
    { icon: 'kids', label: 'أطفال في الصالة', value: data.kids_inside, note: '', to: '/kids' },
    { icon: 'cash', label: 'ورديات مفتوحة', value: data.open_shifts, note: '', to: '/shifts' },
  ]
})

onMounted(async () => {
  // Each panel is asked for only by somebody who may have it, and `optional`
  // turns a refusal into an absent panel rather than a message. A user is never
  // told off for a request the interface made on their behalf.
  const jobs: Promise<unknown>[] = []

  if (auth.can('reports.sales')) {
    jobs.push(
      api
        .optional<Dashboard>('/reports/dashboard/')
        .then((data) => (board.value = data))
        // An outage is not a "no". The numbers stay absent and the banner says
        // why, rather than the screen pretending the day had no sales.
        .catch(() => (failed.value = true)),
    )
  }
  if (auth.can('inventory.view')) {
    jobs.push(
      api
        .optional<LowStock[]>('/inventory/levels/', { low_stock: 'true' })
        .then((rows) => (lowStock.value = rows ?? []))
        .catch(() => (failed.value = true)),
    )
  }

  await Promise.all(jobs)
  loading.value = false
})
</script>

<template>
  <div class="space-y-6">
    <header class="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 class="text-2xl font-bold text-ink">لوحة التحكم</h1>
        <p class="mt-1 text-sm text-ink-muted">
          أهلاً {{ auth.me?.full_name_ar }}
          <template v-if="board">
            — يوم العمل {{ board.business_date }}، يبدأ {{ board.boundary }}
          </template>
        </p>
      </div>
      <RouterLink
        v-if="auth.can('reports.sales')"
        to="/reports"
        class="rounded-lg px-3 py-2 text-sm font-medium text-brand-800 ring-1 ring-inset ring-brand-200 hover:bg-brand-50"
      >
        التقارير التفصيلية
      </RouterLink>
    </header>

    <UiSkeleton v-if="loading" :rows="5" />

    <template v-else>
      <!-- Alerts first: they need a decision, the numbers below do not. -->
      <UiCard v-if="lowStock.length">
        <div class="mb-3 flex items-center gap-2 text-warning">
          <UiIcon name="box" size="1.1rem" />
          <h2 class="text-base font-semibold">مخزون قارب على الانتهاء</h2>
        </div>
        <ul class="grid gap-2 sm:grid-cols-2">
          <li
            v-for="level in lowStock.slice(0, 6)"
            :key="level.item_code"
            class="flex items-center justify-between gap-4 rounded-lg bg-warning-bg px-3.5 py-2.5"
          >
            <span class="font-medium text-ink">{{ level.item_name }}</span>
            <span class="text-sm tabular-nums text-warning">
              {{ level.quantity_on_hand }} {{ level.unit_code }}
            </span>
          </li>
        </ul>
        <RouterLink
          v-if="lowStock.length > 6"
          to="/stock"
          class="mt-3 inline-block text-sm text-brand-700 hover:underline"
        >
          و{{ lowStock.length - 6 }} صنف آخر
        </RouterLink>
      </UiCard>

      <!-- No "you lack permission" banner. Somebody without the reports code
           simply has a dashboard without numbers on it, and it reads as a
           complete screen rather than a locked one. An OUTAGE does get said
           out loud — a blank panel that means "we could not reach the server"
           must not look like a quiet day. -->
      <UiAlert v-if="failed" tone="warning">
        تعذّر تحميل بعض الأرقام — جرّب التحديث بعد قليل.
      </UiAlert>

      <template v-if="board">
        <!-- The hero. Exactly one per view. -->
        <section class="hero">
          <div>
            <p class="hero-label">صافي مبيعات اليوم</p>
            <p class="hero-value">{{ money(board.today.net_sales) }}</p>
            <p v-if="change !== null" class="hero-delta" :class="change >= 0 ? 'is-up' : 'is-down'">
              <span aria-hidden="true">{{ change >= 0 ? '▲' : '▼' }}</span>
              {{ Math.abs(change) }}% عن أمس
              <span class="hero-base">({{ money(board.yesterday_net) }})</span>
            </p>
            <p v-else class="hero-delta is-flat">لا توجد مبيعات أمس للمقارنة</p>
          </div>

          <dl class="hero-split">
            <div>
              <dt>نقدي</dt>
              <dd class="tabular-nums">{{ money(board.today.cash_sales) }}</dd>
            </div>
            <div>
              <dt>غير نقدي</dt>
              <dd class="tabular-nums">{{ money(board.today.non_cash_sales) }}</dd>
            </div>
          </dl>
        </section>

        <!-- Money that qualifies the hero. -->
        <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <UiStat
            label="مجمل الربح"
            :value="money(board.today.gross_profit)"
            :hint="`هامش ${board.today.margin_percent}%`"
          />
          <UiStat
            label="عدد الطلبات"
            :value="String(board.today.order_count)"
            :hint="board.today.void_count ? `${board.today.void_count} صنف ملغي` : 'لا إلغاءات'"
          />
          <UiStat label="متوسط الفاتورة" :value="money(board.today.average_ticket)" />
          <UiStat
            label="آخر ٧ أيام"
            :value="money(board.week.net_sales)"
            :hint="`${board.week.order_count} طلب`"
          />
        </div>

        <!-- The room right now. Counts, and styled so they do not read as money. -->
        <div class="room">
          <RouterLink v-for="cell in room" :key="cell.label" :to="cell.to" class="room-cell">
            <UiIcon :name="cell.icon" size="1.1rem" />
            <span class="room-value">{{ cell.value }}</span>
            <span class="room-label">{{ cell.label }}</span>
            <span v-if="cell.note" class="room-note tabular-nums">{{ cell.note }}</span>
          </RouterLink>
        </div>

        <div class="grid gap-4 lg:grid-cols-2">
          <UiCard title="المبيعات حسب الساعة" subtitle="صافي المبيعات في كل ساعة عمل">
            <p v-if="!hours.length" class="py-10 text-center text-sm text-ink-muted">
              لا توجد مبيعات اليوم بعد.
            </p>
            <UiColumns v-else :points="hours" :format="(v) => money(v).replace(' ج.م', '')" />
          </UiCard>

          <UiCard title="الأكثر بيعاً اليوم">
            <p
              v-if="!board.top_products.length"
              class="py-10 text-center text-sm text-ink-muted"
            >
              لا توجد مبيعات اليوم بعد.
            </p>
            <ul v-else class="space-y-3">
              <li v-for="product in board.top_products" :key="product.variant_id">
                <div class="flex items-baseline justify-between gap-4">
                  <span class="font-medium text-ink">{{ product.name }}</span>
                  <span class="shrink-0 text-sm text-ink-muted">
                    <span class="tabular-nums">{{ product.quantity }}</span> ×
                    <span class="font-semibold text-ink">{{ money(product.revenue) }}</span>
                  </span>
                </div>
                <!--
                  A bar under each row rather than numbers alone: ranked revenue
                  is a magnitude comparison, and the eye reads relative length
                  far faster than it reads four-digit figures.
                -->
                <div class="rank-track">
                  <span
                    class="rank-fill"
                    :style="{ width: `${(Number(product.revenue) / topRevenue) * 100}%` }"
                  />
                </div>
              </li>
            </ul>
          </UiCard>
        </div>
      </template>
    </template>
  </div>
</template>

<style scoped>
.hero {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: space-between;
  gap: 1.5rem;
  padding: 1.5rem 1.75rem;
  border-radius: 1rem;
  background: linear-gradient(135deg, var(--brand-800), var(--brand-700) 60%);
  color: var(--fg-on-brand);
}

.hero-label {
  font-size: 0.9rem;
  opacity: 0.8;
}

.hero-value {
  margin-top: 0.35rem;
  /* The one number the screen leads with. Same sans as everything else — a
     display or serif face here reads as decoration rather than data. */
  font-size: clamp(2.25rem, 5vw, 3rem);
  font-weight: 700;
  line-height: 1.05;
  /* Proportional, not tabular: at this size tabular digits look loose. */
  font-variant-numeric: proportional-nums;
}

.hero-delta {
  margin-top: 0.5rem;
  font-size: 0.88rem;
  font-weight: 600;
}
.hero-delta.is-up {
  color: #a7e8bd;
}
.hero-delta.is-down {
  color: #f6b8b3;
}
.hero-delta.is-flat {
  opacity: 0.7;
  font-weight: 400;
}
.hero-base {
  font-weight: 400;
  opacity: 0.75;
}

.hero-split {
  display: flex;
  gap: 2rem;
}
.hero-split dt {
  font-size: 0.78rem;
  opacity: 0.75;
}
.hero-split dd {
  margin-top: 0.15rem;
  font-size: 1.05rem;
  font-weight: 600;
}

.room {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr));
  gap: 0.75rem;
}

.room-cell {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.1rem;
  padding: 0.85rem 1rem;
  border-radius: 0.75rem;
  border: 1px solid var(--border);
  background: var(--surface-muted);
  color: var(--ink-muted);
  transition: border-color 0.12s ease;
}
.room-cell:hover {
  border-color: var(--border-strong);
}

.room-value {
  font-size: 1.5rem;
  font-weight: 650;
  color: var(--ink);
  line-height: 1.2;
}
.room-label {
  font-size: 0.78rem;
}
.room-note {
  font-size: 0.72rem;
  color: var(--ink-faint);
}

.rank-track {
  margin-top: 0.4rem;
  height: 6px;
  border-radius: 999px;
  background: var(--surface-sunken);
  overflow: hidden;
}
.rank-fill {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: var(--brand-700);
}
</style>
