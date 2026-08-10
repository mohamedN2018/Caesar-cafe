<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { api } from '@/api/client'
import UiBadge from '@/components/ui/UiBadge.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import UiTable from '@/components/ui/UiTable.vue'
import { dateTime, money, quantity } from '@/lib/format'

interface Level {
  item: string
  item_code: string
  item_name: string
  unit_code: string
  quantity_on_hand: string
  quantity_reserved: string
  quantity_available: string
  weighted_avg_cost: string
  total_value: string
  is_low: boolean
  last_movement_at: string | null
}

const levels = ref<Level[]>([])
const loading = ref(true)
const lowOnly = ref(false)

const columns = [
  { key: 'item', label: 'الصنف' },
  { key: 'on_hand', label: 'الرصيد', align: 'end' as const },
  { key: 'reserved', label: 'محجوز', align: 'end' as const },
  { key: 'cost', label: 'متوسط التكلفة', align: 'end' as const },
  { key: 'value', label: 'القيمة', align: 'end' as const },
  { key: 'last', label: 'آخر حركة' },
]

const lowCount = computed(() => levels.value.filter((level) => level.is_low).length)
const shown = computed(() =>
  lowOnly.value ? levels.value.filter((level) => level.is_low) : levels.value,
)
const totalValue = computed(() =>
  levels.value.reduce((sum, level) => sum + Number(level.total_value), 0),
)

onMounted(async () => {
  try {
    levels.value = await api.get<Level[]>('/inventory/levels/')
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 class="text-2xl font-bold text-ink">أرصدة المخزون</h1>
        <p class="mt-1 text-sm text-ink-muted">
          الأرصدة مشتقة من دفتر الحركات — كل تغيير له حركة تفسّره.
        </p>
      </div>
      <label class="flex items-center gap-2 text-sm text-ink">
        <input v-model="lowOnly" type="checkbox" class="h-5 w-5 rounded border-line-strong" />
        النواقص فقط
      </label>
    </div>

    <UiSkeleton v-if="loading" :rows="8" />

    <template v-else>
      <div class="grid gap-4 sm:grid-cols-3">
        <UiCard>
          <p class="text-sm text-ink-muted">إجمالي قيمة المخزون</p>
          <p class="mt-1 text-2xl font-bold text-ink">{{ money(totalValue) }}</p>
        </UiCard>
        <UiCard>
          <p class="text-sm text-ink-muted">عدد الأصناف</p>
          <p class="mt-1 text-2xl font-bold text-ink">{{ levels.length }}</p>
        </UiCard>
        <UiCard>
          <p class="text-sm text-ink-muted">أصناف تحت الحد الأدنى</p>
          <p
            class="mt-1 text-2xl font-bold"
            :class="lowCount ? 'text-warning' : 'text-ink'"
          >
            {{ lowCount }}
          </p>
        </UiCard>
      </div>

      <UiCard>
        <UiEmpty
          v-if="!shown.length"
          icon="box"
          :title="lowOnly ? 'لا توجد نواقص' : 'لا توجد أصناف'"
          :description="
            lowOnly
              ? 'كل الأصناف فوق الحد الأدنى.'
              : 'أضف أصناف المخزون ثم سجّل رصيداً افتتاحياً.'
          "
        />
        <UiTable v-else :columns="columns">
          <tr v-for="level in shown" :key="level.item" class="hover:bg-surface-muted">
            <td class="px-4 py-3">
              <div class="flex items-center gap-2">
                <span class="font-medium text-ink">{{ level.item_name }}</span>
                <UiBadge v-if="level.is_low" tone="warning">تحت الحد</UiBadge>
              </div>
              <p class="font-mono text-xs text-ink-faint" dir="ltr">{{ level.item_code }}</p>
            </td>
            <td class="px-4 py-3 text-end font-medium tabular-nums">
              {{ quantity(level.quantity_on_hand, level.unit_code) }}
            </td>
            <td class="px-4 py-3 text-end tabular-nums text-ink-muted">
              {{ Number(level.quantity_reserved) ? quantity(level.quantity_reserved) : '—' }}
            </td>
            <td class="px-4 py-3 text-end tabular-nums text-ink-muted">
              {{ Number(level.weighted_avg_cost) ? money(level.weighted_avg_cost) : '—' }}
            </td>
            <td class="px-4 py-3 text-end tabular-nums">{{ money(level.total_value) }}</td>
            <td class="px-4 py-3 text-sm text-ink-muted">{{ dateTime(level.last_movement_at) }}</td>
          </tr>
        </UiTable>
      </UiCard>
    </template>
  </div>
</template>
