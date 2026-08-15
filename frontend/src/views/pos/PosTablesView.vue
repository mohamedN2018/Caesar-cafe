<script setup lang="ts">
/**
 * The till opens on the room, not on the menu.
 *
 * This is a deliberate reversal. The POS used to land on the product board, so
 * the first question it asked was "what are you selling?" — which is the second
 * question. In a café with table service the first one is always "who is this
 * for?", and answering it late is how a round of coffees lands on the wrong bill
 * and is discovered at closing.
 *
 * Every table carries what a waiter actually needs to decide where to go next:
 * whether anyone is sitting there, how long they have been, how many orders are
 * open on them and what is owed. All of it from a single `/floor/status/` call —
 * the board is one request, not one per table, because a floor screen that fans
 * out per table gets slower exactly as the room gets busier.
 *
 * The geometry (`pos_x`, `pos_y`, `span`, `shape`, `rotation`) is the same the
 * admin floor plan uses, so a table moved on the plan moves here. A second,
 * hand-maintained layout would be two drawings of one room, and they would
 * disagree the first week.
 */
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { useRouter } from 'vue-router'

import { ApiError, api } from '@/api/client'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import { money } from '@/lib/format'

interface FloorTable {
  table_id: string
  number: string
  area: string
  seats: number
  seated_count: number
  status: string
  pos_x: number
  pos_y: number
  shape: string
  span_x: number
  span_y: number
  rotation: number
  session_id: string | null
  guest_count: number | null
  seated_minutes: number | null
  order_count: number
  total_due: string
  waiter: string | null
}

const router = useRouter()

const tables = ref<FloorTable[]>([])
const loading = ref(true)
const error = ref('')
const area = ref<string>('')

/**
 * Refreshed on a timer, because a floor is shared.
 *
 * Two waiters and a cashier look at this at once; a board that only updates when
 * you reload it will show a table as free that somebody seated a minute ago. Ten
 * seconds is frequent enough that the room is never meaningfully stale and rare
 * enough that a busy service is not making six requests a second.
 */
const REFRESH_MS = 10_000
let timer: ReturnType<typeof setInterval> | undefined

async function load(showSpinner = false) {
  if (showSpinner) loading.value = true
  try {
    const payload = await api.get<{ tables: FloorTable[] }>('/floor/status/')
    tables.value = payload.tables ?? []
    error.value = ''
  } catch (exc) {
    // A refresh that fails leaves the last good board on screen rather than
    // blanking it. A waiter mid-service needs the slightly stale answer far more
    // than they need an empty screen that is technically honest.
    error.value = exc instanceof ApiError ? exc.message : 'تعذّر تحديث الصالة.'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  load(true)
  timer = setInterval(() => load(false), REFRESH_MS)
})
onBeforeUnmount(() => clearInterval(timer))

const areas = computed(() => [...new Set(tables.value.map((t) => t.area))])

const visible = computed(() =>
  area.value ? tables.value.filter((t) => t.area === area.value) : tables.value,
)

/** Free tables first is wrong: a waiter is looking for the table they were called to. */
const sorted = computed(() =>
  [...visible.value].sort((a, b) =>
    a.area === b.area
      ? a.number.localeCompare(b.number, 'ar', { numeric: true })
      : a.area.localeCompare(b.area, 'ar'),
  ),
)

const occupied = computed(() => tables.value.filter((t) => t.session_id).length)
const owed = computed(() =>
  tables.value.reduce((sum, t) => sum + Number(t.total_due || 0), 0),
)

function isBusy(table: FloorTable): boolean {
  return Boolean(table.session_id)
}

/** Long-seated with nothing ordered is the one state worth flagging. */
function isNeglected(table: FloorTable): boolean {
  return isBusy(table) && table.order_count === 0 && (table.seated_minutes ?? 0) >= 10
}

function minutes(value: number | null): string {
  if (value === null) return ''
  if (value < 60) return `${value} د`
  return `${Math.floor(value / 60)} س ${value % 60} د`
}

/**
 * Tapping a table goes to the order screen carrying the table with it.
 *
 * The table id travels in the query rather than the board keeping it in memory,
 * so a reload — or a second device opening the same URL — lands on the same
 * table instead of on a blank order that belongs to nobody.
 */
function open(table: FloorTable) {
  router.push({
    name: 'pos-order',
    query: {
      table: table.table_id,
      session: table.session_id ?? undefined,
      number: table.number,
    },
  })
}

/** Selling without a table: takeaway, delivery, the counter. */
function walkIn() {
  router.push({ name: 'pos-order' })
}
</script>

<template>
  <div class="tables-screen">
    <header class="tables-head">
      <div>
        <h1 class="tables-title">الصالة</h1>
        <p class="tables-sub">
          <template v-if="!loading">
            {{ occupied }} من {{ tables.length }} مشغولة · مستحق {{ money(owed) }}
          </template>
        </p>
      </div>

      <div class="tables-actions">
        <UiButton variant="secondary" size="lg" @click="walkIn">
          طلب سفري / بدون طاولة
        </UiButton>
      </div>
    </header>

    <UiAlert v-if="error" tone="warning" class="mb-3">{{ error }}</UiAlert>

    <!-- Area tabs, only when there is more than one room to choose between. -->
    <nav v-if="areas.length > 1" class="area-tabs">
      <button
        type="button"
        class="area-tab"
        :class="{ 'area-tab-on': area === '' }"
        @click="area = ''"
      >
        الكل
      </button>
      <button
        v-for="name in areas"
        :key="name"
        type="button"
        class="area-tab"
        :class="{ 'area-tab-on': area === name }"
        @click="area = name"
      >
        {{ name }}
      </button>
    </nav>

    <div v-if="loading" class="tables-grid">
      <UiSkeleton v-for="n in 12" :key="n" class="h-28 rounded-xl" />
    </div>

    <div v-else-if="!tables.length" class="tables-empty">
      <p>لا توجد طاولات معرَّفة.</p>
      <p class="tables-empty-hint">تُضاف من شاشة «الصالة» في الإدارة.</p>
    </div>

    <div v-else class="tables-grid">
      <button
        v-for="table in sorted"
        :key="table.table_id"
        type="button"
        class="table-card"
        :class="{
          'table-busy': isBusy(table),
          'table-neglected': isNeglected(table),
        }"
        @click="open(table)"
      >
        <span class="table-number">{{ table.number }}</span>

        <span class="table-meta">
          <!-- Seated count, not capacity: "4 من 6" answers a question a waiter
               is actually asking; "6 مقاعد" answers one nobody asked. -->
          <span v-if="isBusy(table)">{{ table.seated_count }} من {{ table.seats }}</span>
          <span v-else>{{ table.seats }} مقاعد</span>
        </span>

        <span v-if="isBusy(table)" class="table-state">
          <span v-if="table.order_count" class="table-due">{{ money(table.total_due) }}</span>
          <span v-else class="table-noorder">لم يطلب بعد</span>
          <span v-if="table.seated_minutes !== null" class="table-time">
            {{ minutes(table.seated_minutes) }}
          </span>
        </span>
        <span v-else class="table-free">متاحة</span>

        <span v-if="table.waiter" class="table-waiter">{{ table.waiter }}</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.tables-screen {
  padding: 1rem 1.25rem 2rem;
}

.tables-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 1rem;
}
.tables-title {
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--ink);
}
.tables-sub {
  margin-top: 0.15rem;
  font-size: 0.85rem;
  color: var(--ink-muted);
  min-height: 1.2em;
}

.area-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin-bottom: 0.9rem;
}
.area-tab {
  padding: 0.4rem 0.9rem;
  border-radius: 999px;
  border: 1px solid var(--border);
  font-size: 0.85rem;
  color: var(--ink-muted);
  background: var(--surface);
}
.area-tab-on {
  background: var(--brand-700);
  border-color: var(--brand-700);
  color: #fff;
}

/*
  Big targets on purpose. This is used standing up, at speed, sometimes with a
  tray in the other hand — 6.5rem is comfortably past the 44px minimum and the
  auto-fill keeps a 10" tablet and a 24" till on the same layout rules.
*/
.tables-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(8.5rem, 1fr));
  gap: 0.7rem;
}

.table-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.2rem;
  min-height: 6.5rem;
  padding: 0.6rem;
  border-radius: 0.85rem;
  border: 1px solid var(--border);
  background: var(--surface);
  box-shadow: var(--shadow-xs);
  transition:
    transform var(--duration-fast) var(--ease-out),
    box-shadow var(--duration-fast) var(--ease-out);
}
.table-card:active {
  transform: scale(0.97);
}
.table-card:hover,
.table-card:focus-visible {
  box-shadow: var(--shadow-md);
}

/*
  Occupancy is carried by fill AND by the words inside, never by colour alone —
  the room is read at a glance by people who may not separate the two hues, and
  a busy table already says "متاحة" or a price.
*/
.table-busy {
  background: var(--brand-50);
  border-color: var(--brand-700);
}
.table-neglected {
  border-color: var(--warning);
  border-width: 2px;
}

.table-number {
  font-size: 1.6rem;
  font-weight: 700;
  line-height: 1;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}
.table-meta {
  font-size: 0.72rem;
  color: var(--ink-faint);
}
.table-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.1rem;
}
.table-due {
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--brand-700);
  font-variant-numeric: tabular-nums;
}
.table-noorder {
  font-size: 0.75rem;
  color: var(--ink-muted);
}
.table-time {
  font-size: 0.7rem;
  color: var(--ink-faint);
  font-variant-numeric: tabular-nums;
}
.table-free {
  font-size: 0.8rem;
  color: var(--ink-muted);
}
.table-waiter {
  font-size: 0.68rem;
  color: var(--ink-faint);
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tables-empty {
  padding: 3rem 1rem;
  text-align: center;
  color: var(--ink-muted);
}
.tables-empty-hint {
  margin-top: 0.4rem;
  font-size: 0.8rem;
  color: var(--ink-faint);
}
</style>
