<script setup lang="ts">
/**
 * One order, with its event stream.
 *
 * The stream is the point: "why is this bill 204.29?" is answered by reading
 * what happened, in order, not by trusting a total.
 */
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { ApiError, api } from '@/api/client'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiBadge from '@/components/ui/UiBadge.vue'
import UiIcon from '@/components/ui/UiIcon.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import { dateTime, money, percent, quantity } from '@/lib/format'

interface Item {
  id: string
  name_snapshot: string
  unit_price_snapshot: string
  /** null when the line is at the catalogue price. `'0.00'` is a comped item. */
  price_override: string | null
  price_override_reason: string
  quantity: string
  discount_percent: string
  line_total: string
  status: string
  note: string
  void_reason: string
  modifiers: { id: string; name_snapshot: string; price_delta_snapshot: string }[]
}

interface Order {
  id: string
  local_number: string
  order_type: string
  status: string
  table_number: string | null
  subtotal: string
  discount_total: string
  discount_reason: string
  service_total: string
  tax_total: string
  rounding_adjustment: string
  grand_total: string
  paid_total: string
  balance_due: string
  vat_percent: string
  service_percent: string
  opened_at: string
  closed_at: string | null
  opened_by_name: string | null
  void_reason: string
}

interface Event {
  id: string
  sequence: number
  event_type: string
  payload: Record<string, unknown>
  actor_name: string | null
  approved_by_name: string | null
  occurred_at: string
}

const EVENT_LABELS: Record<string, string> = {
  ORDER_OPENED: 'فتح الطلب',
  ITEM_ADDED: 'إضافة صنف',
  ITEM_QUANTITY_CHANGED: 'تغيير الكمية',
  ITEM_VOIDED: 'إلغاء صنف',
  ITEM_NOTE_SET: 'إضافة ملاحظة',
  ITEM_PRICE_OVERRIDDEN: 'تعديل سعر يدوياً',
  DISCOUNT_APPLIED: 'تطبيق خصم',
  ORDER_FIRED: 'إرسال للمطبخ',
  TABLE_ASSIGNED: 'تحديد طاولة',
  CUSTOMER_ASSIGNED: 'تحديد عميل',
  PAYMENT_TAKEN: 'تحصيل دفعة',
  ORDER_CLOSED: 'إغلاق الطلب',
  ORDER_VOIDED: 'إلغاء الطلب',
}

const route = useRoute()
const order = ref<Order | null>(null)
const items = ref<Item[]>([])
const events = ref<Event[]>([])
const loading = ref(true)
const error = ref('')

onMounted(async () => {
  const id = route.params.id as string
  try {
    const [detail, stream] = await Promise.all([
      api.get<Order & { items: Item[] }>(`/orders/${id}/`),
      api.get<Event[]>(`/orders/${id}/events/`),
    ])
    order.value = detail
    items.value = detail.items
    events.value = stream
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : 'تعذر تحميل الطلب'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="space-y-6">
    <UiSkeleton v-if="loading" :rows="10" />
    <UiAlert v-else-if="error" tone="error">{{ error }}</UiAlert>

    <template v-else-if="order">
      <div class="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 class="text-2xl font-bold text-ink">{{ order.local_number }}</h1>
          <p class="mt-1 text-sm text-ink-muted">
            {{ order.table_number ? `طاولة ${order.table_number}` : 'تيك أواي' }} ·
            {{ dateTime(order.opened_at) }}
            <span v-if="order.opened_by_name"> · {{ order.opened_by_name }}</span>
          </p>
        </div>
        <UiBadge :tone="order.status === 'PAID' ? 'success' : 'info'">{{ order.status }}</UiBadge>
      </div>

      <UiAlert v-if="order.void_reason" tone="warning">
        تم إلغاء الطلب: {{ order.void_reason }}
      </UiAlert>

      <div class="grid gap-6 lg:grid-cols-[1fr_320px]">
        <UiCard title="الأصناف">
          <ul class="divide-y divide-line">
            <li
              v-for="item in items"
              :key="item.id"
              class="flex items-start justify-between gap-4 py-3"
              :class="item.status === 'VOIDED' && 'opacity-50'"
            >
              <div class="min-w-0">
                <p class="font-medium text-ink">
                  <span class="tabular-nums">{{ quantity(item.quantity) }}×</span>
                  {{ item.name_snapshot }}
                  <UiBadge v-if="item.status === 'VOIDED'" tone="danger">ملغي</UiBadge>
                </p>
                <p v-if="item.modifiers.length" class="text-sm text-ink-muted">
                  {{ item.modifiers.map((m) => m.name_snapshot).join('، ') }}
                </p>
                <p v-if="item.note" class="flex items-center gap-1.5 text-sm text-ink-muted">
                  <UiIcon name="note" size="0.9rem" /> {{ item.note }}
                </p>
                <p v-if="item.void_reason" class="text-sm text-danger">
                  سبب الإلغاء: {{ item.void_reason }}
                </p>
                <p v-if="item.price_override !== null" class="text-sm text-warning">
                  سعر يدوي
                  <span v-if="item.price_override_reason">— {{ item.price_override_reason }}</span>
                </p>
              </div>
              <div class="text-end">
                <p class="font-medium tabular-nums">{{ money(item.line_total) }}</p>
                <!--
                  When a price was overridden, showing the catalogue price here
                  would make the line look like it added up wrong. Show what was
                  charged, and strike through what it should have been — the
                  comparison is the whole point of recording both.
                -->
                <p class="text-xs text-ink-muted tabular-nums">
                  {{ money(item.price_override ?? item.unit_price_snapshot) }} للوحدة
                </p>
                <p
                  v-if="item.price_override !== null"
                  class="text-xs text-ink-faint line-through tabular-nums"
                >
                  {{ money(item.unit_price_snapshot) }}
                </p>
              </div>
            </li>
          </ul>
        </UiCard>

        <UiCard title="الحساب">
          <dl class="space-y-2.5 text-sm">
            <div class="flex justify-between">
              <dt class="text-ink-muted">الإجمالي</dt>
              <dd class="tabular-nums">{{ money(order.subtotal) }}</dd>
            </div>
            <div v-if="Number(order.discount_total)" class="flex justify-between text-success">
              <dt>خصم</dt>
              <dd class="tabular-nums">− {{ money(order.discount_total) }}</dd>
            </div>
            <div v-if="Number(order.service_total)" class="flex justify-between">
              <dt class="text-ink-muted">خدمة {{ percent(order.service_percent) }}</dt>
              <dd class="tabular-nums">{{ money(order.service_total) }}</dd>
            </div>
            <div v-if="Number(order.tax_total)" class="flex justify-between">
              <dt class="text-ink-muted">ض.ق.م {{ percent(order.vat_percent) }}</dt>
              <dd class="tabular-nums">{{ money(order.tax_total) }}</dd>
            </div>
            <div
              v-if="Number(order.rounding_adjustment)"
              class="flex justify-between text-ink-muted"
            >
              <dt>تقريب</dt>
              <dd class="tabular-nums">{{ money(order.rounding_adjustment) }}</dd>
            </div>
            <div class="flex justify-between border-t border-line pt-2.5 text-base font-bold">
              <dt>المطلوب</dt>
              <dd class="tabular-nums">{{ money(order.grand_total) }}</dd>
            </div>
            <div v-if="Number(order.paid_total)" class="flex justify-between text-success">
              <dt>المدفوع</dt>
              <dd class="tabular-nums">{{ money(order.paid_total) }}</dd>
            </div>
            <div
              v-if="Number(order.balance_due) > 0"
              class="flex justify-between font-semibold text-warning"
            >
              <dt>المتبقي</dt>
              <dd class="tabular-nums">{{ money(order.balance_due) }}</dd>
            </div>
          </dl>
        </UiCard>
      </div>

      <UiCard
        title="سلسلة الأحداث"
        subtitle="السجل الكامل لما حدث في هذا الطلب، بالترتيب."
      >
        <ol class="space-y-3">
          <li v-for="event in events" :key="event.id" class="flex gap-3">
            <span
              class="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full
                     bg-surface-sunken text-xs font-semibold tabular-nums text-ink-muted"
            >
              {{ event.sequence }}
            </span>
            <div class="min-w-0 flex-1">
              <p class="text-sm font-medium text-ink">
                {{ EVENT_LABELS[event.event_type] ?? event.event_type }}
              </p>
              <p class="text-xs text-ink-muted">
                {{ dateTime(event.occurred_at) }}
                <span v-if="event.actor_name"> · {{ event.actor_name }}</span>
                <span v-if="event.approved_by_name" class="text-warning">
                  · بموافقة {{ event.approved_by_name }}
                </span>
              </p>
            </div>
          </li>
        </ol>
      </UiCard>
    </template>
  </div>
</template>
