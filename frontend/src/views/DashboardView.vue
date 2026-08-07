<script setup lang="ts">
/**
 * The dashboard.
 *
 * Alerts sit ABOVE the charts on purpose: charts describe the past, alerts
 * describe something that needs a decision now. An owner opening this on a
 * phone between meetings should see the problems without scrolling.
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { api } from '@/api/client'
import UiBadge from '@/components/ui/UiBadge.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import { useAuthStore } from '@/stores/auth'
import { money } from '@/lib/format'

const auth = useAuthStore()
const loading = ref(true)

const openOrders = ref<{ id: string; local_number: string; grand_total: string; status: string; table_number: string | null; item_count: number }[]>([])
const lowStock = ref<{ item_code: string; item_name: string; quantity_on_hand: string; unit_code: string }[]>([])
const currentShift = ref<{ id: string; user_name: string | null; opening_cash: string; opened_at: string } | null>(null)

const openTotal = computed(() =>
  openOrders.value.reduce((sum, order) => sum + Number(order.grand_total), 0),
)

onMounted(async () => {
  const jobs: Promise<unknown>[] = []

  if (auth.can('orders.view')) {
    jobs.push(
      api
        .get<typeof openOrders.value>('/orders/', { open: 'true' })
        .then((rows) => (openOrders.value = rows))
        .catch(() => undefined),
    )
  }
  if (auth.can('inventory.view')) {
    jobs.push(
      api
        .get<typeof lowStock.value>('/inventory/levels/', { low_stock: 'true' })
        .then((rows) => (lowStock.value = rows))
        .catch(() => undefined),
    )
  }
  jobs.push(
    api
      .get<{ shift: typeof currentShift.value }>('/shifts/current/')
      .then((result) => (currentShift.value = result.shift))
      .catch(() => undefined),
  )

  await Promise.all(jobs)
  loading.value = false
})
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-bold text-slate-900">لوحة التحكم</h1>
      <p class="mt-1 text-sm text-slate-500">
        أهلاً {{ auth.me?.full_name_ar }} — نظرة سريعة على الوضع الآن.
      </p>
    </div>

    <UiSkeleton v-if="loading" :rows="4" />

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

      <div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <UiCard>
          <p class="text-sm text-slate-500">الطلبات المفتوحة</p>
          <p class="mt-1 text-3xl font-bold text-slate-900">{{ openOrders.length }}</p>
        </UiCard>

        <UiCard>
          <p class="text-sm text-slate-500">قيمة الطلبات المفتوحة</p>
          <p class="mt-1 text-3xl font-bold text-slate-900">{{ money(openTotal) }}</p>
        </UiCard>

        <UiCard>
          <p class="text-sm text-slate-500">الوردية الحالية</p>
          <template v-if="currentShift">
            <p class="mt-1 text-lg font-bold text-slate-900">
              {{ currentShift.user_name ?? '—' }}
            </p>
            <p class="mt-0.5 text-sm text-slate-500">
              رصيد افتتاحي {{ money(currentShift.opening_cash) }}
            </p>
          </template>
          <p v-else class="mt-1 text-lg font-medium text-slate-400">لا توجد وردية مفتوحة</p>
        </UiCard>
      </div>

      <UiCard title="الطلبات المفتوحة الآن">
        <div v-if="!openOrders.length" class="py-8 text-center text-sm text-slate-500">
          لا توجد طلبات مفتوحة.
        </div>
        <ul v-else class="divide-y divide-slate-100">
          <li v-for="order in openOrders" :key="order.id">
            <RouterLink
              :to="`/orders/${order.id}`"
              class="flex items-center justify-between gap-4 px-1 py-3 hover:bg-slate-50"
            >
              <div>
                <p class="font-medium text-slate-900">{{ order.local_number }}</p>
                <p class="text-sm text-slate-500">
                  {{ order.table_number ? `طاولة ${order.table_number}` : 'تيك أواي' }} ·
                  {{ order.item_count }} صنف
                </p>
              </div>
              <div class="flex items-center gap-3">
                <UiBadge :tone="order.status === 'IN_KITCHEN' ? 'warning' : 'info'">
                  {{ order.status }}
                </UiBadge>
                <span class="font-semibold tabular-nums">{{ money(order.grand_total) }}</span>
              </div>
            </RouterLink>
          </li>
        </ul>
      </UiCard>
    </template>
  </div>
</template>
