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
  /**
   * Turn a payload that is NOT a list into rows.
   *
   * The P&L and the sales summary answer with one object of figures, not a table
   * — and they are the two reports an owner actually opens. Rather than build a
   * second rendering path for them, they are folded into the same rows the table
   * and the chart already understand: one row per line of the statement. The
   * table then reads like a statement, which is what it is.
   */
  derive?: (payload: Record<string, unknown>) => Record<string, string | number>[]
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
    /** A single measure. Omit when `series` is given. */
    value?: string
    /**
     * Two or more measures on ONE scale — revenue, cost and profit are all money.
     *
     * A measure on a different scale never joins them. Putting a percentage beside
     * a currency needs a second y-axis, and the crossing point of two lines on two
     * scales is an artefact of where the axes were set: it looks like a finding and
     * is not one.
     */
    series?: { key: string; label: string }[]
    kind: 'bar' | 'horizontal' | 'line' | 'share'
    title: string
    /** Cap the bars drawn. A chart of eighty products is a wall, not a chart. */
    top?: number
  }
}

/** A line of a statement. `money: true` on the value column formats them all. */
function line(label: string, value: unknown): Record<string, string | number> {
  return { line: label, value: String(value ?? '0') }
}

const TABS: Tab[] = [
  {
    /**
     * The one an owner opens first, and it was not on this screen at all —
     * `financial/pnl` has existed since Phase 8 with no way to reach it.
     *
     * **It stops at gross profit and says so in its own payload.** The system
     * knows what was sold and what it cost to make; it knows nothing about rent,
     * salaries or electricity. A figure that looked like net profit while omitting
     * the largest costs would be worse than useless, so the note travels with the
     * numbers rather than living in a footnote somebody scrolls past.
     */
    key: 'pnl',
    label: 'الأرباح والخسائر',
    path: 'financial/pnl',
    permission: 'reports.financial',
    section: '',
    derive: (payload) => [
      line('صافي المبيعات', payload.net_sales),
      line('تكلفة المبيعات', payload.cogs),
      line('الربح الإجمالي', payload.gross_profit),
      line('الهالك', payload.waste_value),
      line('المرتجعات', payload.refunds),
      line('الخصومات', payload.discounts),
      line('ضريبة محصَّلة', payload.tax_collected),
      line('خدمة محصَّلة', payload.service_collected),
    ],
    columns: [
      { key: 'line', label: 'البند' },
      { key: 'value', label: 'المبلغ', align: 'end', money: true },
    ],
    // No `sum` and no chart. These lines are not addends of one another —
    // totalling a column that mixes revenue, cost and tax produces a number with
    // no meaning, and bars would invite exactly that comparison.
    note:
      'ينتهي عند الربح الإجمالي. النظام يعرف ما بيع وما تكلّف، ولا يعرف الإيجار ولا ' +
      'الرواتب ولا الكهرباء — ورقم يبدو ربحاً صافياً وهو يُغفل أكبر التكاليف أسوأ من لا رقم.',
  },
  {
    /**
     * The headline figures. Also had no tab.
     *
     * A statement rather than a chart for the same reason as the P&L: these are
     * different measures, and putting a currency beside a count and a percentage
     * on one scale is the dual-axis mistake wearing a different hat.
     */
    key: 'summary',
    label: 'ملخص المبيعات',
    path: 'sales/summary',
    permission: 'reports.sales',
    section: '',
    derive: (payload) => [
      line('إجمالي المبيعات', payload.gross_sales),
      line('الخصومات', payload.discounts),
      line('الخدمة', payload.service),
      line('الضريبة', payload.tax),
      line('المرتجعات', payload.refunds),
      line('صافي المبيعات', payload.net_sales),
      line('مبيعات نقدية', payload.cash_sales),
      line('مبيعات غير نقدية', payload.non_cash_sales),
      line('تكلفة المبيعات', payload.cogs),
      line('الربح الإجمالي', payload.gross_profit),
      line('متوسط الفاتورة', payload.average_ticket),
    ],
    columns: [
      { key: 'line', label: 'البند' },
      { key: 'value', label: 'المبلغ', align: 'end', money: true },
    ],
  },
  {
    /** Trading shape through the day — the one report that is genuinely a line. */
    key: 'hourly',
    label: 'حسب الساعة',
    path: 'sales/by-hour',
    permission: 'reports.sales',
    section: 'hours',
    columns: [
      { key: 'hour', label: 'الساعة' },
      { key: 'order_count', label: 'الطلبات', align: 'end', sum: true },
      { key: 'net_sales', label: 'صافي المبيعات', align: 'end', money: true, sum: true },
    ],
    chart: {
      label: 'hour',
      value: 'net_sales',
      // A line, because the hours are ordered and the shape between them is the
      // point. Bars would say each hour is a separate category.
      kind: 'line',
      title: 'المبيعات على مدار اليوم',
    },
  },
  {
    key: 'top-products',
    label: 'الأكثر بيعاً',
    path: 'products/top',
    permission: 'reports.products',
    section: 'top',
    columns: [
      { key: 'name', label: 'الصنف' },
      { key: 'category', label: 'القسم' },
      { key: 'quantity', label: 'الكمية', align: 'end', sum: true },
      { key: 'revenue', label: 'الإيراد', align: 'end', money: true, sum: true },
      { key: 'profit', label: 'الربح', align: 'end', money: true, sum: true },
    ],
    chart: {
      label: 'name',
      value: 'revenue',
      // Horizontal: product names are long, and rotated labels are unreadable.
      kind: 'horizontal',
      title: 'أعلى الأصناف إيراداً',
      top: 12,
    },
  },
  {
    key: 'purchases',
    label: 'المشتريات',
    path: 'purchases/summary',
    permission: 'reports.inventory',
    section: 'by_supplier',
    columns: [
      { key: 'supplier', label: 'المورد' },
      { key: 'receipts', label: 'عدد الاستلامات', align: 'end', sum: true },
      { key: 'value', label: 'القيمة', align: 'end', money: true, sum: true },
    ],
    chart: {
      label: 'supplier',
      value: 'value',
      kind: 'horizontal',
      title: 'المشتريات حسب المورد',
      top: 10,
    },
  },
  {
    key: 'supplier-balances',
    label: 'أرصدة الموردين',
    path: 'suppliers/balances',
    permission: 'reports.inventory',
    section: 'suppliers',
    columns: [
      { key: 'name', label: 'المورد' },
      { key: 'phone', label: 'الهاتف' },
      { key: 'balance', label: 'الرصيد', align: 'end', money: true, sum: true },
    ],
    note: 'الرصيد مشتق من دفتر الحسابات، لا يُدخل يدوياً — أي فرق يظهر هو خطأ في مسار كتابة.',
  },
  {
    key: 'movements',
    label: 'حركة المخزون',
    path: 'inventory/movements',
    permission: 'reports.inventory',
    section: 'movements',
    columns: [
      { key: 'occurred_at', label: 'الوقت', time: true },
      { key: 'item', label: 'الصنف' },
      { key: 'type', label: 'النوع' },
      { key: 'quantity_delta', label: 'الحركة', align: 'end' },
      { key: 'balance_after', label: 'الرصيد بعدها', align: 'end' },
      { key: 'reason', label: 'السبب' },
      { key: 'user', label: 'بواسطة' },
    ],
    // No chart and no sum: this is a ledger read line by line, and the quantities
    // are per-item units. Adding 3kg of coffee to 40 cups is a number, not a fact.
    note: 'السجل هو الحقيقة، والأرصدة مشتقة منه. يُقرأ سطراً سطراً وليس كإجمالي.',
  },
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
      label: 'method',
      value: 'amount',
      // Share of ONE total across a handful of categories — the only question a
      // doughnut answers well, and the only place this product uses one. People
      // read angles badly, so it is never used to compare magnitudes.
      kind: 'share',
      title: 'حصة كل طريقة من المحصَّل',
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
      label: 'name',
      // Three measures on ONE scale, because all three are money. The margin
      // percentage in the table beside them deliberately stays OUT of the chart:
      // a percentage on a currency axis needs a second scale, and a second scale
      // is the mistake that makes two lines appear to cross meaningfully.
      series: [
        { key: 'revenue', label: 'الإيراد' },
        { key: 'cost', label: 'التكلفة' },
        { key: 'profit', label: 'الربح' },
      ],
      kind: 'horizontal',
      title: 'الإيراد والتكلفة والربح — أعلى ١٢ صنفاً',
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

/** A doughnut stops being readable well before this; see the fold below. */
const MAX_SLICES = 5

/**
 * The rows the chart draws, already ordered and capped.
 *
 * Sorted by magnitude — a bar chart in table order is a comparison the eye has to
 * do itself, whereas sorted, the ranking IS the chart. Capped because eighty
 * products is a wall of hairlines and the table underneath still has all of them.
 *
 * EXCEPT for a line chart, where the sequence is the point: re-ordering the hours
 * of a day by how much each took would destroy the only thing the shape is there
 * to show.
 */
const chartRows = computed(() => {
  const spec = active.value?.chart
  if (!spec || !rows.value.length) return null

  // With multiple series, the first one decides the ordering — otherwise each
  // series would want a different order and none of them would get it.
  const sortKey = spec.series?.[0]?.key ?? spec.value
  if (!sortKey) return null

  const points = rows.value
    .map((row) => ({
      label: String(row[spec.label] ?? '—'),
      value: Number(row[sortKey] ?? 0),
      row,
    }))
    .filter((point) => Number.isFinite(point.value))

  const ordered = spec.kind === 'line' ? points : [...points].sort((a, b) => b.value - a.value)

  /**
   * A share chart FOLDS its tail; it never truncates it.
   *
   * Dropping slices off a doughnut is the one cap that changes the meaning of what
   * is left: the arcs no longer add up to the whole, so every remaining share is
   * overstated while still looking like a share. Anything past the fifth becomes
   * "أخرى" and keeps its weight. A bar chart has no such problem — the bars are
   * read against the axis, not against each other's sum — so it truncates.
   */
  if (spec.kind === 'share' && ordered.length > MAX_SLICES) {
    const head = ordered.slice(0, MAX_SLICES - 1)
    const tail = ordered.slice(MAX_SLICES - 1)
    const rest = tail.reduce((sum, point) => sum + point.value, 0)
    return [...head, { label: `أخرى (${tail.length})`, value: rest, row: {} }]
  }

  const capped = ordered.slice(0, spec.top ?? 20)
  return capped.length ? capped : null
})

/** `undefined` for a single measure, so UiChart takes the `values` path. */
const chartSeries = computed(() => {
  const spec = active.value?.chart
  if (!spec?.series || !chartRows.value) return undefined
  return spec.series.map((s) => ({
    label: s.label,
    values: chartRows.value!.map((point) => Number(point.row[s.key] ?? 0)),
  }))
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
    rows.value = active.value.derive
      ? active.value.derive(payload)
      : ((payload[active.value.section] ?? []) as Record<string, string | number>[])
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
              :values="chartSeries ? undefined : chartRows.map((point) => point.value)"
              :series="chartSeries"
              :kind="active.chart.kind"
              :format="(value) => money(value)"
              :height="
                active.chart.kind === 'horizontal'
                  ? Math.max(220, chartRows.length * 30)
                  : active.chart.kind === 'share'
                    ? 280
                    : 260
              "
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
