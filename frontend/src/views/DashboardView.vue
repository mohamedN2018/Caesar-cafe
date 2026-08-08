<script setup lang="ts">
/**
 * The dashboard.
 *
 * One request for the numbers — `/reports/dashboard/` — because the owner opens
 * this on a phone over a mobile connection, and eight round-trips to render one
 * screen is the difference between a dashboard they check and one they stop
 * opening (C11).
 *
 * Alerts sit ABOVE the numbers on purpose: numbers describe the past, alerts
 * describe something that needs a decision now.
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { api } from '@/api/client'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import { useAuthStore } from '@/stores/auth'
import { money } from '@/lib/format'

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
const board = ref<Dashboard | null>(null)
const lowStock = ref<LowStock[]>([])

const change = computed(() => {
  const raw = board.value?.change_percent
  return raw === null || raw === undefined ? null : Number(raw)
})

const peak = computed(() => {
  const hours = board.value?.by_hour ?? []
  return Math.max(1, ...hours.map((h) => Number(h.net_sales)))
})

const busiestHours = computed(() =>
  (board.value?.by_hour ?? []).filter((hour) => hour.order_count > 0),
)

onMounted(async () => {
  const jobs: Promise<unknown>[] = []

  if (auth.can('reports.sales')) {
    jobs.push(
      api
        .get<Dashboard>('/reports/dashboard/')
        .then((data) => (board.value = data))
        .catch(() => undefined),
    )
  }
  if (auth.can('inventory.view')) {
    jobs.push(
      api
        .get<LowStock[]>('/inventory/levels/', { low_stock: 'true' })
        .then((rows) => (lowStock.value = rows))
        .catch(() => undefined),
    )
  }

  await Promise.all(jobs)
  loading.value = false
})
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 class="text-2xl font-bold text-slate-900">لوحة التحكم</h1>
        <p class="mt-1 text-sm text-slate-500">
          أهلاً {{ auth.me?.full_name_ar }}
          <template v-if="board">
            — يوم العمل {{ board.business_date }} (يبدأ {{ board.boundary }})
          </template>
        </p>
      </div>
      <RouterLink
        v-if="auth.can('reports.sales')"
        to="/reports"
        class="rounded-lg px-3 py-2 text-sm font-medium text-brand-800 ring-1 ring-inset ring-brand-200 hover:bg-brand-50"
      >
        التقارير التفصيلية ←
      </RouterLink>
    </div>

    <UiSkeleton v-if="loading" :rows="5" />

    <template v-else>
      <!-- Alerts first: they need a decision, the numbers below do not. -->
      <UiCard v-if="lowStock.length" title="⚠️ تنبيهات المخزون">
        <ul class="space-y-2">
          <li
            v-for="level in lowStock.slice(0, 6)"
            :key="level.item_code"
            class="flex items-center justify-between gap-4 rounded-lg bg-amber-50 px-4 py-2.5"
          >
            <span class="font-medium text-amber-900">{{ level.item_name }}</span>
            <span class="text-sm text-amber-800">
              المتاح {{ level.quantity_on_hand }} {{ level.unit_code }}
            </span>
          </li>
        </ul>
        <RouterLink
          v-if="lowStock.length > 6"
          to="/stock"
          class="mt-3 inline-block text-sm text-brand-700 hover:underline"
        >
          و{{ lowStock.length - 6 }} صنف آخر ←
        </RouterLink>
      </UiCard>

      <UiAlert v-if="!board" tone="info">
        لوحة الأرقام تحتاج صلاحية تقارير المبيعات.
      </UiAlert>

      <template v-else>
        <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <UiCard>
            <p class="text-sm text-slate-500">صافي مبيعات اليوم</p>
            <p class="mt-1 text-3xl font-bold text-slate-900">
              {{ money(board.today.net_sales) }}
            </p>
            <p
              v-if="change !== null"
              class="mt-1 text-sm font-medium"
              :class="change >= 0 ? 'text-emerald-700' : 'text-red-700'"
            >
              {{ change >= 0 ? '▲' : '▼' }} {{ Math.abs(change) }}% مقارنة بأمس
              ({{ money(board.yesterday_net) }})
            </p>
            <p v-else class="mt-1 text-xs text-slate-400">لا توجد مبيعات أمس للمقارنة</p>
          </UiCard>

          <UiCard>
            <p class="text-sm text-slate-500">مجمل الربح اليوم</p>
            <p class="mt-1 text-3xl font-bold text-slate-900">
              {{ money(board.today.gross_profit) }}
            </p>
            <p class="mt-1 text-sm text-slate-500">هامش {{ board.today.margin_percent }}%</p>
          </UiCard>

          <UiCard>
            <p class="text-sm text-slate-500">الطلبات · متوسط الفاتورة</p>
            <p class="mt-1 text-3xl font-bold text-slate-900">{{ board.today.order_count }}</p>
            <p class="mt-1 text-sm text-slate-500">{{ money(board.today.average_ticket) }}</p>
          </UiCard>

          <UiCard>
            <p class="text-sm text-slate-500">آخر ٧ أيام</p>
            <p class="mt-1 text-3xl font-bold text-slate-900">{{ money(board.week.net_sales) }}</p>
            <p class="mt-1 text-sm text-slate-500">{{ board.week.order_count }} طلب</p>
          </UiCard>
        </div>

        <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <UiCard>
            <p class="text-sm text-slate-500">طلبات مفتوحة</p>
            <p class="mt-1 text-2xl font-bold text-slate-900">{{ board.open_orders }}</p>
            <p class="mt-1 text-sm text-slate-500">{{ money(board.open_orders_value) }}</p>
          </UiCard>
          <UiCard>
            <p class="text-sm text-slate-500">تذاكر في المطبخ</p>
            <p class="mt-1 text-2xl font-bold text-slate-900">{{ board.open_tickets }}</p>
          </UiCard>
          <UiCard>
            <p class="text-sm text-slate-500">أطفال في الصالة</p>
            <p class="mt-1 text-2xl font-bold text-slate-900">{{ board.kids_inside }}</p>
          </UiCard>
          <UiCard>
            <p class="text-sm text-slate-500">ورديات مفتوحة</p>
            <p class="mt-1 text-2xl font-bold text-slate-900">{{ board.open_shifts }}</p>
          </UiCard>
        </div>

        <div class="grid gap-4 lg:grid-cols-2">
          <UiCard title="المبيعات حسب الساعة">
            <div
              v-if="!busiestHours.length"
              class="py-8 text-center text-sm text-slate-500"
            >
              لا توجد مبيعات اليوم بعد.
            </div>
            <div v-else class="flex items-end gap-1 overflow-x-auto" dir="ltr">
              <div
                v-for="hour in busiestHours"
                :key="hour.hour"
                class="flex min-w-8 flex-1 flex-col items-center gap-1"
              >
                <span class="text-xs tabular-nums text-slate-500">{{ hour.order_count }}</span>
                <div
                  class="w-full rounded-t bg-brand-500"
                  :style="{
                    height: `${Math.max(4, (Number(hour.net_sales) / peak) * 120)}px`,
                  }"
                  :title="`${hour.hour}:00 — ${hour.net_sales}`"
                />
                <span class="text-xs tabular-nums text-slate-400">{{ hour.hour }}</span>
              </div>
            </div>
          </UiCard>

          <UiCard title="الأكثر بيعاً اليوم">
            <div v-if="!board.top_products.length" class="py-8 text-center text-sm text-slate-500">
              لا توجد مبيعات اليوم بعد.
            </div>
            <ul v-else class="divide-y divide-slate-100">
              <li
                v-for="product in board.top_products"
                :key="product.variant_id"
                class="flex items-center justify-between gap-4 py-3"
              >
                <span class="font-medium text-slate-900">{{ product.name }}</span>
                <span class="text-sm text-slate-500">
                  {{ product.quantity }} × ·
                  <span class="font-semibold text-slate-900">{{ money(product.revenue) }}</span>
                </span>
              </li>
            </ul>
          </UiCard>
        </div>
      </template>
    </template>
  </div>
</template>
