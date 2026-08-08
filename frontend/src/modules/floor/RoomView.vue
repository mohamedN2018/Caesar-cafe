<script setup lang="ts">
/**
 * The room, seen from above and slightly in front.
 *
 * Built from CSS 3D transforms rather than a WebGL library, and that is a real
 * decision rather than a shortcut. A tilted plane with elements standing on it
 * is all the depth this needs; three.js would add ~600 kB to a bundle a cafe
 * loads over Egyptian mobile data, and it would put every table behind a canvas
 * — unselectable, untabbable, invisible to a screen reader. Here a table is a
 * `<button>` that happens to be tilted.
 *
 * What the view is for: **seating a walk-in and finding a bill.** So it draws
 * the two facts a status grid cannot — the shape of the furniture, and how many
 * of its chairs actually have somebody in them. A four-top with two people is
 * not "occupied"; it is half a table, and only this screen says so.
 *
 * The tilt defaults to 52°. Steeper looks better in a screenshot and makes the
 * far side of the room unreadable, which is the half a waiter usually asks about.
 */
import { computed, ref } from 'vue'

import { footprint, fullness, seatsFor, type Seat, type TableShape } from './geometry'

export interface RoomTable {
  table_id: string
  number: string
  seats: number
  seated_count: number
  status: string
  pos_x: number
  pos_y: number
  shape: TableShape
  span_x: number
  span_y: number
  rotation: number
  order_count?: number
  total_due?: string
  waiter?: string | null
  seated_minutes?: number | null
}

export interface RoomStation {
  id: string
  name_ar: string
  open_tickets: number
  late_tickets: number
}

const props = withDefaults(
  defineProps<{
    tables: RoomTable[]
    stations?: RoomStation[]
    /** Editing moves furniture. Off, the room is a live board. */
    editable?: boolean
    selectedId?: string | null
    /** Grid cells across and down. The Desktop renders the same grid. */
    columns?: number
    rows?: number
  }>(),
  { stations: () => [], editable: false, selectedId: null, columns: 10, rows: 8 },
)

const emit = defineEmits<{
  (event: 'select', table: RoomTable): void
  (event: 'move', payload: { id: string; x: number; y: number }): void
}>()

/** Pixels per grid cell. */
const CELL = 86

const STATE_LABEL: Record<string, string> = {
  free: 'متاحة',
  light: 'جالسون قليل',
  busy: 'شبه ممتلئة',
  full: 'ممتلئة',
  cleaning: 'تحتاج تنظيف',
}

const tilt = ref(52)
const zoom = ref(1)
const partySize = ref(0)
const dragging = ref<string | null>(null)

const width = computed(() => props.columns * CELL)
const height = computed(() => props.rows * CELL)

const seatedTotal = computed(() => props.tables.reduce((sum, t) => sum + t.seated_count, 0))
const seatsTotal = computed(() => props.tables.reduce((sum, t) => sum + t.seats, 0))
const freeTables = computed(() => props.tables.filter((t) => t.seated_count === 0).length)

/** A walk-in of N needs a table with at least N free chairs. */
function canSeat(table: RoomTable, party: number): boolean {
  return table.seats - table.seated_count >= party
}

const seatable = computed(() =>
  partySize.value ? props.tables.filter((t) => canSeat(t, partySize.value)).length : 0,
)

function styleFor(table: RoomTable) {
  const { width: w, height: h } = footprint(table.shape, table.span_x, table.span_y, CELL)
  return {
    width: `${w}px`,
    height: `${h}px`,
    transform: `translate(${table.pos_x * CELL}px, ${table.pos_y * CELL}px) rotate(${table.rotation}deg)`,
  }
}

function seatStyle(seat: Seat, table: RoomTable) {
  const { width: w, height: h } = footprint(table.shape, table.span_x, table.span_y, CELL)
  return {
    transform: `translate(${seat.x * w * 0.5}px, ${seat.y * h * 0.5}px) rotate(${seat.angle}deg)`,
  }
}

function stateOf(table: RoomTable): string {
  if (table.status === 'CLEANING') return 'cleaning'
  return fullness(table.seats, table.seated_count)
}

function startDrag(table: RoomTable, event: DragEvent) {
  if (!props.editable) return
  dragging.value = table.table_id
  event.dataTransfer?.setData('text/plain', table.table_id)
}

function dropAt(x: number, y: number) {
  const id = dragging.value
  dragging.value = null
  if (id && props.editable) emit('move', { id, x, y })
}
</script>

<template>
  <div class="space-y-3">
    <!-- ── controls ──────────────────────────────────────────────────────── -->
    <div class="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
      <span class="font-semibold text-ink">{{ seatedTotal }} / {{ seatsTotal }} كرسي مشغول</span>
      <span class="text-ink-muted">{{ freeTables }} طاولة فاضية</span>

      <label class="flex items-center gap-2">
        <span class="text-ink-muted">يسع كام؟</span>
        <input
          v-model.number="partySize"
          type="number"
          min="0"
          max="12"
          class="w-16 rounded-lg border border-strong px-2 py-1 text-center"
        />
        <span v-if="partySize" class="font-medium text-brand-700">
          {{ seatable }} طاولة تنفع
        </span>
      </label>

      <label class="ms-auto flex items-center gap-2 text-ink-muted">
        الميل
        <input v-model.number="tilt" type="range" min="0" max="65" class="w-28" />
      </label>
      <label class="flex items-center gap-2 text-ink-muted">
        التقريب
        <input v-model.number="zoom" type="range" min="0.6" max="1.4" step="0.05" class="w-28" />
      </label>
    </div>

    <!-- ── the room ──────────────────────────────────────────────────────── -->
    <div class="room-stage">
      <div
        class="room-floor"
        :style="{
          width: `${width}px`,
          height: `${height}px`,
          transform: `rotateX(${tilt}deg) scale(${zoom})`,
        }"
      >
        <!-- Drop targets sit under the furniture, so a table can be dragged
             onto any cell including one it already partly overlaps. -->
        <template v-if="editable">
          <div
            v-for="cell in columns * rows"
            :key="`cell-${cell}`"
            class="room-cell"
            :style="{
              width: `${CELL}px`,
              height: `${CELL}px`,
              transform: `translate(${((cell - 1) % columns) * CELL}px, ${Math.floor((cell - 1) / columns) * CELL}px)`,
            }"
            @dragover.prevent
            @drop.prevent="dropAt((cell - 1) % columns, Math.floor((cell - 1) / columns))"
          />
        </template>

        <div
          v-for="table in tables"
          :key="table.table_id"
          class="table-slot"
          :style="styleFor(table)"
        >
          <!-- Chairs first, so the table top overlaps them like real furniture. -->
          <span
            v-for="(seat, index) in seatsFor(
              table.shape,
              table.seats,
              table.seated_count,
              table.span_x,
              table.span_y,
            )"
            :key="index"
            class="chair"
            :class="seat.occupied ? 'chair-taken' : 'chair-empty'"
            :style="seatStyle(seat, table)"
            :title="seat.occupied ? 'كرسي عليه حد' : 'كرسي فاضي'"
          />

          <button
            type="button"
            class="table-top"
            :class="[
              `is-${stateOf(table)}`,
              `shape-${table.shape.toLowerCase()}`,
              selectedId === table.table_id && 'is-selected',
              partySize && !canSeat(table, partySize) && 'is-dimmed',
            ]"
            :draggable="editable"
            :aria-label="`طاولة ${table.number} — ${table.seated_count} من ${table.seats} كرسي — ${STATE_LABEL[stateOf(table)]}`"
            @dragstart="startDrag(table, $event)"
            @click="emit('select', table)"
          >
            <!-- Counter-rotated so the number stays upright however the table
                 is turned and however far the room is tilted. -->
            <span
              class="table-face"
              :style="{ transform: `rotate(${-table.rotation}deg) rotateX(${-tilt}deg)` }"
            >
              <span class="table-number">{{ table.number }}</span>
              <span class="table-seats">{{ table.seated_count }}/{{ table.seats }}</span>
            </span>
          </button>
        </div>

        <!-- ── kitchen stations, along the back wall ─────────────────────── -->
        <div
          v-for="(station, index) in stations"
          :key="station.id"
          class="station"
          :class="station.late_tickets ? 'station-late' : ''"
          :style="{
            transform: `translate(${index * (CELL * 2.1)}px, ${height - CELL * 0.55}px)`,
            width: `${CELL * 1.9}px`,
          }"
        >
          <span class="station-face" :style="{ transform: `rotateX(${-tilt}deg)` }">
            <span class="station-name">🔥 {{ station.name_ar }}</span>
            <span class="station-count">
              {{ station.open_tickets }} تذكرة
              <template v-if="station.late_tickets">· {{ station.late_tickets }} متأخرة</template>
            </span>
          </span>
        </div>
      </div>
    </div>

    <!-- ── legend: colour is never the only signal ───────────────────────── -->
    <div class="flex flex-wrap items-center gap-4 text-xs text-ink-muted">
      <span v-for="(label, key) in STATE_LABEL" :key="key" class="flex items-center gap-1.5">
        <span class="legend-dot" :class="`is-${key}`" />
        {{ label }}
      </span>
      <span class="flex items-center gap-1.5">
        <span class="legend-chair chair-taken" /> كرسي عليه حد
      </span>
      <span class="flex items-center gap-1.5">
        <span class="legend-chair chair-empty" /> كرسي فاضي
      </span>
    </div>
  </div>
</template>

<style scoped>
.room-stage {
  overflow: auto;
  padding: 2.5rem 1rem 5rem;
  border-radius: 1rem;
  perspective: 1400px;
  background: radial-gradient(120% 90% at 50% 0%, var(--surface-muted), var(--surface-sunken));
  border: 1px solid var(--border);
}

.room-floor {
  position: relative;
  margin-inline: auto;
  transform-style: preserve-3d;
  transform-origin: 50% 100%;
  border-radius: 0.75rem;
  /* Tiles, drawn rather than imaged: a repeating gradient costs nothing to load
     and stays crisp at any zoom. */
  background-image:
    linear-gradient(45deg, var(--floor-tile-alt) 25%, transparent 25%),
    linear-gradient(-45deg, var(--floor-tile-alt) 25%, transparent 25%),
    linear-gradient(45deg, transparent 75%, var(--floor-tile-alt) 75%),
    linear-gradient(-45deg, transparent 75%, var(--floor-tile-alt) 75%);
  background-size: 44px 44px;
  background-position:
    0 0,
    0 22px,
    22px -22px,
    -22px 0;
  background-color: var(--floor-tile);
  box-shadow: 0 30px 60px -20px var(--shadow-room);
}

.room-cell {
  position: absolute;
  inset-inline-start: 0;
  top: 0;
  border: 1px dashed rgba(42, 26, 22, 0.12);
  border-radius: 0.5rem;
}

.table-slot {
  position: absolute;
  inset-inline-start: 0;
  top: 0;
  transform-style: preserve-3d;
}

/* ── chairs ───────────────────────────────────────────────────────────────
   Small blocks standing just off the table edge. Occupied ones are burgundy and
   sit taller, so a full table reads as full from across the screen — before
   anybody has read a number. */
.chair {
  position: absolute;
  inset-inline-start: 50%;
  top: 50%;
  width: 22px;
  height: 22px;
  margin-inline-start: -11px;
  margin-top: -11px;
  border-radius: 6px 6px 3px 3px;
}

.chair-empty {
  background: var(--chair);
  box-shadow:
    0 4px 0 var(--wood-edge),
    0 6px 8px -2px var(--shadow-room);
  opacity: 0.72;
}

.chair-taken {
  background: var(--chair-occupied);
  box-shadow:
    0 8px 0 var(--brand-900),
    0 10px 12px -2px var(--shadow-room);
}

/* ── table tops ─────────────────────────────────────────────────────────── */
.table-top {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  border: 2px solid var(--wood-edge);
  cursor: pointer;
  transform-style: preserve-3d;
  /* The lift is what makes it read as furniture standing on a floor rather than
     a sticker printed on it. */
  box-shadow:
    0 10px 0 var(--wood-dark),
    0 18px 24px -8px var(--shadow-room);
  transition:
    box-shadow 120ms ease,
    filter 120ms ease;
}

.table-top:hover {
  filter: brightness(1.04);
  box-shadow:
    0 12px 0 var(--wood-dark),
    0 22px 30px -8px var(--shadow-room);
}

.table-top:focus-visible {
  outline: 3px solid var(--gold-500);
  outline-offset: 3px;
}

.shape-round {
  border-radius: 50%;
}
.shape-square,
.shape-rect {
  border-radius: 10px;
}
.shape-booth {
  border-radius: 10px 10px 22px 22px;
}
.shape-bar {
  border-radius: 8px;
}

.is-free {
  background: linear-gradient(160deg, var(--table-free), #f3ece2);
}
.is-light {
  background: linear-gradient(160deg, #fdf0f1, var(--table-busy));
}
.is-busy {
  background: linear-gradient(160deg, var(--table-busy), #e9a8ad);
}
.is-full {
  background: linear-gradient(160deg, #e9a8ad, var(--brand-300));
}
.is-cleaning {
  background: repeating-linear-gradient(
    45deg,
    var(--warning-bg),
    var(--warning-bg) 8px,
    #f6e3c4 8px,
    #f6e3c4 16px
  );
}

.is-selected {
  outline: 3px solid var(--gold-500);
  outline-offset: 2px;
}

/* Dimmed, not hidden: "this one will not fit your party of six" is useful, and
   removing it would make the room look wrong. */
.is-dimmed {
  filter: grayscale(0.75) opacity(0.45);
}

.table-face {
  display: grid;
  gap: 1px;
  place-items: center;
  line-height: 1.05;
  pointer-events: none;
}

.table-number {
  font-size: 17px;
  font-weight: 800;
  color: var(--ink);
}

.table-seats {
  font-size: 11px;
  font-weight: 600;
  color: var(--ink-muted);
  font-variant-numeric: tabular-nums;
}

/* ── stations ───────────────────────────────────────────────────────────── */
.station {
  position: absolute;
  inset-inline-start: 0;
  top: 0;
  height: 46px;
  display: grid;
  place-items: center;
  border-radius: 8px;
  border: 2px solid var(--wood-edge);
  background: linear-gradient(160deg, var(--wood), var(--wood-dark));
  box-shadow:
    0 12px 0 var(--wood-edge),
    0 20px 26px -10px var(--shadow-room);
  transform-style: preserve-3d;
}

.station-late {
  border-color: var(--danger);
  box-shadow:
    0 12px 0 var(--danger),
    0 20px 26px -10px var(--shadow-room);
}

.station-face {
  display: grid;
  place-items: center;
  gap: 1px;
  pointer-events: none;
}

.station-name {
  font-size: 12px;
  font-weight: 700;
  color: #fff;
}

.station-count {
  font-size: 10px;
  color: var(--gold-200);
  font-variant-numeric: tabular-nums;
}

/* ── legend ─────────────────────────────────────────────────────────────── */
.legend-dot {
  width: 14px;
  height: 14px;
  border-radius: 4px;
  border: 1px solid var(--wood-edge);
  display: inline-block;
}
.legend-dot.is-free {
  background: var(--table-free);
}
.legend-dot.is-light {
  background: #fdf0f1;
}
.legend-dot.is-busy {
  background: var(--table-busy);
}
.legend-dot.is-full {
  background: var(--brand-300);
}
.legend-dot.is-cleaning {
  background: var(--warning-bg);
}

.legend-chair {
  width: 12px;
  height: 12px;
  border-radius: 4px 4px 2px 2px;
  display: inline-block;
}

/* Flat for anyone who asked the OS not to animate or tilt things. The room
   still works — it just stops pretending to have depth. */
@media (prefers-reduced-motion: reduce) {
  .room-floor {
    transform: none !important;
  }
  .table-face,
  .station-face {
    transform: none !important;
  }
  .table-top {
    transition: none;
  }
}
</style>
