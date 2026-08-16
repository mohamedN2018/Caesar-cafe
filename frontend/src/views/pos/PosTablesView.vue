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
    // `/floor/status/` answers with a bare ARRAY of tables — the client already
    // strips the `{success, data}` envelope, so `data` IS the list.
    //
    // This read `payload.tables`, which is `undefined` against the real server:
    // the room came back full and rendered as "لا توجد طاولات معرَّفة". The unit
    // tests agreed with the mistake because they mocked the shape the code
    // expected instead of the shape the server sends, so a whole screen was
    // empty in production and green in CI. `FloorPlanView` had it right.
    const payload = await api.get<FloorTable[]>('/floor/status/')
    tables.value = Array.isArray(payload) ? payload : []
    error.value = ''

    // Open on ONE room, not on all of them.
    //
    // Every room shares the screen when "الكل" is chosen, and a screen split
    // three ways is three plans too small to read. A waiter works one room at a
    // time; that room should get the whole till, and seeing the rest is a tap.
    //
    // Only until somebody chooses for themselves. Without the flag, picking
    // "الكل" would snap back to one room on the next ten-second refresh — the
    // screen overruling the person using it, every ten seconds, silently.
    if (!areaChosen.value && areas.value.length > 1) {
      area.value = areas.value[0]
    }
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

/** Whether the person has picked a room themselves. See `load`. */
const areaChosen = ref(false)

function chooseArea(name: string) {
  areaChosen.value = true
  area.value = name
  picked.value = null
}

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
/**
 * How much of the plan an EMPTY row or column is worth.
 *
 * A room's coordinates are sparse — tables cluster along the walls and around
 * the door, and whole rows in the middle hold nothing. Drawing every track at
 * full size spent most of the screen on floor nobody sits on, and the tables
 * were smaller for it.
 *
 * Collapsed rather than removed. A gap between two clusters is the aisle, and
 * deleting it would make this plan disagree with the admin's drawing of the same
 * room — which is the one thing a second view of a floor must never do. At a
 * third of a track it still reads as a gap, and the tables get the rest.
 */
const GAP_TRACK = 0.34

/** `1fr` where something sits, a sliver where nothing does. */
function tracks(count: number, used: Set<number>): { template: string; weight: number } {
  const parts: string[] = []
  let weight = 0
  for (let i = 0; i < count; i++) {
    const full = used.has(i)
    parts.push(full ? '1fr' : `${GAP_TRACK}fr`)
    weight += full ? 1 : GAP_TRACK
  }
  return { template: parts.join(' '), weight: Math.max(weight, 1) }
}

const plan = computed(() => {
  const rooms = new Map<string, FloorTable[]>()
  for (const table of visible.value) {
    const list = rooms.get(table.area) ?? []
    list.push(table)
    rooms.set(table.area, list)
  }

  return [...rooms.entries()].map(([name, list]) => {
    // +1 because the coordinates are zero-based cell indices, not counts.
    const cols = Math.max(...list.map((t) => t.pos_x + t.span_x)) + 1
    const rows = Math.max(...list.map((t) => t.pos_y + t.span_y)) + 1

    const usedCols = new Set<number>()
    const usedRows = new Set<number>()
    for (const t of list) {
      for (let x = 0; x < t.span_x; x++) usedCols.add(t.pos_x + x)
      for (let y = 0; y < t.span_y; y++) usedRows.add(t.pos_y + y)
    }

    const col = tracks(cols, usedCols)
    const row = tracks(rows, usedRows)

    return {
      name,
      cols,
      rows,
      colTemplate: col.template,
      rowTemplate: row.template,
      // The proportions the room is drawn at, and the divisor the type scales
      // from — both in track-widths, so a collapsed gap costs neither.
      colWeight: Math.round(col.weight * 100) / 100,
      rowWeight: Math.round(row.weight * 100) / 100,
      // The room's proportions, as one number the fit rule can divide by.
      ratio: Math.round((col.weight / row.weight) * 1000) / 1000,
      tables: [...list].sort((a, b) => a.number.localeCompare(b.number, 'ar', { numeric: true })),
    }
  })
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

/**
 * Free tables — the number a waiter is actually looking for.
 *
 * The header counted occupied, which is the same arithmetic seen from the wrong
 * end: somebody standing at the door with a party of four is not counting the
 * tables they cannot seat them at.
 */
const free = computed(() => tables.value.length - occupied.value)

/**
 * The table somebody just tapped, if any.
 *
 * One sheet, two jobs, because the two questions asked at this moment are the
 * same shape. On a FREE table it asks how many sat down — a single tap that the
 * old path skipped, and skipping it made every session claim one guest, so the
 * board reported "1 من 4" for a party of four and the room read as emptier than
 * it was. On an OCCUPIED table it shows what is already on the bill first,
 * because adding to somebody else's tab is discovered at closing, when there is
 * no way left to work out which items were whose.
 */
const picked = ref<FloorTable | null>(null)
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

/** Tapping a table opens the sheet; the sheet decides what it is asking. */
function pick(table: FloorTable) {
  picked.value = table
}

/**
 * How many people to offer, for a free table.
 *
 * Capped at the seats the table actually has plus one, because parties do squeeze
 * an extra chair in and a picker that cannot express what happened sends the
 * waiter to the wrong number rather than to the right one.
 */
const guestChoices = computed(() => {
  const seats = picked.value?.seats ?? 4
  return Array.from({ length: Math.max(seats, 1) + 1 }, (_, i) => i + 1)
})

/**
 * Go to the order screen carrying the table with it.
 *
 * Everything travels in the query rather than in this board's memory, so a reload
 * — or a second device opening the same URL — lands on the same table instead of
 * on a blank order that belongs to nobody. `guests` is only sent for a table with
 * no session yet: it is what opens the session, and passing it for an occupied
 * table would silently rewrite a party somebody already counted.
 */
function open(table: FloorTable, guests?: number) {
  picked.value = null
  router.push({
    name: 'pos-order',
    query: {
      table: table.table_id,
      session: table.session_id ?? undefined,
      number: table.number,
      guests: table.session_id ? undefined : String(guests ?? 1),
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
    <!--
      One bar, not a page header.

      This screen sits inside a shell that is exactly one viewport tall and does
      not scroll. Every row spent on chrome is a row taken off the room, so the
      counts, the rooms and the one action share a single line and the plan gets
      all the rest.
    -->
    <header class="tables-bar">
      <div class="bar-counts" :class="{ 'is-loading': loading }">
        <!-- Free first: it is the number somebody is looking for. -->
        <span class="count count-free">
          <b>{{ loading ? '—' : free }}</b>
          فاضية
        </span>
        <span class="count count-busy">
          <b>{{ loading ? '—' : occupied }}</b>
          مشغولة
        </span>
        <span class="count count-owed">
          <b>{{ loading ? '—' : money(owed) }}</b>
          مستحق
        </span>
      </div>

      <!-- Rooms, on the same line. Only when there is more than one to pick. -->
      <nav v-if="areas.length > 1" class="area-tabs">
        <button
          type="button"
          class="area-tab"
          :class="{ 'area-tab-on': area === '' }"
          @click="chooseArea('')"
        >
          الكل
        </button>
        <button
          v-for="name in areas"
          :key="name"
          type="button"
          class="area-tab"
          :class="{ 'area-tab-on': area === name }"
          @click="chooseArea(name)"
        >
          {{ name }}
        </button>
      </nav>

      <!--
        Quick sell, on the floor rather than only in the tab bar.

        A counter sale happens while somebody is standing at this screen — the
        till is not always table service, and making them leave the room to
        reach the menu is a step that exists for no reason.
      -->
      <UiButton class="bar-sell" variant="secondary" @click="walkIn">بيع سريع</UiButton>
    </header>

    <UiAlert v-if="error" tone="warning" class="tables-alert">{{ error }}</UiAlert>

    <div v-if="loading" class="tables-grid">
      <UiSkeleton v-for="n in 12" :key="n" class="skeleton-table" />
    </div>

    <div v-else-if="!tables.length" class="tables-empty">
      <p>لا توجد طاولات معرَّفة.</p>
      <p class="tables-empty-hint">تُضاف من شاشة «الصالة» في الإدارة.</p>
    </div>

    <!--
      A plan per room, side by side, all of it inside one screen.

      Rooms used to stack, so a second area pushed the first off the bottom of a
      shell that does not scroll — the tables were there and simply could not be
      reached. Laid out in a row instead, each room taking width in proportion to
      how wide the room actually is, so a nine-column hall gets more of the screen
      than a three-column terrace and both stay on it.
    -->
    <div v-else class="plan-rooms">
      <section
        v-for="room in plan"
        :key="room.name"
        class="plan-room"
        :style="{ flexGrow: room.cols }"
      >
        <h2 v-if="plan.length > 1" class="plan-room-name">{{ room.name }}</h2>

        <!--
          The room keeps its own proportions and shrinks to whatever is left.

          `aspect-ratio` from the real column and row counts, then capped at 100%
          of both axes: the plan is as large as it can be while still fitting,
          and the cells stay square, so a round table is a circle rather than an
          ellipse stretched to fill a slot.
        -->
        <div class="plan-fit">
          <div
            class="plan-grid"
            :style="{
              gridTemplateColumns: room.colTemplate,
              gridTemplateRows: room.rowTemplate,
              '--ratio': room.ratio,
              '--cols': room.colWeight,
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
              @click="pick(table)"
            >
              <!-- The shape carries the rotation; the label never does. -->
              <span class="table-shape" :style="shapeStyle(table)" aria-hidden="true" />

              <span class="table-face">
                <span class="table-number">{{ table.number }}</span>

                <!--
                  Two lines under the number, never six.

                  The cell used to carry the order count, the sitting time and the
                  waiter as well, hidden below 1280px and cramped above it. All of
                  that now lives one tap away in the sheet, where there is room to
                  read it — and in the title, which is what a screen reader speaks.
                -->
                <span class="table-meta">
                  <template v-if="isBusy(table)">{{ table.seated_count }} من {{ table.seats }}</template>
                  <template v-else>{{ table.seats }} مقاعد</template>
                </span>

                <span v-if="isBusy(table)" class="table-state">
                  <span v-if="table.order_count" class="table-due">{{ money(table.total_due) }}</span>
                  <span v-else class="table-noorder">لم يطلب بعد</span>
                </span>
                <span v-else class="table-free">متاحة</span>
              </span>
            </button>
          </div>
        </div>
      </section>
    </div>
    <!--
      One sheet, two questions — whichever the tapped table raises.

      FREE: how many sat down. One tap, and it is the tap that was missing: every
      session used to open claiming a single guest, so a party of four showed as
      "1 من 4" and the room read emptier than it was.

      OCCUPIED: what is already on the bill, before anything is added to it. The
      amount due, how long they have been sitting and who is serving them are the
      three facts that say whether this is the right table — and getting that
      wrong writes a round of coffees onto somebody else's tab.
    -->
    <div v-if="picked" class="picked-scrim" @click="picked = null">
      <div
        class="picked"
        role="dialog"
        aria-modal="true"
        :aria-label="`طاولة ${picked.number}`"
        @click.stop
      >
        <header class="picked-head">
          <h2 class="picked-title">طاولة {{ picked.number }}</h2>
          <span class="picked-state" :class="isBusy(picked) ? 'is-busy' : 'is-free'">
            {{ isBusy(picked) ? 'عليها ناس' : 'فاضية' }}
          </span>
        </header>
        <!--
          Separate elements, not one interpolated string.

          Written as `{{ area }} · {{ seats }} مقاعد` the bidi algorithm ran the
          interpunct up against the number and "6 مقاعد" rendered as "60 مقاعد" —
          a seat count that is wrong by a factor of ten, on the screen where
          somebody decides whether a party fits.
        -->
        <p class="picked-where">
          <span>{{ picked.area }}</span>
          <span class="picked-dot" aria-hidden="true">·</span>
          <span>{{ picked.seats }} مقاعد</span>
        </p>

        <!-- Free: the one thing worth asking before the menu. -->
        <template v-if="!isBusy(picked)">
          <p class="picked-ask">كم شخص؟</p>
          <div class="guest-choices">
            <button
              v-for="n in guestChoices"
              :key="n"
              type="button"
              class="guest-choice"
              @click="open(picked, n)"
            >
              {{ n }}
            </button>
          </div>
          <UiButton variant="ghost" size="lg" block @click="picked = null">إلغاء</UiButton>
        </template>

        <!-- Occupied: what is on it now. -->
        <template v-else>
          <dl class="picked-facts">
            <div>
              <dt>الجالسون</dt>
              <dd>{{ picked.seated_count }} من {{ picked.seats }}</dd>
            </div>
            <div v-if="picked.seated_minutes !== null">
              <dt>منذ</dt>
              <dd>{{ minutes(picked.seated_minutes) }}</dd>
            </div>
            <div>
              <dt>الطلبات</dt>
              <dd>{{ picked.order_count || 'لم تطلب بعد' }}</dd>
            </div>
            <div>
              <dt>المستحق</dt>
              <dd class="picked-due">{{ money(picked.total_due) }}</dd>
            </div>
            <div v-if="picked.waiter">
              <dt>الويتر</dt>
              <dd>{{ picked.waiter }}</dd>
            </div>
          </dl>

          <div class="picked-actions">
            <UiButton size="lg" block @click="open(picked)">
              {{ picked.order_count ? 'إضافة إلى هذه الطاولة' : 'تسجيل طلب' }}
            </UiButton>
            <UiButton variant="ghost" size="lg" block @click="picked = null">إلغاء</UiButton>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
/*
  The whole room, inside one screen, with nothing to scroll.

  `PosLayout` is `100dvh` with `overflow: hidden` and lays its body out as a
  flex row — and this screen never claimed that slot. As a plain block it took
  its width from its content and its height from the plan, so the room was cut
  off by a shell that hides what it cannot fit: the tables were rendered, and
  simply not reachable.

  Claiming it is `flex: 1` plus `min-height/min-width: 0`, without which a flex
  item refuses to shrink below its content and the overflow comes straight back.
*/
.tables-screen {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  padding: 0.7rem 0.9rem 0.9rem;
  overflow: hidden;
}

/* One line: what the room is doing, which room, and the one action. */
.tables-bar {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}
.bar-counts {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.count {
  display: inline-flex;
  align-items: baseline;
  gap: 0.3rem;
  padding: 0.3rem 0.7rem;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
  font-size: 0.78rem;
  color: var(--ink-muted);
  white-space: nowrap;
}
.count b {
  font-size: 1.05rem;
  font-weight: 800;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}
/* Free is the number somebody is looking for, so it is the one that carries. */
.count-free {
  border-color: var(--brand-700);
  background: var(--brand-50);
}
.count-free b {
  color: var(--brand-700);
}
.count-busy b {
  color: var(--gold-700);
}
.bar-sell {
  margin-inline-start: auto;
}
.tables-alert {
  flex: 0 0 auto;
}

/*
  A sheet rather than a corner popover: this is used standing up, and a small
  target that has to be dismissed accurately is the wrong shape for a thumb.
*/
.picked-scrim {
  position: fixed;
  inset: 0;
  z-index: 40;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  background: var(--scrim, rgba(0, 0, 0, 0.45));
}
.picked {
  width: min(28rem, 100%);
  padding: 1.25rem;
  border-radius: 1rem 1rem 0 0;
  background: var(--surface);
  box-shadow: var(--shadow-xl);
}
.picked-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}
.picked-title {
  font-size: 1.35rem;
  font-weight: 700;
  color: var(--ink);
}
/* The answer to "is anyone on it", stated rather than inferred from a colour. */
.picked-state {
  padding: 0.2rem 0.6rem;
  border-radius: 999px;
  font-size: 0.8rem;
  font-weight: 600;
}
.picked-state.is-busy {
  background: var(--gold-100);
  color: var(--gold-700);
}
.picked-state.is-free {
  background: var(--brand-50);
  color: var(--brand-700);
}
.picked-where {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin-top: 0.25rem;
  font-size: 0.85rem;
  color: var(--ink-muted);
}
.picked-dot {
  color: var(--ink-faint);
}
.picked-ask {
  margin: 1rem 0 0.5rem;
  font-size: 0.95rem;
  color: var(--ink);
}
/*
  Numbers, not a stepper. A party size is known the moment it walks in, so one
  tap on the right number beats holding "+" four times.
*/
/*
  Chips of one fixed size, wrapping.

  Two versions of this were wrong in ways only a render showed. A grid left the
  seventh chip alone at one-sixth width; letting them grow instead turned that
  same lone chip into a full-width banner that read as a different control
  entirely. Every chip is the same button, so every chip is the same size, and
  the row that does not fill simply does not fill.
*/
.guest-choices {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-bottom: 1rem;
}
.guest-choice {
  flex: 0 0 3.5rem;
  min-height: 3.25rem;
  border: 1px solid var(--border-strong);
  border-radius: 0.65rem;
  background: var(--surface);
  font-size: 1.15rem;
  font-weight: 700;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}
.guest-choice:hover {
  border-color: var(--brand-700);
  background: var(--brand-50);
}
.guest-choice:focus-visible {
  outline: 3px solid var(--focus-ring);
  outline-offset: 2px;
}
.picked-facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(7rem, 1fr));
  gap: 0.75rem;
  margin: 1rem 0 1.25rem;
}
.picked-facts dt {
  font-size: 0.75rem;
  color: var(--ink-faint);
}
.picked-facts dd {
  font-size: 1rem;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}
.picked-due {
  font-weight: 700;
  color: var(--brand-700);
}
.picked-actions {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.area-tabs {
  display: flex;
  gap: 0.35rem;
}
.area-tab {
  padding: 0.35rem 0.85rem;
  border-radius: 999px;
  border: 1px solid var(--border);
  font-size: 0.82rem;
  color: var(--ink-muted);
  background: var(--surface);
  white-space: nowrap;
}
.area-tab-on {
  background: var(--brand-700);
  border-color: var(--brand-700);
  color: #fff;
}

/*
  The rooms, side by side, sharing whatever height is left.

  `min-height: 0` on both is what actually makes this fit: a flex item defaults
  to `min-height: auto` and refuses to shrink below its content, which is how a
  plan that "fits" still ends up an inch past the bottom of the screen.
*/
.plan-rooms {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  gap: 0.9rem;
}
.plan-room {
  flex: 1 1 0;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  padding: 0.6rem;
  border-radius: 0.9rem;
  background: var(--surface);
  border: 1px solid var(--border);
}
.plan-room-name {
  flex: 0 0 auto;
  font-size: 0.8rem;
  font-weight: 700;
  color: var(--ink-muted);
  text-align: center;
}
/*
  The box the room is fitted into: whatever is left after the bar and the name.

  A size container, so the plan inside can be sized against BOTH of its axes at
  once. That is the whole difficulty — `aspect-ratio` plus `height: 100%` plus
  `max-width: 100%` looks like it fits a box and does not: an explicit height
  wins over the ratio, so when width is the binding constraint the cells stretch
  instead of the plan shrinking. Two rooms side by side came out 93px wide and
  171px tall, which is not what a table looks like from across a room.
*/
.plan-fit {
  flex: 1 1 auto;
  min-height: 0;
  container-type: size;
  display: flex;
  align-items: center;
  justify-content: center;
}

/*
  The room itself, at the largest size that still fits both ways.

  `aspect-ratio` from the real column and row counts keeps the cells SQUARE, so
  a round table is a circle and not an ellipse stretched into its slot. Height
  drives the size; `max-width: 100%` clamps it on a narrow screen and the height
  follows the ratio back down. Nothing scrolls, in either direction.
*/
.plan-grid {
  display: grid;
  /* Tracks and `--ratio` come from the room — see `tracks()` and `plan`. */
  gap: 0.35rem;
  /*
    The largest box of the room's own proportions that fits inside `.plan-fit`,
    measured against both axes. Whichever runs out first decides the size, and
    the cells stay square either way.
  */
  width: min(100cqw, calc(100cqh * var(--ratio)));
  height: min(100cqh, calc(100cqw / var(--ratio)));
  max-width: 100%;
  max-height: 100%;
  /*
    A container, so the tables can size their text from the width of a CELL
    rather than from the viewport. A nine-column hall and a three-column terrace
    are on the same screen at the same time; one font size cannot serve both.
  */
  container-type: inline-size;
}

/* The loading state, shaped like the room it is standing in for. */
.tables-grid {
  flex: 1 1 auto;
  min-height: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(6rem, 1fr));
  grid-auto-rows: 1fr;
  gap: 0.35rem;
}
.skeleton-table {
  height: 100%;
  border-radius: 0.7rem;
}

/*
  A cell in the room. The card itself is a transparent positioning box; the
  drawn table is `.table-shape` beneath the text, so rotation can apply to the
  furniture without tilting the label.

  `--cell` is the width one column actually gets. Everything inside scales from
  it, which is what lets the same markup read correctly whether a table is 60px
  on a shared screen or 160px on a till of its own.
*/
.table-card {
  --cell: calc(100cqw / var(--cols));
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 0;
  padding: 0;
  background: transparent;
  border: 0;
  transition: transform var(--duration-fast) var(--ease-out);
}
.table-shape {
  position: absolute;
  inset: 0;
  border: 1px solid var(--border-strong);
  background: linear-gradient(160deg, var(--surface), var(--surface-muted, var(--surface)));
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
  line-height: 1.15;
  text-align: center;
  padding: 0.1rem;
  max-width: 100%;
  overflow: hidden;
}

/* The five shapes the floor module defines. */
.shape-round .table-shape {
  border-radius: 50%;
}
.shape-square .table-shape {
  border-radius: clamp(0.3rem, calc(var(--cell) * 0.12), 0.8rem);
}
.shape-rect .table-shape {
  border-radius: clamp(0.25rem, calc(var(--cell) * 0.08), 0.6rem);
}
/*
  A booth has a back: a bench along one edge, softer corners at the open side.

  Fixed radii, not percentages. A percentage radius is measured against each
  axis separately, so on a table spanning two cells it drew as a shallow bowl
  that looked like nothing in any café.
*/
.shape-booth .table-shape {
  border-radius: 0.3rem 0.3rem 1.1rem 1.1rem;
  border-top: 0.3rem solid var(--gold-500);
}
/*
  A bar seat is a stool at a counter, not a table. The counter is the gold edge
  it is pushed against; the stool sits below it.

  The shape must still hold the label. The first version offset it to 32% from
  the top, so the number floated ABOVE the drawn stool and straddled its edge —
  visible the moment it was rendered in a real browser, and invisible to every
  test, because happy-dom has no layout to be wrong about.

  This shape was missing entirely on the first pass — the enum has five and I
  wrote four, so the seeded BAR seats drew as plain rectangles.
  `test_floor_shapes.py` fails if a sixth is ever added without a rule.
*/
.shape-bar .table-shape {
  inset: 14% 10% 4%;
  border-radius: 0.25rem 0.25rem 0.9rem 0.9rem;
  border-top: 0.3rem solid var(--gold-600);
}
/* The label follows the stool down, so it reads as being ON it. */
.shape-bar .table-face {
  transform: translateY(5%);
}

.table-card:active {
  transform: scale(0.95);
}
.table-card:hover .table-shape {
  border-color: var(--brand-700);
  box-shadow: var(--shadow-md);
}
.table-card:focus-visible {
  outline: 3px solid var(--focus-ring);
  outline-offset: 1px;
  border-radius: 0.5rem;
}

/*
  Occupancy is carried by fill AND by the words inside, never by colour alone —
  the room is read at a glance by people who may not separate the two hues, and
  a table already says "متاحة" or shows a price.
*/
.table-busy .table-shape {
  background: linear-gradient(160deg, var(--brand-50), #fff);
  border-color: var(--brand-700);
  border-width: 2px;
}
/* Long-seated with nothing ordered: the one state worth interrupting for. */
.table-neglected .table-shape {
  border-color: var(--warning);
  border-width: 2px;
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--warning) 28%, transparent);
}

/*
  Type scaled from the CELL, floored at a size that is still readable across a
  room. Below the floor the line is dropped rather than shrunk into decoration:
  an unreadable label is not information, it is noise wearing the shape of it.
*/
.table-number {
  font-size: clamp(0.9rem, calc(var(--cell) * 0.3), 2.2rem);
  font-weight: 800;
  line-height: 1;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}
.table-meta,
.table-free,
.table-noorder {
  font-size: clamp(0.55rem, calc(var(--cell) * 0.13), 0.85rem);
  color: var(--ink-muted);
  white-space: nowrap;
}
.table-due {
  font-size: clamp(0.6rem, calc(var(--cell) * 0.15), 1rem);
  font-weight: 700;
  color: var(--brand-700);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
/* Under about 68px a cell holds a number and one line, so it holds those. */
@container (max-width: 620px) {
  .table-meta {
    display: none;
  }
}

.tables-empty {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  color: var(--ink-muted);
}
.tables-empty-hint {
  margin-top: 0.4rem;
  font-size: 0.8rem;
  color: var(--ink-faint);
}

/*
  Narrow and showing every room at once is the one case where side-by-side stops
  paying: two nine-column halls on a 10" tablet are two grids of slivers.

  Stacked, they share the height instead of the width — still one screen, still
  nothing to scroll, and each room keeps its proportions because the fit box does
  the same job on either axis. Picking a single room from the tabs is still the
  better way to work, and it is one tap away.
*/
@media (max-width: 900px) {
  .plan-rooms {
    flex-direction: column;
  }
}
</style>
