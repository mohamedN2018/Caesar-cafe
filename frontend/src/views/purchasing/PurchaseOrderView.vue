<script setup lang="ts">
/**
 * Purchase orders and goods receipts.
 *
 * The screen is built around the one rule the purchasing app exists for: **a
 * purchase order moves no stock, and a goods receipt does.** They are two lists
 * and two buttons, not one workflow with a status field, because a UI that
 * blurred them would eventually let somebody "receive" by editing an order —
 * and then the shelf and the ledger would part company.
 *
 * Three consequences worth naming:
 *
 *   * **Receiving asks for the invoiced cost, pre-filled from the order but
 *     editable.** Suppliers raise prices between order and delivery, and
 *     receiving at what was ordered rather than what was invoiced is what makes
 *     a margin quietly wrong.
 *   * **Posting is a separate, deliberate action.** A receipt is a document
 *     until somebody posts it; otherwise a typo in a draft is already a stock
 *     movement.
 *   * **A receipt needs no purchase order.** Cafes buy milk from the shop down
 *     the road when they run out, and that has to be recordable.
 */
import { computed, onMounted, ref } from 'vue'

import { ApiError, api } from '@/api/client'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiBadge from '@/components/ui/UiBadge.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiInput from '@/components/ui/UiInput.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import { dateTime, money, quantity as fmtQuantity } from '@/lib/format'
import { useAuthStore } from '@/stores/auth'

interface Line {
  id?: string
  item: string
  item_code?: string
  item_name?: string
  unit: string
  unit_code?: string
  quantity_ordered?: string
  quantity_received?: string
  unit_price?: string
  unit_cost?: string
  line_total?: string
  outstanding?: string
  po_line?: string | null
}

interface PurchaseOrder {
  id: string
  supplier: string
  supplier_name: string
  po_number: string
  status: string
  expected_date: string | null
  notes: string
  subtotal: string
  is_fully_received: boolean
  lines: Line[]
  created_at: string
}

interface Receipt {
  id: string
  purchase_order: string | null
  supplier: string
  supplier_name: string
  grn_number: string
  supplier_invoice_no: string
  received_date: string
  posted_at: string | null
  is_posted: boolean
  grand_total: string
  lines: Line[]
}

interface Supplier {
  id: string
  name: string
}

interface Item {
  id: string
  code: string
  name_ar: string
  base_unit: string
  base_unit_code: string
}

interface Suggestion {
  item_id: string
  item_code: string
  item_name: string
  available: string
  reorder_level: string
  suggested_quantity: string
  supplier: string | null
  supplier_id: string | null
}

const STATUS: Record<string, { label: string; tone: 'neutral' | 'info' | 'warning' | 'success' | 'danger' }> = {
  DRAFT: { label: 'مسوّدة', tone: 'neutral' },
  SUBMITTED: { label: 'مُرسل', tone: 'info' },
  PARTIAL: { label: 'استلام جزئي', tone: 'warning' },
  RECEIVED: { label: 'مستلم بالكامل', tone: 'success' },
  CANCELLED: { label: 'ملغى', tone: 'danger' },
}

const auth = useAuthStore()
const mayOrder = computed(() => auth.can('purchasing.create_po'))
const mayReceive = computed(() => auth.can('purchasing.receive'))

const tab = ref<'orders' | 'receipts' | 'reorder'>('orders')
const orders = ref<PurchaseOrder[]>([])
const receipts = ref<Receipt[]>([])
const suggestions = ref<Suggestion[]>([])
const suppliers = ref<Supplier[]>([])
const items = ref<Item[]>([])
const loading = ref(true)
const error = ref('')
const busy = ref('')

const draft = ref({
  supplier: '',
  po_number: '',
  expected_date: '',
  lines: [] as { item: string; unit: string; quantity_ordered: string; unit_price: string }[],
})

const receiving = ref<PurchaseOrder | null>(null)
const receiptDraft = ref({ grn_number: '', supplier_invoice_no: '', lines: [] as Line[] })

const unposted = computed(() => receipts.value.filter((r) => !r.is_posted))

function itemById(id: string): Item | undefined {
  return items.value.find((i) => i.id === id)
}

async function load() {
  loading.value = true
  try {
    const [orderRows, receiptRows, supplierRows, itemRows] = await Promise.all([
      api.get<PurchaseOrder[]>('/purchasing/purchase-orders/'),
      api.get<Receipt[]>('/purchasing/receipts/'),
      api.get<Supplier[]>('/suppliers/'),
      api.get<Item[]>('/inventory/items/'),
    ])
    orders.value = orderRows
    receipts.value = receiptRows
    suppliers.value = supplierRows
    items.value = itemRows
    if (!draft.value.supplier && suppliers.value.length) draft.value.supplier = suppliers.value[0].id
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تحميل بيانات الشراء.'
  } finally {
    loading.value = false
  }

  try {
    suggestions.value = await api.get<Suggestion[]>('/purchasing/reorder-suggestions/')
  } catch {
    suggestions.value = []
  }
}

// ── ordering ────────────────────────────────────────────────────────────────

function addDraftLine(item?: Item, quantity = '') {
  const chosen = item ?? items.value[0]
  if (!chosen) return
  draft.value.lines.push({
    item: chosen.id,
    unit: chosen.base_unit,
    quantity_ordered: quantity,
    unit_price: '',
  })
}

function orderFromSuggestion(suggestion: Suggestion) {
  const item = itemById(suggestion.item_id)
  if (!item) return
  if (suggestion.supplier_id) draft.value.supplier = suggestion.supplier_id
  addDraftLine(item, suggestion.suggested_quantity)
  tab.value = 'orders'
}

async function createOrder() {
  if (!draft.value.supplier || !draft.value.po_number.trim() || !draft.value.lines.length) return
  busy.value = 'order'
  try {
    await api.post('/purchasing/purchase-orders/', {
      supplier: draft.value.supplier,
      po_number: draft.value.po_number.trim(),
      expected_date: draft.value.expected_date || null,
      lines: draft.value.lines,
    })
    draft.value = { supplier: draft.value.supplier, po_number: '', expected_date: '', lines: [] }
    await load()
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر إنشاء أمر الشراء.'
  } finally {
    busy.value = ''
  }
}

async function act(url: string, key: string) {
  busy.value = key
  try {
    await api.post(url)
    await load()
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تنفيذ الإجراء.'
  } finally {
    busy.value = ''
  }
}

// ── receiving ───────────────────────────────────────────────────────────────

function startReceiving(order: PurchaseOrder) {
  receiving.value = order
  receiptDraft.value = {
    grn_number: '',
    supplier_invoice_no: '',
    // Pre-filled from the order, but every figure stays editable: what arrived
    // and what was invoiced are both facts the delivery decides, not the order.
    lines: order.lines
      .filter((line) => Number(line.outstanding) > 0)
      .map((line) => ({
        po_line: line.id,
        item: line.item,
        unit: line.unit,
        quantity_received: line.outstanding,
        unit_cost: line.unit_price,
      })),
  }
}

async function createReceipt() {
  if (!receiving.value || !receiptDraft.value.grn_number.trim()) return
  busy.value = 'receipt'
  try {
    await api.post('/purchasing/receipts/', {
      purchase_order: receiving.value.id,
      supplier: receiving.value.supplier,
      grn_number: receiptDraft.value.grn_number.trim(),
      supplier_invoice_no: receiptDraft.value.supplier_invoice_no,
      received_date: new Date().toISOString().slice(0, 10),
      lines: receiptDraft.value.lines,
    })
    receiving.value = null
    tab.value = 'receipts'
    await load()
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تسجيل الاستلام.'
  } finally {
    busy.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-bold text-ink">الشراء والاستلام</h1>
      <p class="mt-1 text-sm text-ink-muted">
        أمر الشراء نيّة ولا يحرّك المخزون. سند الاستلام واقعة، وهو وحده ما يحرّكه.
      </p>
    </div>

    <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>
    <UiAlert v-if="unposted.length" tone="warning">
      {{ unposted.length }} سند استلام لم يُرحَّل — المخزون لم يتغيّر بعد.
    </UiAlert>

    <div class="flex flex-wrap gap-2">
      <button
        v-for="option in [
          { key: 'orders', label: 'أوامر الشراء' },
          { key: 'receipts', label: 'سندات الاستلام' },
          { key: 'reorder', label: `إعادة الطلب (${suggestions.length})` },
        ]"
        :key="option.key"
        class="rounded-lg px-3 py-2 text-sm font-medium ring-1 ring-inset transition"
        :class="
          tab === option.key
            ? 'bg-brand-50 text-brand-800 ring-brand-200'
            : 'bg-surface text-ink ring hover:bg-surface-muted'
        "
        @click="tab = option.key as typeof tab"
      >
        {{ option.label }}
      </button>
    </div>

    <UiSkeleton v-if="loading" :rows="6" />

    <!-- ── orders ─────────────────────────────────────────────────────────── -->

    <template v-else-if="tab === 'orders'">
      <UiEmpty
        v-if="!orders.length"
        icon="box"
        title="لا توجد أوامر شراء"
        description="أنشئ أمراً لتسجيل ما طلبته من المورد."
      />

      <div v-else class="grid gap-3">
        <UiCard v-for="order in orders" :key="order.id">
          <div class="flex flex-wrap items-start justify-between gap-4">
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <span class="font-mono font-bold text-ink" dir="ltr">
                  {{ order.po_number }}
                </span>
                <UiBadge :tone="STATUS[order.status]?.tone ?? 'neutral'">
                  {{ STATUS[order.status]?.label ?? order.status }}
                </UiBadge>
                <span class="text-sm text-ink-muted">{{ order.supplier_name }}</span>
              </div>
              <p class="mt-1 text-sm text-ink-muted">
                {{ dateTime(order.created_at) }}
                <span v-if="order.expected_date">· متوقع {{ order.expected_date }}</span>
              </p>
              <ul class="mt-2 space-y-0.5 text-sm text-ink">
                <li v-for="line in order.lines" :key="line.id">
                  {{ line.item_name }} —
                  <span class="font-mono tabular-nums" dir="ltr">
                    {{ fmtQuantity(line.quantity_ordered, line.unit_code) }}
                  </span>
                  <span v-if="Number(line.quantity_received) > 0" class="text-success">
                    · وصل {{ fmtQuantity(line.quantity_received, line.unit_code) }}
                  </span>
                </li>
              </ul>
            </div>

            <div class="flex flex-col items-end gap-2">
              <p class="font-mono text-lg font-bold tabular-nums text-ink" dir="ltr">
                {{ money(order.subtotal) }}
              </p>
              <div class="flex flex-wrap gap-2">
                <UiButton
                  v-if="mayOrder && order.status === 'DRAFT'"
                  size="sm"
                  :loading="busy === order.id"
                  @click="act(`/purchasing/purchase-orders/${order.id}/submit/`, order.id)"
                >
                  إرسال
                </UiButton>
                <UiButton
                  v-if="mayReceive && ['SUBMITTED', 'PARTIAL'].includes(order.status)"
                  size="sm"
                  variant="secondary"
                  @click="startReceiving(order)"
                >
                  تسجيل استلام
                </UiButton>
                <UiButton
                  v-if="mayOrder && !['RECEIVED', 'CANCELLED'].includes(order.status)"
                  size="sm"
                  variant="ghost"
                  @click="act(`/purchasing/purchase-orders/${order.id}/cancel/`, order.id)"
                >
                  إلغاء
                </UiButton>
              </div>
            </div>
          </div>
        </UiCard>
      </div>

      <UiCard v-if="receiving">
        <h2 class="text-sm font-semibold text-ink">
          استلام على أمر {{ receiving.po_number }}
        </h2>
        <p class="mt-1 text-xs text-ink-muted">
          السعر مأخوذ من الأمر وقابل للتعديل — يُسجَّل بسعر الفاتورة الفعلي، لا بسعر الطلب.
        </p>

        <form class="mt-3 space-y-3" @submit.prevent="createReceipt">
          <div class="grid gap-3 sm:grid-cols-2">
            <UiInput v-model="receiptDraft.grn_number" label="رقم السند" ltr required />
            <UiInput
              v-model="receiptDraft.supplier_invoice_no"
              label="رقم فاتورة المورد"
              ltr
            />
          </div>

          <div
            v-for="(line, index) in receiptDraft.lines"
            :key="index"
            class="grid gap-3 rounded-lg bg-surface-muted px-3 py-2 sm:grid-cols-3"
          >
            <p class="self-center text-sm font-medium text-ink">
              {{ itemById(line.item)?.name_ar }}
            </p>
            <UiInput
              v-model="line.quantity_received"
              label="الكمية المستلمة"
              type="number"
              step="0.001"
              ltr
            />
            <UiInput v-model="line.unit_cost" label="سعر الوحدة (فاتورة)" type="number" step="0.0001" ltr />
          </div>

          <div class="flex gap-2">
            <UiButton type="submit" :loading="busy === 'receipt'">حفظ السند</UiButton>
            <UiButton variant="ghost" @click="receiving = null">إلغاء</UiButton>
          </div>
        </form>
      </UiCard>

      <UiCard v-if="mayOrder && !receiving">
        <h2 class="text-sm font-semibold text-ink">أمر شراء جديد</h2>

        <form class="mt-3 space-y-3" @submit.prevent="createOrder">
          <div class="grid gap-3 sm:grid-cols-3">
            <label class="block">
              <span class="mb-1.5 block text-sm font-medium text-ink">المورد</span>
              <select
                v-model="draft.supplier"
                class="w-full min-h-[44px] rounded-lg border border-line-strong bg-surface px-3 text-[15px]
                       focus:border-brand-700 focus:outline-none focus:ring-2 focus:ring-brand-700/30"
              >
                <option v-for="supplier in suppliers" :key="supplier.id" :value="supplier.id">
                  {{ supplier.name }}
                </option>
              </select>
            </label>
            <UiInput v-model="draft.po_number" label="رقم الأمر" ltr required />
            <UiInput v-model="draft.expected_date" label="تاريخ التوريد المتوقع" type="date" />
          </div>

          <div
            v-for="(line, index) in draft.lines"
            :key="index"
            class="grid gap-3 rounded-lg bg-surface-muted px-3 py-2 sm:grid-cols-4"
          >
            <label class="block sm:col-span-2">
              <span class="mb-1.5 block text-sm font-medium text-ink">الصنف</span>
              <select
                v-model="line.item"
                class="w-full min-h-[44px] rounded-lg border border-line-strong bg-surface px-3 text-[15px]"
              >
                <option v-for="item in items" :key="item.id" :value="item.id">
                  {{ item.name_ar }} ({{ item.code }})
                </option>
              </select>
            </label>
            <UiInput v-model="line.quantity_ordered" label="الكمية" type="number" step="0.001" ltr />
            <UiInput v-model="line.unit_price" label="سعر الوحدة" type="number" step="0.0001" ltr />
          </div>

          <div class="flex flex-wrap gap-2">
            <UiButton variant="secondary" @click="addDraftLine()">إضافة صنف</UiButton>
            <UiButton
              type="submit"
              :loading="busy === 'order'"
              :disabled="!draft.lines.length || !draft.po_number.trim()"
            >
              حفظ كمسوّدة
            </UiButton>
          </div>
          <p class="text-xs text-ink-faint">
            الحفظ لا يحرّك المخزون ولا يبلّغ المورد — الإرسال خطوة منفصلة.
          </p>
        </form>
      </UiCard>
    </template>

    <!-- ── receipts ───────────────────────────────────────────────────────── -->

    <template v-else-if="tab === 'receipts'">
      <UiEmpty
        v-if="!receipts.length"
        icon="receipt"
        title="لا توجد سندات استلام"
        description="السند هو ما يزيد المخزون ويقيّد على المورد."
      />

      <div v-else class="grid gap-3">
        <UiCard v-for="receipt in receipts" :key="receipt.id">
          <div class="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div class="flex flex-wrap items-center gap-2">
                <span class="font-mono font-bold text-ink" dir="ltr">
                  {{ receipt.grn_number }}
                </span>
                <UiBadge :tone="receipt.is_posted ? 'success' : 'warning'">
                  {{ receipt.is_posted ? 'مُرحَّل' : 'غير مُرحَّل' }}
                </UiBadge>
                <span class="text-sm text-ink-muted">{{ receipt.supplier_name }}</span>
              </div>
              <p class="mt-1 text-sm text-ink-muted">
                {{ receipt.received_date }}
                <span v-if="receipt.supplier_invoice_no" dir="ltr">
                  · فاتورة {{ receipt.supplier_invoice_no }}
                </span>
              </p>
              <ul class="mt-2 space-y-0.5 text-sm text-ink">
                <li v-for="line in receipt.lines" :key="line.id">
                  {{ line.item_name }} —
                  <span class="font-mono tabular-nums" dir="ltr">
                    {{ fmtQuantity(line.quantity_received, line.unit_code) }}
                    @ {{ line.unit_cost }}
                  </span>
                </li>
              </ul>
            </div>

            <div class="flex flex-col items-end gap-2">
              <p class="font-mono text-lg font-bold tabular-nums text-ink" dir="ltr">
                {{ money(receipt.grand_total) }}
              </p>
              <UiButton
                v-if="mayReceive && !receipt.is_posted"
                size="sm"
                :loading="busy === receipt.id"
                @click="act(`/purchasing/receipts/${receipt.id}/post/`, receipt.id)"
              >
                ترحيل (يزيد المخزون)
              </UiButton>
            </div>
          </div>
        </UiCard>
      </div>
    </template>

    <!-- ── reorder ────────────────────────────────────────────────────────── -->

    <template v-else>
      <UiEmpty
        v-if="!suggestions.length"
        icon="check"
        title="لا شيء تحت حد الطلب"
        description="كل الأصناف فوق حدودها المحددة."
      />

      <UiCard v-else>
        <p class="text-sm text-ink-muted">
          اقتراح، وليس أمر شراء. تحويله إلى طلب تلقائي معناه أن يصرف النظام مالاً بناءً على رقم
          كُتب مرة واحدة.
        </p>
        <div class="mt-3 overflow-x-auto">
          <table class="w-full text-sm">
            <thead class="text-xs text-ink-muted">
              <tr>
                <th class="px-2 py-2 text-start">الصنف</th>
                <th class="px-2 py-2 text-end">المتاح</th>
                <th class="px-2 py-2 text-end">حد الطلب</th>
                <th class="px-2 py-2 text-end">المقترح</th>
                <th class="px-2 py-2 text-start">المورد</th>
                <th />
              </tr>
            </thead>
            <tbody class="divide-y divide-line">
              <tr v-for="row in suggestions" :key="row.item_id">
                <td class="px-2 py-2 text-ink">{{ row.item_name }}</td>
                <td class="px-2 py-2 text-end font-mono tabular-nums text-danger" dir="ltr">
                  {{ fmtQuantity(row.available) }}
                </td>
                <td class="px-2 py-2 text-end font-mono tabular-nums text-ink-muted" dir="ltr">
                  {{ fmtQuantity(row.reorder_level) }}
                </td>
                <td class="px-2 py-2 text-end font-mono tabular-nums text-ink" dir="ltr">
                  {{ fmtQuantity(row.suggested_quantity) }}
                </td>
                <td class="px-2 py-2 text-ink-muted">{{ row.supplier ?? '—' }}</td>
                <td class="px-2 py-2 text-end">
                  <UiButton v-if="mayOrder" size="sm" variant="secondary" @click="orderFromSuggestion(row)">
                    أضف لأمر شراء
                  </UiButton>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </UiCard>
    </template>
  </div>
</template>
