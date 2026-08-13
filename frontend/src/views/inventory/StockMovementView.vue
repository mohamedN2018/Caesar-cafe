<script setup lang="ts">
/**
 * The stock ledger, exactly as the server records it.
 *
 * Read-only on purpose: movements are written by services, never by a form.
 * An adjustment made here would be a stock change with no explanation.
 */
import { onMounted, ref } from 'vue'

import { api } from '@/api/client'
import UiBadge from '@/components/ui/UiBadge.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import UiTable from '@/components/ui/UiTable.vue'
import { dateTime, money, quantity } from '@/lib/format'

interface Movement {
  id: string
  item_code: string
  item_name: string
  movement_type: string
  quantity_delta: string
  unit_cost: string
  balance_after: string
  value_delta: string
  user_name: string | null
  reason: string
  occurred_at: string
}

type Tone = 'success' | 'danger' | 'info' | 'warning' | 'neutral'

const TYPES: Record<string, { label: string; tone: Tone }> = {
  OPENING: { label: 'رصيد افتتاحي', tone: 'info' },
  PURCHASE: { label: 'شراء', tone: 'success' },
  SALE: { label: 'بيع', tone: 'neutral' },
  WASTE: { label: 'هالك', tone: 'danger' },
  ADJUSTMENT: { label: 'تسوية', tone: 'warning' },
  RETURN: { label: 'مرتجع', tone: 'warning' },
  COUNT: { label: 'جرد', tone: 'info' },
  TRANSFER: { label: 'تحويل', tone: 'neutral' },
}

const movements = ref<Movement[]>([])
const loading = ref(true)
const typeFilter = ref('')

const columns = [
  { key: 'when', label: 'التاريخ' },
  { key: 'item', label: 'الصنف' },
  { key: 'type', label: 'النوع' },
  { key: 'delta', label: 'الكمية', align: 'end' as const },
  { key: 'balance', label: 'الرصيد بعدها', align: 'end' as const },
  { key: 'value', label: 'القيمة', align: 'end' as const },
  { key: 'who', label: 'بواسطة' },
]

function toneFor(type: string): Tone {
  return TYPES[type]?.tone ?? 'neutral'
}

function labelFor(type: string): string {
  return TYPES[type]?.label ?? type
}

async function load() {
  loading.value = true
  try {
    movements.value = await api.get<Movement[]>(
      '/inventory/movements/',
      typeFilter.value ? { type: typeFilter.value } : undefined,
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
        <h1 class="text-2xl font-bold text-ink">حركة المخزون</h1>
        <p class="mt-1 text-sm text-ink-muted">
          الدفتر الكامل. كل تغيير في رصيد له سطر هنا يفسّره.
        </p>
      </div>
      <select
        v-model="typeFilter"
        class="min-h-[44px] rounded-lg border border-line-strong px-3 py-2.5 text-sm"
        @change="load"
      >
        <option value="">كل الأنواع</option>
        <option v-for="(meta, key) in TYPES" :key="key" :value="key">{{ meta.label }}</option>
      </select>
    </div>

    <UiSkeleton v-if="loading" :rows="10" />

    <UiCard v-else>
      <UiEmpty
        v-if="!movements.length"
        icon="history"
        title="لا توجد حركات"
        description="ستظهر هنا كل عمليات الشراء والبيع والهالك والتسويات."
      />
      <UiTable v-else :columns="columns">
        <tr v-for="movement in movements" :key="movement.id" class="hover:bg-surface-muted">
          <td class="whitespace-nowrap px-4 py-3 text-sm text-ink-muted">
            {{ dateTime(movement.occurred_at) }}
          </td>
          <td class="px-4 py-3">
            <p class="font-medium text-ink">{{ movement.item_name }}</p>
            <p v-if="movement.reason" class="text-xs text-ink-muted">{{ movement.reason }}</p>
          </td>
          <td class="px-4 py-3">
            <UiBadge :tone="toneFor(movement.movement_type)">
              {{ labelFor(movement.movement_type) }}
            </UiBadge>
          </td>
          <td
            class="px-4 py-3 text-end font-medium tabular-nums"
            :class="Number(movement.quantity_delta) < 0 ? 'text-danger' : 'text-success'"
          >
            <span v-if="Number(movement.quantity_delta) > 0">+</span
            >{{ quantity(movement.quantity_delta) }}
          </td>
          <td class="px-4 py-3 text-end tabular-nums text-ink-muted">
            {{ quantity(movement.balance_after) }}
          </td>
          <td class="px-4 py-3 text-end tabular-nums text-ink-muted">
            {{ money(movement.value_delta) }}
          </td>
          <td class="px-4 py-3 text-sm text-ink-muted">{{ movement.user_name ?? '—' }}</td>
        </tr>
      </UiTable>
    </UiCard>
  </div>
</template>
