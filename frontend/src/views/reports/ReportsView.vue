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
import UiEmpty from '@/components/ui/UiEmpty.vue'
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
      { key: 'quantity', label: 'الكمية', align: 'end' },
      { key: 'revenue', label: 'الإيراد', align: 'end', money: true },
      { key: 'profit', label: 'الربح', align: 'end', money: true },
      { key: 'share_percent', label: 'النسبة %', align: 'end' },
    ],
  },
  {
    key: 'methods',
    label: 'طرق الدفع',
    path: 'sales/by-payment-method',
    permission: 'reports.sales',
    section: 'methods',
    columns: [
      { key: 'method', label: 'الطريقة' },
      { key: 'count', label: 'العدد', align: 'end' },
      { key: 'amount', label: 'المبلغ', align: 'end', money: true },
    ],
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
      { key: 'quantity', label: 'الكمية', align: 'end' },
      { key: 'revenue', label: 'الإيراد', align: 'end', money: true },
      { key: 'cost', label: 'التكلفة', align: 'end', money: true },
      { key: 'profit', label: 'الربح', align: 'end', money: true },
      { key: 'margin_percent', label: 'الهامش %', align: 'end' },
    ],
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
      { key: 'quantity', label: 'الكمية', align: 'end' },
      { key: 'value', label: 'القيمة', align: 'end', money: true },
      { key: 'events', label: 'عدد المرات', align: 'end' },
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
      { key: 'variance', label: 'الفرق', align: 'end' },
      { key: 'value', label: 'القيمة', align: 'end', money: true },
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
      { key: 'order_count', label: 'الطلبات', align: 'end' },
      { key: 'net_sales', label: 'صافي المبيعات', align: 'end', money: true },
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
      { key: 'order_count', label: 'الطلبات', align: 'end' },
      { key: 'voided_orders', label: 'طلبات ملغاة', align: 'end' },
      { key: 'voided_items', label: 'أصناف ملغاة', align: 'end' },
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
      { key: 'variance', label: 'الفرق', align: 'end', money: true },
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
const summary = ref<Record<string, unknown> | null>(null)
const loading = ref(true)
const error = ref('')

function isoDaysAgo(days: number): string {
  const day = new Date()
  day.setDate(day.getDate() - days)
  return day.toISOString().slice(0, 10)
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

async function load() {
  if (!active.value) return
  loading.value = true
  try {
    const payload = await api.get<Record<string, unknown>>(`/reports/${active.value.path}/`, {
      date_from: dateFrom.value,
      date_to: dateTo.value,
    })
    rows.value = (payload[active.value.section] ?? []) as Record<string, string | number>[]
    summary.value = payload
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
          <UiButton @click="load">تحديث</UiButton>
          <UiButton variant="secondary" @click="download">تنزيل CSV</UiButton>
        </div>
      </UiCard>

      <div class="flex flex-wrap gap-2">
        <button
          v-for="tab in visibleTabs"
          :key="tab.key"
          class="rounded-lg px-3 py-2 text-sm font-medium ring-1 ring-inset transition"
          :class="
            active?.key === tab.key
              ? 'bg-brand-50 text-brand-800 ring-brand-200'
              : 'bg-surface text-ink ring hover:bg-surface-muted'
          "
          @click="active = tab"
        >
          {{ tab.label }}
        </button>
      </div>

      <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>

      <UiCard v-if="active">
        <p v-if="active.note" class="mb-3 text-sm text-ink-muted">ⓘ {{ active.note }}</p>

        <UiSkeleton v-if="loading" :rows="6" />
        <UiEmpty
          v-else-if="!rows.length"
          icon="receipt"
          title="لا توجد بيانات"
          description="لا توجد حركة في هذه الفترة."
        />
        <UiTable v-else :columns="active.columns">
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
        </UiTable>
      </UiCard>
    </template>
  </div>
</template>
