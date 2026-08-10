<script setup lang="ts">
/**
 * What one person has been doing.
 *
 * The counts are laid out in two groups because they answer two different
 * questions. **Work** — orders, changes, payments — says how busy somebody was,
 * and is only meaningful next to another cashier's. **Watch** — voids,
 * discounts, manual prices — are the three that move money without selling
 * anything, and they are the reason this screen exists at all.
 *
 * Split rather than listed together on purpose: seven numbers in a row is a
 * grid nobody reads, and burying "12 voids" between "84 orders" and "3 shifts"
 * is how it goes unnoticed for a month.
 *
 * The trail beneath is the audit log filtered to this person. Nothing here is a
 * separate tally kept alongside the records — every figure is derived from
 * orders, payments and the audit trail, so it cannot disagree with them.
 */
import { computed, onMounted, ref, watch } from 'vue'

import { ApiError, api } from '@/api/client'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import { dateTime } from '@/lib/format'

const props = withDefaults(defineProps<{ userId: string; days?: number }>(), { days: 30 })

interface Activity {
  user: { id: string; full_name_ar: string }
  days: number
  orders_opened: number
  changes_made: number
  payments_taken: number
  items_voided: number
  discounts_given: number
  prices_overridden: number
  approvals_given: number
  recent: { action: string; label: string; at: string; severity: string }[]
}

const data = ref<Activity | null>(null)
const loading = ref(true)
const error = ref('')

const work = computed(() => {
  const a = data.value
  if (!a) return []
  return [
    { label: 'طلبات فتحها', value: a.orders_opened },
    { label: 'تعديلات', value: a.changes_made },
    { label: 'دفعات حصّلها', value: a.payments_taken },
  ]
})

const watched = computed(() => {
  const a = data.value
  if (!a) return []
  return [
    { label: 'أصناف ألغاها', value: a.items_voided },
    { label: 'خصومات', value: a.discounts_given },
    { label: 'أسعار يدوية', value: a.prices_overridden },
    { label: 'موافقات أعطاها', value: a.approvals_given },
  ]
})

async function load() {
  loading.value = true
  try {
    data.value = await api.get<Activity>(`/staff/${props.userId}/activity/`, {
      days: props.days,
    })
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تحميل النشاط.'
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => [props.userId, props.days], load)
</script>

<template>
  <div>
    <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>
    <UiSkeleton v-else-if="loading" :rows="4" />

    <template v-else-if="data">
      <p class="since">آخر {{ data.days }} يوم</p>

      <div class="band">
        <div v-for="cell in work" :key="cell.label" class="cell">
          <span class="cell-value">{{ cell.value }}</span>
          <span class="cell-label">{{ cell.label }}</span>
        </div>
      </div>

      <p class="group-label">تحت المراقبة</p>
      <div class="band">
        <div
          v-for="cell in watched"
          :key="cell.label"
          class="cell"
          :class="{ 'is-flagged': cell.value > 0 }"
        >
          <span class="cell-value">{{ cell.value }}</span>
          <span class="cell-label">{{ cell.label }}</span>
        </div>
      </div>

      <p class="group-label">آخر العمليات</p>
      <p v-if="!data.recent.length" class="quiet">لا توجد عمليات مسجّلة في هذه الفترة.</p>
      <ul v-else class="trail">
        <li v-for="(row, index) in data.recent" :key="index">
          <span class="trail-label">{{ row.label || row.action }}</span>
          <span class="trail-at">{{ dateTime(row.at) }}</span>
        </li>
      </ul>
    </template>
  </div>
</template>

<style scoped>
.since {
  font-size: 0.75rem;
  color: var(--ink-faint);
  margin-bottom: 0.6rem;
}

.group-label {
  margin: 1rem 0 0.5rem;
  font-size: 0.75rem;
  font-weight: 700;
  color: var(--ink-muted);
}

.band {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(6rem, 1fr));
  gap: 0.5rem;
}

.cell {
  display: flex;
  flex-direction: column;
  padding: 0.6rem 0.75rem;
  border-radius: 0.6rem;
  background: var(--surface-muted);
  border: 1px solid var(--border);
}
/* A non-zero count in the watched group is marked, not coloured red: these are
   normal parts of a shift, and painting every discount as an alarm teaches the
   reader to ignore the colour. */
.cell.is-flagged {
  border-color: var(--warning);
}

.cell-value {
  font-size: 1.3rem;
  font-weight: 650;
  color: var(--ink);
}
.cell-label {
  font-size: 0.72rem;
  color: var(--ink-muted);
}

.quiet {
  font-size: 0.82rem;
  color: var(--ink-faint);
}

.trail {
  max-height: 16rem;
  overflow-y: auto;
}
.trail li {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.4rem 0;
  border-bottom: 1px solid var(--surface-sunken);
  font-size: 0.82rem;
}
.trail-label {
  color: var(--ink);
}
.trail-at {
  flex: 0 0 auto;
  color: var(--ink-faint);
  font-size: 0.72rem;
}
</style>
