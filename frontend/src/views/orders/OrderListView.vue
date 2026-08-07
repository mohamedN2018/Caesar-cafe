<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { api } from '@/api/client'
import UiBadge from '@/components/ui/UiBadge.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import UiTable from '@/components/ui/UiTable.vue'
import { dateTime, money } from '@/lib/format'

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

type Tone = 'success' | 'danger' | 'info' | 'warning' | 'neutral'

const STATUSES: Record<string, { label: string; tone: Tone }> = {
  DRAFT: { label: 'مسودة', tone: 'neutral' },
  OPEN: { label: 'مفتوح', tone: 'info' },
  IN_KITCHEN: { label: 'في المطبخ', tone: 'warning' },
  READY: { label: 'جاهز', tone: 'success' },
  SERVED: { label: 'تم التقديم', tone: 'success' },
  PAID: { label: 'مدفوع', tone: 'success' },
  CANCELLED: { label: 'ملغي', tone: 'danger' },
  REFUNDED: { label: 'مسترجع', tone: 'danger' },
}

const TYPES: Record<string, string> = {
  DINE_IN: 'صالة',
  TAKE_AWAY: 'تيك أواي',
  DELIVERY: 'توصيل',
}

const orders = ref<OrderRow[]>([])
const loading = ref(true)
const openOnly = ref(false)

const columns = [
  { key: 'number', label: 'رقم الطلب' },
  { key: 'type', label: 'النوع' },
  { key: 'items', label: 'الأصناف', align: 'end' as const },
  { key: 'total', label: 'الإجمالي', align: 'end' as const },
  { key: 'status', label: 'الحالة' },
  { key: 'when', label: 'الوقت' },
]

function toneFor(status: string): Tone {
  return STATUSES[status]?.tone ?? 'neutral'
}

function labelFor(status: string): string {
  return STATUSES[status]?.label ?? status
}

async function load() {
  loading.value = true
  try {
    orders.value = await api.get<OrderRow[]>(
      '/orders/',
      openOnly.value ? { open: 'true' } : undefined,
    )
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 class="text-2xl font-bold text-slate-900">الطلبات</h1>
        <p class="mt-1 text-sm text-slate-500">
          كل طلب له سلسلة أحداث كاملة تشرح كيف وصل إجماليه لهذا الرقم.
        </p>
      </div>
      <label class="flex items-center gap-2 text-sm text-slate-700">
        <input
          v-model="openOnly"
          type="checkbox"
          class="h-5 w-5 rounded border-slate-300"
          @change="load"
        />
        المفتوحة فقط
      </label>
    </div>

    <UiSkeleton v-if="loading" :rows="8" />

    <UiCard v-else>
      <UiEmpty
        v-if="!orders.length"
        icon="🧾"
        :title="openOnly ? 'لا توجد طلبات مفتوحة' : 'لا توجد طلبات'"
        description="ستظهر هنا الطلبات فور إنشائها من نقطة البيع."
      />
      <UiTable v-else :columns="columns">
        <tr v-for="order in orders" :key="order.id" class="hover:bg-slate-50">
          <td class="px-4 py-3">
            <RouterLink
              :to="`/orders/${order.id}`"
              class="font-medium text-brand-700 hover:underline"
            >
              {{ order.local_number }}
            </RouterLink>
            <p v-if="order.table_number" class="text-xs text-slate-500">
              طاولة {{ order.table_number }}
            </p>
          </td>
          <td class="px-4 py-3 text-slate-600">{{ TYPES[order.order_type] ?? order.order_type }}</td>
          <td class="px-4 py-3 text-end tabular-nums text-slate-600">{{ order.item_count }}</td>
          <td class="px-4 py-3 text-end font-medium tabular-nums">{{ money(order.grand_total) }}</td>
          <td class="px-4 py-3">
            <UiBadge :tone="toneFor(order.status)">{{ labelFor(order.status) }}</UiBadge>
          </td>
          <td class="whitespace-nowrap px-4 py-3 text-sm text-slate-500">
            {{ dateTime(order.opened_at) }}
          </td>
        </tr>
      </UiTable>
    </UiCard>
  </div>
</template>
