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

/**
 * The room, drawn where it actually is.
 *
 * `pos_x`, `pos_y`, `span_x`, `span_y`, `shape` and `rotation` have been in the
 * payload — and under a CI guard — since the floor module was built, and nothing
 * had ever drawn them. Both this screen and the admin's rendered a sorted LIST,
 * which is a different thing wearing the same data: a list tells you a table
 * exists, a plan tells you which one the customer is waving from.
 *
 * Grouped per area because a terrace and an inside room are two rooms, and
 * overlaying their coordinates would put table 11 on top of table 1.
 */
const plan = computed(() => {
  const rooms = new Map<string, FloorTable[]>()
  for (const table of visible.value) {
    const list = rooms.get(table.area) ?? []
    list.push(table)
    rooms.set(table.area, list)
  }
  return [...rooms.entries()].map(([name, list]) => ({
    name,
    // +1 because the coordinates are zero-based cell indices, not counts.
    cols: Math.max(...list.map((t) => t.pos_x + t.span_x)) + 1,
    rows: Math.max(...list.map((t) => t.pos_y + t.span_y)) + 1,
    tables: [...list].sort((a, b) =>
      a.number.localeCompare(b.number, 'ar', { numeric: true }),
    ),
  }))
})

/**
 * Where a table sits, as grid lines.
 *
 * CSS grid is 1-based and the data is 0-based, hence the +1. `rotation` is
 * applied to the shape only — never to the label — because a number rotated 15
 * degrees is a number somebody has to tilt their head to read, and the whole
 * point of the plan is reading it at a glance.
 */
function place(table: FloorTable) {
  return {
    gridColumn: `${table.pos_x + 1} / span ${table.span_x}`,
    gridRow: `${table.pos_y + 1} / span ${table.span_y}`,
  }
}

function shapeStyle(table: FloorTable) {
  return {
    transform: table.rotation ? `rotate(${table.rotation}deg)` : undefined,
  }
}

/** ROUND · SQUARE · RECT · BOOTH — the four the floor module defines. */
function shapeClass(table: FloorTable): string {
  return `shape-${(table.shape || 'SQUARE').toLowerCase()}`
}

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
 * Everything about a table, in one line.
 *
 * The plan shows three facts per table because that is what fits in a cell the
 * size of a table; the rest is here, on hover and to a screen reader, so the
 * detail is never actually lost — only kept out of a space that cannot hold it.
 */
function summary(table: FloorTable): string {
  const bits = [`طاولة ${table.number}`, table.area, `${table.seats} مقاعد`]
  if (isBusy(table)) {
    bits.push(`${table.seated_count} جالسين`)
    bits.push(table.order_count ? `${table.order_count} طلب · ${money(table.total_due)}` : 'لم يطلب بعد')
    if (table.seated_minutes !== null) bits.push(`منذ ${minutes(table.seated_minutes)}`)
    if (table.waiter) bits.push(table.waiter)
  } else {
    bits.push('متاحة')
  }
  return bits.join(' · ')
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

    <!--
      A plan per room. The grid is the room; a table occupies the cells it was
      placed in on the admin's floor plan, so moving a table there moves it here.
    -->
    <div v-else class="plan-rooms">
      <section v-for="room in plan" :key="room.name" class="plan-room">
        <h2 v-if="plan.length > 1" class="plan-room-name">{{ room.name }}</h2>

        <div
          class="plan-grid"
          :style="{
            gridTemplateColumns: `repeat(${room.cols}, minmax(5.5rem, 1fr))`,
            gridTemplateRows: `repeat(${room.rows}, minmax(5.5rem, auto))`,
          }"
        >
          <button
            v-for="table in room.tables"
            :key="table.table_id"
            type="button"
            class="table-card"
            :class="[
              shapeClass(table),
              { 'table-busy': isBusy(table), 'table-neglected': isNeglected(table) },
            ]"
            :style="place(table)"
            :title="summary(table)"
            @click="open(table)"
          >
            <!-- The shape carries the rotation; the label never does. -->
            <span class="table-shape" :style="shapeStyle(table)" aria-hidden="true" />

            <span class="table-face">
              <span class="table-number">{{ table.number }}</span>

              <span class="table-meta">
                <span v-if="isBusy(table)">{{ table.seated_count }} من {{ table.seats }}</span>
                <span v-else>{{ table.seats }} مقاعد</span>
              </span>

              <span v-if="isBusy(table)" class="table-state">
                <span v-if="table.order_count" class="table-due">{{ money(table.total_due) }}</span>
                <span v-else class="table-noorder">لم يطلب بعد</span>
                <span v-if="table.order_count" class="table-orders table-detail">
                  {{ table.order_count }} طلب
                </span>
                <span v-if="table.seated_minutes !== null" class="table-time table-detail">
                  {{ minutes(table.seated_minutes) }}
                </span>
              </span>
              <span v-else class="table-free">متاحة</span>

              <span v-if="table.waiter" class="table-waiter table-detail">{{ table.waiter }}</span>
            </span>
          </button>
        </div>
      </section>
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
.plan-rooms {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}
.plan-room-name {
  margin-bottom: 0.5rem;
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--ink-muted);
}
/*
  The room. `overflow-x: auto` because a wide terrace on a 10" tablet has to
  scroll sideways rather than squash the tables into unreadable slivers — the
  plan is only useful while it still resembles the room.
*/
.plan-grid {
  display: grid;
  gap: 0.6rem;
  overflow-x: auto;
  padding-bottom: 0.3rem;
}

/* The tables-grid rule is kept for the loading skeletons only. */
.tables-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(8.5rem, 1fr));
  gap: 0.7rem;
}

/*
  A cell in the room. The card itself is a transparent positioning box; the
  drawn table is `.table-shape` beneath the text, so rotation can apply to the
  furniture without tilting the label.
*/
.table-card {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 5.5rem;
  padding: 0.4rem;
  background: transparent;
  border: 0;
  transition: transform var(--duration-fast) var(--ease-out);
}
.table-shape {
  position: absolute;
  inset: 0.25rem;
  border: 1px solid var(--border);
  background: var(--surface);
  box-shadow: var(--shadow-xs);
  transition:
    box-shadow var(--duration-fast) var(--ease-out),
    background-color var(--duration-fast) var(--ease-out),
    border-color var(--duration-fast) var(--ease-out);
}
.table-face {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.1rem;
  text-align: center;
}

/* The four shapes the floor module defines. */
.shape-round .table-shape {
  border-radius: 50%;
}
.shape-square .table-shape {
  border-radius: 0.5rem;
}
.shape-rect .table-shape {
  border-radius: 0.4rem;
}
/* A booth has a back: one flat edge, three soft ones. */
.shape-booth .table-shape {
  border-radius: 0.5rem 0.5rem 1.4rem 1.4rem;
  border-top-width: 3px;
  border-top-color: var(--gold-500);
}
/*
  A bar seat is a stool at a counter, not a table. Drawn as a narrow rounded
  slab against one edge so it reads as "along the bar" rather than "a small
  square table that happens to be there".

  This was missing on the first pass — the enum has five shapes and I wrote four,
  so the three BAR seats in the seeded room rendered as unstyled rectangles. The
  test below now fails if a sixth is ever added without a rule.
*/
.shape-bar .table-shape {
  inset: 1.1rem 0.25rem 0.25rem;
  border-radius: 0.4rem;
  border-top: 3px solid var(--gold-600);
}
.table-card:active {
  transform: scale(0.96);
}
.table-card:hover .table-shape,
.table-card:focus-visible .table-shape {
  box-shadow: var(--shadow-md);
}

/*
  Occupancy is carried by fill AND by the words inside, never by colour alone —
  the room is read at a glance by people who may not separate the two hues, and
  a busy table already says "متاحة" or a price.
*/
.table-busy .table-shape {
  background: var(--brand-50);
  border-color: var(--brand-700);
}
.table-neglected .table-shape {
  border-color: var(--warning);
  border-width: 2px;
}

.table-number {
  font-size: 1.45rem;
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
.table-orders {
  font-size: 0.7rem;
  color: var(--ink-muted);
}

/*
  Secondary detail, hidden until the screen is wide enough to hold it.

  `FloorPlanView` recorded why a drawn room was removed once before: the tables
  ended up the size the layout dictated rather than the size their information
  needed. The inside room is nine columns wide, so on a 10" tablet each table is
  about 88px — room for three lines, not six. These are the other three.

  Nothing is lost: `summary()` puts the whole table in the title, which is also
  what a screen reader reads.
*/
.table-detail {
  display: none;
}
@media (min-width: 1280px) {
  .table-detail {
    display: block;
  }
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
