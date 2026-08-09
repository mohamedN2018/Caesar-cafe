<script setup lang="ts">
/**
 * Session history and the occupancy report.
 *
 * Occupancy-by-hour is the operationally useful chart: it tells the owner when
 * to staff the area and whether weekend peak pricing is justified. It is drawn
 * with plain divs rather than a charting library — the whole page must work
 * inside a strict CSP and over a slow connection from a phone.
 */
import { computed, onMounted, ref } from 'vue'

import { api } from '@/api/client'
import UiBadge from '@/components/ui/UiBadge.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import UiTable from '@/components/ui/UiTable.vue'
import { dateTime, money } from '@/lib/format'

interface Session {
  id: string
  tag_number: string
  status: string
  child_name: string
  guardian_name: string
  guardian_phone: string
  tariff_name: string
  checked_in_at: string
  checked_out_at: string | null
  billable_minutes: number
  computed_charge: string
  override_charge: string | null
  payable: string
  order_id: string | null
}

interface Report {
  days: number
  sessions: number
  revenue: string
  average_minutes: number
  overridden: number
  by_hour: Record<string, { sessions: number; revenue: string }>
  by_tariff: Record<string, { sessions: number; revenue: string }>
}

const sessions = ref<Session[]>([])
const report = ref<Report | null>(null)
const loading = ref(true)
const statusFilter = ref('')

const columns = [
  { key: 'tag', label: 'التاج' },
  { key: 'child', label: 'الطفل' },
  { key: 'tariff', label: 'التعريفة' },
  { key: 'in', label: 'الدخول' },
  { key: 'out', label: 'الخروج' },
  { key: 'minutes', label: 'الدقائق', align: 'end' as const },
  { key: 'charge', label: 'المستحق', align: 'end' as const },
]

const shown = computed(() =>
  statusFilter.value ? sessions.value.filter((s) => s.status === statusFilter.value) : sessions.value,
)

const hourly = computed(() => {
  const buckets = report.value?.by_hour ?? {}
  const peak = Math.max(1, ...Object.values(buckets).map((b) => b.sessions))
  return Object.entries(buckets).map(([hour, bucket]) => ({
    hour,
    ...bucket,
    height: Math.round((bucket.sessions / peak) * 100),
  }))
})

const statusTone: Record<string, 'success' | 'warning' | 'danger' | 'neutral'> = {
  ACTIVE: 'success',
  OVERDUE: 'warning',
  CHECKED_OUT: 'neutral',
  CANCELLED: 'danger',
}

const statusLabel: Record<string, string> = {
  ACTIVE: 'بالداخل',
  OVERDUE: 'متجاوز',
  CHECKED_OUT: 'خرج',
  CANCELLED: 'ملغاة',
}

onMounted(async () => {
  try {
    ;[sessions.value, report.value] = await Promise.all([
      api.get<Session[]>('/kids/sessions/'),
      api.get<Report>('/kids/reports/?days=30'),
    ])
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 class="text-2xl font-bold text-slate-900">جلسات صالة الأطفال</h1>
        <p class="mt-1 text-sm text-slate-500">
          السجل الكامل — القيمة المحسوبة محفوظة دائماً بجوار أي تعديل يدوي.
        </p>
      </div>
      <select
        v-model="statusFilter"
        class="rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-700"
      >
        <option value="">كل الحالات</option>
        <option value="ACTIVE">بالداخل</option>
        <option value="OVERDUE">متجاوز الوقت</option>
        <option value="CHECKED_OUT">خرج</option>
      </select>
    </div>

    <UiSkeleton v-if="loading" :rows="8" />

    <template v-else>
      <div v-if="report" class="grid gap-4 sm:grid-cols-4">
        <UiCard>
          <p class="text-sm text-slate-500">جلسات ({{ report.days }} يوم)</p>
          <p class="mt-1 text-2xl font-bold text-slate-900">{{ report.sessions }}</p>
        </UiCard>
        <UiCard>
          <p class="text-sm text-slate-500">الإيراد</p>
          <p class="mt-1 text-2xl font-bold text-slate-900">{{ money(report.revenue) }}</p>
        </UiCard>
        <UiCard>
          <p class="text-sm text-slate-500">متوسط المدة</p>
          <p class="mt-1 text-2xl font-bold text-slate-900">{{ report.average_minutes }} د</p>
        </UiCard>
        <UiCard>
          <p class="text-sm text-slate-500">جلسات معدّلة يدوياً</p>
          <p
            class="mt-1 text-2xl font-bold"
            :class="report.overridden ? 'text-amber-700' : 'text-slate-900'"
          >
            {{ report.overridden }}
          </p>
        </UiCard>
      </div>

      <UiCard v-if="hourly.length">
        <h2 class="text-sm font-semibold text-slate-700">الإشغال حسب الساعة</h2>
        <p class="mt-1 text-xs text-slate-400">
          متى تحتاج الصالة موظفاً إضافياً، وهل تسعير الذروة له ما يبرره.
        </p>
        <div class="mt-4 flex items-end gap-1 overflow-x-auto" dir="ltr">
          <div v-for="bucket in hourly" :key="bucket.hour" class="flex min-w-8 flex-1 flex-col items-center gap-1">
            <span class="text-xs tabular-nums text-slate-500">{{ bucket.sessions }}</span>
            <div
              class="w-full rounded-t bg-brand-500"
              :style="{ height: `${Math.max(4, bucket.height)}px`, minHeight: '4px' }"
              :title="`${bucket.hour}:00 — ${bucket.sessions} جلسة`"
            />
            <span class="text-xs tabular-nums text-slate-400">{{ bucket.hour }}</span>
          </div>
        </div>
      </UiCard>

      <UiCard>
        <UiEmpty
          v-if="!shown.length"
          icon="kids"
          title="لا توجد جلسات"
          description="ستظهر هنا فور تسجيل أول دخول."
        />
        <UiTable v-else :columns="columns">
          <tr v-for="session in shown" :key="session.id" class="hover:bg-slate-50">
            <td class="px-4 py-3">
              <span class="font-mono font-medium text-slate-900" dir="ltr">
                #{{ session.tag_number }}
              </span>
            </td>
            <td class="px-4 py-3">
              <div class="flex items-center gap-2">
                <span class="font-medium text-slate-900">{{ session.child_name }}</span>
                <UiBadge :tone="statusTone[session.status] ?? 'neutral'">
                  {{ statusLabel[session.status] ?? session.status }}
                </UiBadge>
              </div>
              <p class="text-xs text-slate-400">{{ session.guardian_name }}</p>
            </td>
            <td class="px-4 py-3 text-slate-600">{{ session.tariff_name }}</td>
            <td class="px-4 py-3 text-sm text-slate-500">{{ dateTime(session.checked_in_at) }}</td>
            <td class="px-4 py-3 text-sm text-slate-500">{{ dateTime(session.checked_out_at) }}</td>
            <td class="px-4 py-3 text-end tabular-nums text-slate-600">
              {{ session.billable_minutes || '—' }}
            </td>
            <td class="px-4 py-3 text-end tabular-nums">
              <span class="font-medium text-slate-900">{{ money(session.payable) }}</span>
              <p v-if="session.override_charge" class="text-xs text-amber-700">
                محسوبة {{ money(session.computed_charge) }}
              </p>
            </td>
          </tr>
        </UiTable>
      </UiCard>
    </template>
  </div>
</template>
