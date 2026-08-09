<script setup lang="ts">
/**
 * Shift history.
 *
 * The variance column is one of the two loss-prevention signals in the product
 * (the other is void rate per user). Consistent negative variance concentrated
 * on one person is what cash shrinkage looks like in the data.
 */
import { onMounted, ref } from 'vue'

import { api } from '@/api/client'
import UiBadge from '@/components/ui/UiBadge.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import UiTable from '@/components/ui/UiTable.vue'
import { dateTime, money } from '@/lib/format'

interface Shift {
  id: string
  status: string
  user_name: string | null
  opening_cash: string
  counted_cash: string | null
  variance: string | null
  variance_reason: string
  opened_at: string
  closed_at: string | null
  closed_by_name: string | null
}

const shifts = ref<Shift[]>([])
const loading = ref(true)

const columns = [
  { key: 'user', label: 'الكاشير' },
  { key: 'opened', label: 'الفتح' },
  { key: 'closed', label: 'الإغلاق' },
  { key: 'opening', label: 'رصيد افتتاحي', align: 'end' as const },
  { key: 'counted', label: 'المعدود', align: 'end' as const },
  { key: 'variance', label: 'الفرق', align: 'end' as const },
  { key: 'status', label: 'الحالة' },
]

function varianceTone(variance: string | null): 'success' | 'warning' | 'danger' | 'neutral' {
  if (variance === null) return 'neutral'
  const value = Math.abs(Number(variance))
  if (value === 0) return 'success'
  return value > 50 ? 'danger' : 'warning'
}

onMounted(async () => {
  try {
    shifts.value = await api.get<Shift[]>('/shifts/')
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-bold text-slate-900">الورديات</h1>
      <p class="mt-1 text-sm text-slate-500">
        فرق النقدية المتكرر على نفس الشخص هو أول إشارة على وجود مشكلة.
      </p>
    </div>

    <UiSkeleton v-if="loading" :rows="6" />

    <UiCard v-else>
      <UiEmpty
        v-if="!shifts.length"
        icon="cash"
        title="لا توجد ورديات"
        description="تُفتح الوردية من نقطة البيع قبل أول عملية بيع."
      />
      <UiTable v-else :columns="columns">
        <tr v-for="shift in shifts" :key="shift.id" class="hover:bg-slate-50">
          <td class="px-4 py-3 font-medium text-slate-900">{{ shift.user_name ?? '—' }}</td>
          <td class="whitespace-nowrap px-4 py-3 text-sm text-slate-500">
            {{ dateTime(shift.opened_at) }}
          </td>
          <td class="whitespace-nowrap px-4 py-3 text-sm text-slate-500">
            {{ dateTime(shift.closed_at) }}
          </td>
          <td class="px-4 py-3 text-end tabular-nums">{{ money(shift.opening_cash) }}</td>
          <td class="px-4 py-3 text-end tabular-nums">{{ money(shift.counted_cash) }}</td>
          <td class="px-4 py-3 text-end">
            <div v-if="shift.variance !== null" class="inline-flex flex-col items-end gap-1">
              <UiBadge :tone="varianceTone(shift.variance)">
                {{ Number(shift.variance) > 0 ? '+' : '' }}{{ money(shift.variance) }}
              </UiBadge>
              <span v-if="shift.variance_reason" class="text-xs text-slate-500">
                {{ shift.variance_reason }}
              </span>
            </div>
            <span v-else class="text-slate-400">—</span>
          </td>
          <td class="px-4 py-3">
            <UiBadge :tone="shift.status === 'OPEN' ? 'info' : 'neutral'">
              {{ shift.status === 'OPEN' ? 'مفتوحة' : 'مغلقة' }}
            </UiBadge>
          </td>
        </tr>
      </UiTable>
    </UiCard>
  </div>
</template>
