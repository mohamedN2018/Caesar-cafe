<script setup lang="ts">
/**
 * The reports page.
 *
 * One date range across every tab, because an owner comparing two reports
 * almost always wants them on the same window, and making them set it twice is
 * how the two get compared over different periods by mistake.
 *
 * The tab list is filtered by permission: a manager without `reports.financial`
 * does not see a locked "الأرباح" tab, they see no such tab. Hiding what someone
 * cannot use keeps the interface honest — and the server re-checks regardless.
 */
import { computed, onMounted, ref, watch } from 'vue'

import { ApiError, api } from '@/api/client'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiChart from '@/components/ui/UiChart.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiIcon from '@/components/ui/UiIcon.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import UiTable from '@/components/ui/UiTable.vue'
import { useAuthStore } from '@/stores/auth'
import { dateTime, money } from '@/lib/format'

interface Column {
  key: string
  label: string
  align?: 'start' | 'end'
  /** Render as money. Values arrive as strings and stay strings until here. */
  money?: boolean
  time?: boolean
  /**
   * Include this column in the totals row.
   *
   * Opt-in, never inferred from the type. Money and counts add up; a percentage
   * does not — the sum of eight margin percentages is a number with no meaning,
   * and printing it under a column of real ones is worse than printing nothing,
   * because it looks like an answer.
   */
  sum?: boolean
}

interface Tab {
  key: string
  label: string
  path: string
  permission: string
  /** Which array inside the payload holds the rows. */
  section: string
  columns: Column[]
  note?: string
  /**
   * What to plot above the table, if anything.
   *
   * Not every report earns a chart. A waste log is a list of incidents to read
   * line by line; drawing it as bars invites a comparison nobody is making. So
   * this is opt-in per tab rather than a chart on everything.
   */
  chart?: {
    label: string
    value: string
    kind: 'bar' | 'horizontal' | 'line'
    title: string
    /** Cap the bars drawn. A chart of eighty products is a wall, not a chart. */
    top?: number
  }
}

const TABS: Tab[] = [
  {
    key: 'category',
    label: 'حسب القسم',
    path: 'sales/by-category',
    permission: 'reports.sales',
    section: 'categories',
    columns: [
      { key: 'category', label: 'القسم' },
      { key: 'quantity', label: 'الكمية', align: 'end', sum: true },
      { key: 'revenue', label: 'الإيراد', align: 'end', money: true, sum: true },
      { key: 'profit', label: 'الربح', align: 'end', money: true, sum: true },
      { key: 'share_percent', label: 'النسبة %', align: 'end' },
    ],
    chart: {
      label: "category",
      value: "revenue",
      kind: "horizontal",
      title: "الإيراد حسب القسم",
    },
  },
  {
    key: 'methods',
    label: 'طرق الدفع',
    path: 'sales/by-payment-method',
    permission: 'reports.sales',
    section: 'methods',
    columns: [
      { key: 'method', label: 'الطريقة' },
      { key: 'count', label: 'العدد', align: 'end', sum: true },
      { key: 'amount', label: 'المبلغ', align: 'end', money: true, sum: true },
    ],
    chart: {
      label: "method",
      value: "amount",
      kind: "horizontal",
      title: "المحصَّل بكل طريقة",
    },
    note: 'مطابقة النقدي مع البطاقات — الفرق بينهما هو ما يجب أن يكون في الدرج.',
  },
  {
    key: 'products',
    label: 'ربحية الأصناف',
    path: 'products/profitability',
    permission: 'reports.products',
    section: 'products',
    columns: [
      { key: 'name', label: 'الصنف' },
      { key: 'category', label: 'القسم' },
      { key: 'quantity', label: 'الكمية', align: 'end', sum: true },
      { key: 'revenue', label: 'الإيراد', align: 'end', money: true, sum: true },
      { key: 'cost', label: 'التكلفة', align: 'end', money: true, sum: true },
      { key: 'profit', label: 'الربح', align: 'end', money: true, sum: true },
      { key: 'margin_percent', label: 'الهامش %', align: 'end' },
    ],
    chart: {
      label: "name",
      value: "profit",
      kind: "horizontal",
      title: "أعلى ١٢ صنفاً ربحاً",
      top: 12,
    },
    note: 'الصنف الأعلى إيراداً ليس دائماً الأجدر بالترويج — الهامش هو ما يفرق.',
  },
  {
    key: 'waste',
    label: 'الهالك',
    path: 'inventory/waste',
    permission: 'reports.inventory',
    section: 'items',
    columns: [
      { key: 'item', label: 'الصنف' },
      // No `sum` on the quantity: these rows are raw stock items, each in its
      // own base unit. Adding 3 kg of coffee to 40 cups is a number, not a
      // fact. The value column is the honest total, because money is one unit.
      { key: 'quantity', label: 'الكمية', align: 'end' },
      { key: 'value', label: 'القيمة', align: 'end', money: true, sum: true },
      { key: 'events', label: 'عدد المرات', align: 'end', sum: true },
    ],
  },
  {
    key: 'variance',
    label: 'فروق الجرد',
    path: 'inventory/variance',
    permission: 'reports.inventory',
    section: 'items',
    columns: [
      { key: 'item', label: 'الصنف' },
      { key: 'count_reference', label: 'الجرد' },
      { key: 'system_quantity', label: 'رصيد النظام', align: 'end' },
      { key: 'counted_quantity', label: 'المعدود', align: 'end' },
      // Same as the waste tab: quantities here are per-item units, value is not.
      { key: 'variance', label: 'الفرق', align: 'end' },
      { key: 'value', label: 'القيمة', align: 'end', money: true, sum: true },
    ],
    note: 'الفرق ملاحظة وليس اتهاماً — أغلب الأسباب وصفة غير مضبوطة أو خطأ في العد.',
  },
  {
    key: 'employees',
    label: 'مبيعات الموظفين',
    path: 'employees/sales',
    permission: 'reports.employees',
    section: 'employees',
    columns: [
      { key: 'name', label: 'الموظف' },
      { key: 'order_count', label: 'الطلبات', align: 'end', sum: true },
      { key: 'net_sales', label: 'صافي المبيعات', align: 'end', money: true, sum: true },
      // An average is not additive — the branch average is the total over the
      // total, not the sum of the per-cashier averages.
      { key: 'average_ticket', label: 'متوسط الفاتورة', align: 'end', money: true },
    ],
  },
  {
    key: 'voids',
    label: 'الإلغاءات والخصومات',
    path: 'employees/voids',
    permission: 'reports.employees',
    section: 'employees',
    columns: [
      { key: 'name', label: 'الموظف' },
      { key: 'order_count', label: 'الطلبات', align: 'end', sum: true },
      { key: 'voided_orders', label: 'طلبات ملغاة', align: 'end', sum: true },
      { key: 'voided_items', label: 'أصناف ملغاة', align: 'end', sum: true },
      { key: 'void_rate_percent', label: 'نسبة الإلغاء %', align: 'end' },
      { key: 'discount_rate_percent', label: 'نسبة الخصم %', align: 'end' },
    ],
    note: 'المعدل وليس العدد — الكاشير الأكثر عملاً سيلغي أكثر بطبيعة الحال.',
  },
  {
    key: 'shifts',
    label: 'فروق النقدية',
    path: 'shifts/variance',
    permission: 'reports.financial',
    section: 'closes',
    columns: [
      { key: 'closed_at', label: 'وقت الإغلاق', time: true },
      { key: 'user', label: 'الموظف' },
      // The one figure the owner is here for: whether the shortfalls and the
      // overs cancel out across the period, or the drawer is quietly down.
      { key: 'variance', label: 'الفرق', align: 'end', money: true, sum: true },
      { key: 'reason', label: 'السبب' },
    ],
    note: 'ليلة واحدة خطأ؛ اتجاه ثابت نمط.',
  },
]

const auth = useAuthStore()

const visibleTabs = computed(() => TABS.filter((tab) => auth.can(tab.permission)))
const active = ref<Tab | null>(null)

const dateFrom = ref('')
const dateTo = ref('')
const rows = ref<Record<string, string | number>[]>([])
const loading = ref(true)
const error = ref('')

/** A range that runs backwards returns nothing — say so before asking for it. */
const rangeInvalid = computed(() => Boolean(dateFrom.value && dateTo.value && dateFrom.value > dateTo.value))

/**
 * A calendar date, `days` ago, in the *cafe's* timezone.
 *
 * Built from the local parts rather than `toISOString()`, which converts to UTC
 * first. Egypt is UTC+3, so from midnight until 03:00 — the tail of every
 * trading night, which is exactly when a manager is closing up and pulling a
 * report — `toISOString()` returns *yesterday*, and the range silently misses
 * the day being asked about.
 */
function isoDaysAgo(days: number): string {
  const day = new Date()
  day.setDate(day.getDate() - days)
  const month = String(day.getMonth() + 1).padStart(2, '0')
  return `${day.getFullYear()}-${month}-${String(day.getDate()).padStart(2, '0')}`
}

function cell(row: Record<string, string | number>, column: Column): string {
  const value = row[column.key]
  if (value === null || value === undefined || value === '') return '—'
  if (column.money) return money(String(value))
  if (column.time) return dateTime(String(value))
  return String(value)
}

function isNegative(row: Record<string, string | number>, column: Column): boolean {
  return Boolean(column.money) && Number(row[column.key]) < 0
}

/**
 * The rows the chart draws, biggest first and capped.
 *
 * Sorted because a bar chart in table order is a comparison the eye has to do
 * itself; sorted, the ranking IS the chart. Capped because eighty products is
 * a wall of hairlines, and the table underneath still has all of them.
 */
const chartRows = computed(() => {
  const spec = active.value?.chart
  if (!spec || !rows.value.length) return null

  const points = rows.value
    .map((row) => ({
      label: String(row[spec.label] ?? '—'),
      value: Number(row[spec.value] ?? 0),
    }))
    .filter((point) => Number.isFinite(point.value))
    .sort((a, b) => b.value - a.value)
    .slice(0, spec.top ?? 20)

  return points.length ? points : null
})

/**
 * The totals row.
 *
 * Computed from the rows on screen rather than read off the payload, because
 * the two would otherwise be able to disagree — and a total that disagrees with
 * the column above it is the one number in a report nobody can act on. Only the
 * columns marked `sum` appear; the rest are left blank rather than zeroed, so a
 * gap reads as "this does not add up", not as "this is zero".
 */
const totals = computed<Record<string, number> | null>(() => {
  const columns = active.value?.columns.filter((column) => column.sum) ?? []
  if (!columns.length || rows.value.length < 2) return null

  const result: Record<string, number> = {}
  for (const column of columns) {
    result[column.key] = rows.value.reduce((sum, row) => sum + (Number(row[column.key]) || 0), 0)
  }
  return result
})

function totalCell(column: Column): string {
  const value = totals.value?.[column.key]
  if (value === undefined) return ''
  return column.money ? money(value) : String(Number(value.toFixed(3)))
}

async function load() {
  if (!active.value || rangeInvalid.value) return
  loading.value = true
  try {
    const payload = await api.get<Record<string, unknown>>(`/reports/${active.value.path}/`, {
      date_from: dateFrom.value,
      date_to: dateTo.value,
    })
    rows.value = (payload[active.value.section] ?? []) as Record<string, string | number>[]
    error.value = ''
  } catch (exc) {
    rows.value = []
    error.value = exc instanceof ApiError ? exc.message : 'تعذّر تحميل التقرير.'
  } finally {
    loading.value = false
  }
}

async function download() {
  if (!active.value) return
  // Fetched through the client so the Authorization header is attached. A plain
  // <a href> would be an unauthenticated request, and the user would "download"
  // a 401.
  const response = await api.raw.get(`/reports/${active.value.path}/`, {
    params: { date_from: dateFrom.value, date_to: dateTo.value, export: 'csv' },
    responseType: 'blob',
  })

  const url = URL.createObjectURL(response.data as Blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${active.value.key}-${dateFrom.value}-${dateTo.value}.csv`
  link.click()
  URL.revokeObjectURL(url)
}

watch(active, load)

onMounted(async () => {
  dateFrom.value = isoDaysAgo(29)
  dateTo.value = isoDaysAgo(0)
  active.value = visibleTabs.value[0] ?? null
  if (!active.value) loading.value = false
})
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-bold text-ink">التقارير</h1>
      <p class="mt-1 text-sm text-ink-muted">
        التواريخ أيام عمل — تبدأ من ساعة بداية اليوم المضبوطة في الإعدادات، لا من منتصف الليل.
      </p>
    </div>

    <UiEmpty
      v-if="!visibleTabs.length"
      icon="shield"
      title="لا توجد تقارير متاحة"
      description="لا تملك صلاحية أي تقرير في هذا الفرع."
    />

    <template v-else>
      <UiCard>
        <div class="flex flex-wrap items-end gap-3">
          <label class="text-sm text-ink">
            من
            <input
              v-model="dateFrom"
              type="date"
              class="mt-1 block rounded-lg border border-line-strong px-3 py-2 text-sm"
            />
          </label>
          <label class="text-sm text-ink">
            إلى
            <input
              v-model="dateTo"
              type="date"
              class="mt-1 block rounded-lg border border-line-strong px-3 py-2 text-sm"
            />
          </label>
          <UiButton :disabled="rangeInvalid" @click="load">تحديث</UiButton>
          <UiButton variant="secondary" :disabled="rangeInvalid" @click="download">
            تنزيل CSV
          </UiButton>
        </div>
        <p v-if="rangeInvalid" class="mt-3 text-sm text-danger">
          تاريخ البداية بعد تاريخ النهاية.
        </p>
      </UiCard>

      <div class="flex flex-wrap gap-2">
        <button
          v-for="tab in visibleTabs"
          :key="tab.key"
          class="rounded-lg px-3 py-2 text-sm font-medium ring-1 ring-inset transition"
          :class="
            active?.key === tab.key
              ? 'bg-brand-50 text-brand-800 ring-brand-200'
              : 'bg-surface text-ink-muted ring-line hover:bg-surface-muted hover:text-ink'
          "
          @click="active = tab"
        >
          {{ tab.label }}
        </button>
      </div>

      <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>

      <UiCard v-if="active">
        <p v-if="active.note" class="mb-3 flex items-start gap-2 text-sm text-ink-muted">
          <UiIcon name="note" size="0.95rem" class="mt-0.5 flex-none" />
          <span>{{ active.note }}</span>
        </p>

        <UiSkeleton v-if="loading" :rows="6" />
        <UiEmpty
          v-else-if="!rows.length"
          icon="receipt"
          title="لا توجد بيانات"
          description="لا توجد حركة في هذه الفترة."
        />

        <template v-else>
          <!--
            The chart sits ABOVE the table and the table keeps every row. The
            chart answers "which is biggest" at a glance; the table answers
            "what exactly" — and neither does the other's job well, so this is
            not a choice between them.
          -->
          <section v-if="active.chart && chartRows" class="mb-6">
            <h3 class="mb-2 text-sm font-semibold text-ink">{{ active.chart.title }}</h3>
            <UiChart
              :labels="chartRows.map((point) => point.label)"
              :values="chartRows.map((point) => point.value)"
              :kind="active.chart.kind"
              :format="(value) => money(value)"
              :height="Math.max(220, chartRows.length * 30)"
            />
          </section>

          <UiTable :columns="active.columns">
          <tr v-for="(row, index) in rows" :key="index" class="hover:bg-surface-muted">
            <td
              v-for="column in active.columns"
              :key="column.key"
              class="px-4 py-3"
              :class="[
                column.align === 'end' ? 'text-end tabular-nums' : '',
                isNegative(row, column) ? 'font-medium text-danger' : '',
              ]"
            >
              {{ cell(row, column) }}
            </td>
          </tr>
          <!--
            The totals row is inside the table, not a card beneath it, so it
            stays under its own columns when the table scrolls sideways on a
            phone. A total that has drifted away from its column is a total
            being read against the wrong heading.
          -->
          <tr v-if="totals" class="border-t-2 border-line-strong bg-surface-muted font-semibold">
            <td
              v-for="(column, index) in active.columns"
              :key="column.key"
              class="px-4 py-3"
              :class="[
                column.align === 'end' ? 'text-end tabular-nums' : '',
                totals[column.key] !== undefined && totals[column.key] < 0 ? 'text-danger' : '',
              ]"
            >
              <template v-if="index === 0">الإجمالي</template>
              <template v-else>{{ totalCell(column) }}</template>
            </td>
          </tr>
          </UiTable>
        </template>
      </UiCard>
    </template>
  </div>
</template>
