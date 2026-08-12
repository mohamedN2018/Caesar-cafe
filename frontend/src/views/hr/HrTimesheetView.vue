<script setup lang="ts">
/**
 * The monthly timesheet — the screen a wage is calculated from.
 *
 * Every figure here is computed from the punches on the server, on every read.
 * Nothing is stored, so an amendment made a minute ago is already reflected and
 * there is no rebuild to remember. That is the same discipline `StockLevel`
 * follows against `StockMovement`, and it exists for the same reason: a stored
 * total is a number that can quietly disagree with the records it came from.
 *
 * **Absence is `scheduled AND NOT present`.** Somebody who was never rostered is
 * not absent, they were off. A report that counted every day off as an absence
 * would be a report an owner stops opening.
 *
 * **An open punch contributes no hours and is counted separately.** Adding the
 * time since check-in would grow somebody's wage every time the page reloaded,
 * so the column says how many rows are still open and leaves the hours out until
 * a human resolves them.
 */
import { computed, onMounted, ref } from 'vue'

import { ApiError, api } from '@/api/client'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiBadge from '@/components/ui/UiBadge.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiIcon from '@/components/ui/UiIcon.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import UiTable from '@/components/ui/UiTable.vue'

interface Row {
  user_id: string
  name_ar: string
  scheduled_days: number
  present_days: number
  absent_days: number
  late_days: number
  late_minutes: number
  worked_minutes: number
  overtime_minutes: number
  open_punches: number
}

const rows = ref<Row[]>([])
const loading = ref(true)
const error = ref('')

function pad(n: number): string {
  return String(n).padStart(2, '0')
}

/**
 * Built from local parts, never `toISOString()`.
 *
 * Egypt is UTC+3, so from midnight until 03:00 — the tail of every trading
 * night, which is exactly when a manager is closing up — `toISOString()` returns
 * yesterday and the range silently misses a day. The reports page had this bug.
 */
function localIso(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

const now = new Date()
const dateFrom = ref(localIso(new Date(now.getFullYear(), now.getMonth(), 1)))
const dateTo = ref(localIso(now))

const rangeInvalid = computed(() => Boolean(dateFrom.value && dateTo.value && dateFrom.value > dateTo.value))

const columns = [
  { key: 'name_ar', label: 'الموظف' },
  { key: 'scheduled_days', label: 'أيام مجدولة', align: 'end' as const },
  { key: 'present_days', label: 'أيام حضور', align: 'end' as const },
  { key: 'absent_days', label: 'غياب', align: 'end' as const },
  { key: 'late_days', label: 'مرات التأخير', align: 'end' as const },
  { key: 'late_minutes', label: 'دقائق التأخير', align: 'end' as const },
  { key: 'worked_minutes', label: 'الساعات', align: 'end' as const },
  { key: 'overtime_minutes', label: 'إضافي', align: 'end' as const },
]

/** Minutes as `H:MM`. A wage is argued in hours and minutes, not in 487. */
function hours(minutes: number): string {
  return `${Math.floor(minutes / 60)}:${pad(minutes % 60)}`
}

const totals = computed(() => ({
  scheduled_days: rows.value.reduce((s, r) => s + r.scheduled_days, 0),
  present_days: rows.value.reduce((s, r) => s + r.present_days, 0),
  absent_days: rows.value.reduce((s, r) => s + r.absent_days, 0),
  late_days: rows.value.reduce((s, r) => s + r.late_days, 0),
  late_minutes: rows.value.reduce((s, r) => s + r.late_minutes, 0),
  worked_minutes: rows.value.reduce((s, r) => s + r.worked_minutes, 0),
  overtime_minutes: rows.value.reduce((s, r) => s + r.overtime_minutes, 0),
}))

const stillOpen = computed(() => rows.value.reduce((s, r) => s + r.open_punches, 0))

async function load() {
  if (rangeInvalid.value) return
  loading.value = true
  try {
    rows.value = await api.get<Row[]>(
      `/hr/timesheet/?date_from=${dateFrom.value}&date_to=${dateTo.value}`,
    )
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تحميل الكشف.'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-bold text-ink">كشف الحضور</h1>
      <p class="mt-1 text-sm text-ink-muted">
        كل رقم محسوب من البصمات عند كل قراءة — التعديل ينعكس فوراً ولا يوجد ما يُعاد بناؤه.
      </p>
    </div>

    <UiCard>
      <div class="flex flex-wrap items-end gap-3">
        <label class="text-sm">
          <span class="block text-ink-muted">من</span>
          <input
            v-model="dateFrom"
            type="date"
            class="mt-1 block rounded-lg border border-line-strong px-3 py-2 text-sm"
          />
        </label>
        <label class="text-sm">
          <span class="block text-ink-muted">إلى</span>
          <input
            v-model="dateTo"
            type="date"
            class="mt-1 block rounded-lg border border-line-strong px-3 py-2 text-sm"
          />
        </label>
        <button
          type="button"
          class="rounded-lg bg-brand-700 px-4 py-2 text-sm font-medium text-gold-300 disabled:opacity-50"
          :disabled="rangeInvalid"
          @click="load"
        >
          تحديث
        </button>
      </div>
      <p v-if="rangeInvalid" class="mt-3 text-sm text-danger">تاريخ البداية بعد تاريخ النهاية.</p>
    </UiCard>

    <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>

    <!-- Stated rather than silently excluded: the hours are missing from the
         totals below, and somebody reading a wage off this needs to know why. -->
    <UiAlert v-if="stillOpen" tone="warning">
      {{ stillOpen }} تسجيل حضور ما زال مفتوحاً — ساعاته غير محسوبة هنا حتى يُغلق أو يُصحَّح.
    </UiAlert>

    <UiSkeleton v-if="loading" :rows="8" />

    <UiCard v-else>
      <UiEmpty
        v-if="!rows.length"
        icon="clipboard"
        title="لا توجد بيانات في هذه الفترة"
        description="لم يُسجَّل حضور ولم تُجدول ورديات خلال المدة المختارة."
      />

      <UiTable v-else :columns="columns">
        <tr v-for="row in rows" :key="row.user_id" class="hover:bg-surface-muted">
          <td class="px-4 py-3">
            <span class="font-medium text-ink">{{ row.name_ar }}</span>
            <UiBadge v-if="row.open_punches" tone="warning" class="ms-2">
              {{ row.open_punches }} مفتوح
            </UiBadge>
          </td>
          <td class="px-4 py-3 text-end tabular-nums">{{ row.scheduled_days }}</td>
          <td class="px-4 py-3 text-end tabular-nums">{{ row.present_days }}</td>
          <td class="px-4 py-3 text-end tabular-nums" :class="row.absent_days ? 'text-danger' : ''">
            {{ row.absent_days }}
          </td>
          <td class="px-4 py-3 text-end tabular-nums">{{ row.late_days }}</td>
          <td class="px-4 py-3 text-end tabular-nums" :class="row.late_minutes ? 'text-warning' : ''">
            {{ row.late_minutes }}
          </td>
          <td class="px-4 py-3 text-end tabular-nums">{{ hours(row.worked_minutes) }}</td>
          <td class="px-4 py-3 text-end tabular-nums">{{ hours(row.overtime_minutes) }}</td>
        </tr>

        <!--
          Totals inside the table, under their own columns, so they stay aligned
          when it scrolls sideways on a phone. Days and minutes add up; nothing
          here is an average, so every column is honestly summable.
        -->
        <tr class="border-t-2 border-line-strong bg-surface-muted font-semibold">
          <td class="px-4 py-3">الإجمالي</td>
          <td class="px-4 py-3 text-end tabular-nums">{{ totals.scheduled_days }}</td>
          <td class="px-4 py-3 text-end tabular-nums">{{ totals.present_days }}</td>
          <td class="px-4 py-3 text-end tabular-nums">{{ totals.absent_days }}</td>
          <td class="px-4 py-3 text-end tabular-nums">{{ totals.late_days }}</td>
          <td class="px-4 py-3 text-end tabular-nums">{{ totals.late_minutes }}</td>
          <td class="px-4 py-3 text-end tabular-nums">{{ hours(totals.worked_minutes) }}</td>
          <td class="px-4 py-3 text-end tabular-nums">{{ hours(totals.overtime_minutes) }}</td>
        </tr>
      </UiTable>

      <p class="mt-4 flex items-start gap-2 text-sm text-ink-muted">
        <UiIcon name="note" size="0.95rem" class="mt-0.5 flex-none" />
        <span>
          الغياب يُحسب فقط لمن كان مجدولاً ولم يحضر — يوم الراحة ليس غياباً.
        </span>
      </p>
    </UiCard>
  </div>
</template>
