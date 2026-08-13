<script setup lang="ts">
/**
 * The tariff builder's read side, with worked examples.
 *
 * The examples come from the SERVER — `/kids/tariffs/{id}/preview/` runs the
 * same `compute_charge` a real checkout runs. Calculating them here would be a
 * second pricing implementation, and a second implementation is exactly how the
 * number an admin sees while designing a rule drifts from the number a parent
 * is charged under it.
 */
import { onMounted, ref } from 'vue'

import { api } from '@/api/client'
import UiBadge from '@/components/ui/UiBadge.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import UiTable from '@/components/ui/UiTable.vue'
import { money } from '@/lib/format'

interface Tariff {
  id: string
  area: string
  name_ar: string
  mode: 'TIMED' | 'PACKAGE' | 'OPEN_DAY'
  entry_fee: string
  included_minutes: number
  package_minutes: number
  block_minutes: number
  block_rate: string
  grace_minutes: number | null
  daily_cap: string
  applies_days: number[]
  applies_from: string | null
  applies_to: string | null
  priority: number
  is_default: boolean
  is_active: boolean
}

interface Preview {
  minutes: number
  charge: string
  billable_minutes: number
  blocks: number
  capped: boolean
}

const DAY_NAMES = ['الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت', 'الأحد']

const MODE_LABELS: Record<Tariff['mode'], string> = {
  TIMED: 'عدّاد',
  PACKAGE: 'باقة',
  OPEN_DAY: 'يوم مفتوح',
}

const tariffs = ref<Tariff[]>([])
const previews = ref<Record<string, Preview[]>>({})
const expanded = ref<string>('')
const loading = ref(true)

const previewColumns = [
  { key: 'minutes', label: 'المدة' },
  { key: 'charge', label: 'المستحق', align: 'end' as const },
  { key: 'why', label: 'التفسير' },
]

/** A one-line statement of the rule, built from the fields — no arithmetic. */
function describe(tariff: Tariff): string {
  if (tariff.mode === 'OPEN_DAY') {
    return `${money(tariff.entry_fee)} لليوم كاملاً بدون حد للمدة.`
  }
  const covered = tariff.mode === 'PACKAGE' ? tariff.package_minutes : tariff.included_minutes
  const parts = [`${money(tariff.entry_fee)} تشمل ${covered} دقيقة`]
  if (tariff.block_minutes > 0) {
    parts.push(`ثم ${money(tariff.block_rate)} لكل ${tariff.block_minutes} دقيقة`)
  }
  if (tariff.grace_minutes !== null) parts.push(`سماح ${tariff.grace_minutes} دقيقة`)
  if (Number(tariff.daily_cap) > 0) parts.push(`بحد أقصى ${money(tariff.daily_cap)}`)
  return `${parts.join('، ')}.`
}

function window(tariff: Tariff): string {
  const days = tariff.applies_days?.length
    ? tariff.applies_days.map((d) => DAY_NAMES[d] ?? d).join('، ')
    : 'كل الأيام'
  const hours =
    tariff.applies_from && tariff.applies_to
      ? `${tariff.applies_from.slice(0, 5)} — ${tariff.applies_to.slice(0, 5)}`
      : 'طوال اليوم'
  return `${days} · ${hours}`
}

function explain(row: Preview, tariff: Tariff): string {
  if (row.capped) return 'وصل الحد الأقصى'
  if (tariff.mode === 'OPEN_DAY') return 'سعر ثابت'
  if (row.blocks === 0) return 'داخل الفترة المشمولة أو مهلة السماح'
  return `${row.blocks} فترة إضافية · محتسب ${row.billable_minutes} دقيقة`
}

async function toggle(tariff: Tariff) {
  expanded.value = expanded.value === tariff.id ? '' : tariff.id
  if (expanded.value && !previews.value[tariff.id]) {
    previews.value[tariff.id] = await api.get<Preview[]>(`/kids/tariffs/${tariff.id}/preview/`)
  }
}

onMounted(async () => {
  try {
    tariffs.value = await api.get<Tariff[]>('/kids/tariffs/')
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-bold text-ink">تعريفات صالة الأطفال</h1>
      <p class="mt-1 text-sm text-ink-muted">
        الأمثلة محسوبة على الخادم بنفس الكود الذي يحاسب به الكاشير.
      </p>
    </div>

    <UiSkeleton v-if="loading" :rows="5" />

    <UiEmpty
      v-else-if="!tariffs.length"
      icon="ticket"
      title="لا توجد تعريفات"
      description="أضف تعريفة واحدة على الأقل حتى يمكن تسجيل دخول طفل."
    />

    <div v-else class="grid gap-3">
      <UiCard v-for="tariff in tariffs" :key="tariff.id">
        <div class="flex flex-wrap items-start justify-between gap-4">
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <span class="text-lg font-bold text-ink">{{ tariff.name_ar }}</span>
              <UiBadge tone="info">{{ MODE_LABELS[tariff.mode] }}</UiBadge>
              <UiBadge v-if="tariff.is_default" tone="success">افتراضية</UiBadge>
              <UiBadge v-if="!tariff.is_active" tone="neutral">موقوفة</UiBadge>
            </div>
            <p class="mt-1 text-sm text-ink-muted">{{ describe(tariff) }}</p>
            <p class="mt-0.5 text-xs text-ink-faint">
              {{ window(tariff) }} · أولوية {{ tariff.priority }}
            </p>
          </div>

          <button
            class="rounded-lg px-3 py-2 text-sm font-medium text-brand-800 ring-1 ring-inset ring-brand-200 hover:bg-brand-50"
            @click="toggle(tariff)"
          >
            {{ expanded === tariff.id ? 'إخفاء الأمثلة' : 'أمثلة محسوبة' }}
          </button>
        </div>

        <div v-if="expanded === tariff.id" class="mt-4 border-t border-line pt-4">
          <UiSkeleton v-if="!previews[tariff.id]" :rows="4" />
          <UiTable v-else :columns="previewColumns">
            <tr v-for="row in previews[tariff.id]" :key="row.minutes">
              <td class="px-4 py-2 tabular-nums text-ink">{{ row.minutes }} دقيقة</td>
              <td class="px-4 py-2 text-end font-medium tabular-nums text-ink">
                {{ money(row.charge) }}
              </td>
              <td class="px-4 py-2 text-sm text-ink-muted">{{ explain(row, tariff) }}</td>
            </tr>
          </UiTable>
        </div>
      </UiCard>
    </div>
  </div>
</template>
