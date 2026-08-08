<script setup lang="ts">
/**
 * The room.
 *
 * Built from CSS 3D transforms rather than a WebGL library, and that is a real
 * decision rather than a shortcut. three.js would add ~600 kB to a bundle a
 * cafe loads over Egyptian mobile data, and it would put every table behind a
 * canvas — unselectable, untabbable, invisible to a screen reader. Here a table
 * is a `<button>` that happens to be tilted.
 *
 * What the view is for: **seating a walk-in and finding a bill.** Everything
 * below serves one of those two.
 *
 * Four things make it read as a room rather than a diagram:
 *
 *   * **Depth order.** A chair at the near edge is painted OVER the table; one
 *     at the far edge is painted under it. Drawing them in one pass — the first
 *     version's mistake — tucks the near chairs behind the furniture and the
 *     illusion collapses immediately.
 *   * **Chairs shaped like chairs.** A back and a seat, turned to face the
 *     table. A square block beside a table is a square block.
 *   * **People, not coloured squares.** An occupied seat gets a head and
 *     shoulders. "How many are actually on it" is the question this screen
 *     exists to answer, and a person answers it before any number does.
 *   * **Walls, a door and windows.** A floating slab of tiles is a diagram. A
 *     room with a back wall is somewhere a waiter recognises.
 *
 * The terrace is drawn differently from the inside room — decking instead of
 * tiles, a railing instead of walls — because a waiter asking "which is
 * outside" should not have to read a tab to find out.
 */
import { computed, ref } from 'vue'

import { footprint, fullness, seatsFor, splitByDepth, type Seat, type TableShape } from './geometry'

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
    /** Outdoor areas are decked and railed rather than tiled and walled. */
    outdoor?: boolean
    columns?: number
    rows?: number
  }>(),
  {
    stations: () => [],
    editable: false,
    selectedId: null,
    outdoor: false,
    columns: 10,
    rows: 8,
  },
)

const emit = defineEmits<{
  (event: 'select', table: RoomTable): void
  (event: 'move', payload: { id: string; x: number; y: number }): void
}>()

/** Pixels per grid cell. */
const CELL = 88
/** How tall the back wall stands, in the same units. */
const WALL = 150

const STATE_LABEL: Record<string, string> = {
  free: 'متاحة',
  light: 'جالسون قليل',
  busy: 'شبه ممتلئة',
  full: 'ممتلئة',
  cleaning: 'تحتاج تنظيف',
}

const tilt = ref(54)
const zoom = ref(1)
const partySize = ref(0)
const dragging = ref<string | null>(null)

const width = computed(() => props.columns * CELL)
const height = computed(() => props.rows * CELL)

const seatedTotal = computed(() => props.tables.reduce((sum, t) => sum + t.seated_count, 0))
const seatsTotal = computed(() => props.tables.reduce((sum, t) => sum + t.seats, 0))
const freeTables = computed(() => props.tables.filter((t) => t.seated_count === 0).length)

function canSeat(table: RoomTable, party: number): boolean {
  return table.seats - table.seated_count >= party
}

const seatable = computed(() =>
  partySize.value ? props.tables.filter((t) => canSeat(t, partySize.value)).length : 0,
)

/**
 * Far tables first, so a near one overlaps it.
 *
 * `pos_y` alone is not enough: a table one row back but drawn tall can still
 * reach in front of the one below it, so the sort is on where the table's near
 * edge actually falls.
 */
const ordered = computed(() =>
  [...props.tables].sort(
    (a, b) => a.pos_y + a.span_y * 0.5 - (b.pos_y + b.span_y * 0.5) || a.pos_x - b.pos_x,
  ),
)

function sizeOf(table: RoomTable) {
  return footprint(table.shape, table.span_x, table.span_y, CELL)
}

function styleFor(table: RoomTable) {
  const { width: w, height: h } = sizeOf(table)
  return {
    width: `${w}px`,
    height: `${h}px`,
    transform: `translate(${table.pos_x * CELL}px, ${table.pos_y * CELL}px) rotate(${table.rotation}deg)`,
  }
}

function seatsOf(table: RoomTable) {
  return splitByDepth(
    seatsFor(table.shape, table.seats, table.seated_count, table.span_x, table.span_y),
  )
}

function seatStyle(seat: Seat, table: RoomTable) {
  const { width: w, height: h } = sizeOf(table)
  return {
    transform: `translate(${seat.x * w * 0.5}px, ${seat.y * h * 0.5}px) rotate(${seat.angle}deg)`,
  }
}

/** The ellipse of shadow a table casts, sized to its footprint. */
function shadowStyle(table: RoomTable) {
  const { width: w, height: h } = sizeOf(table)
  return {
    width: `${w * 1.15}px`,
    height: `${h * 0.55}px`,
    transform: `translate(-50%, 0) translateY(${h * 0.28}px)`,
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
        <input v-model.number="tilt" type="range" min="0" max="66" class="w-24" />
      </label>
      <label class="flex items-center gap-2 text-ink-muted">
        التقريب
        <input v-model.number="zoom" type="range" min="0.6" max="1.5" step="0.05" class="w-24" />
      </label>
    </div>

    <!-- ── the room ──────────────────────────────────────────────────────── -->
    <div class="room-stage" :class="outdoor ? 'is-outdoor' : 'is-indoor'">
      <div class="room-camera" :style="{ transform: `rotateX(${tilt}deg) scale(${zoom})` }">
        <div
          class="room-floor"
          :class="outdoor ? 'floor-deck' : 'floor-tile'"
          :style="{ width: `${width}px`, height: `${height}px` }"
        >
          <!-- ── the shell ──────────────────────────────────────────────────
               A back wall standing up from the far edge, with a doorway and two
               windows cut into it. Outside, a railing instead. -->
          <div
            class="wall wall-back"
            :style="{ width: `${width}px`, height: `${WALL}px`, transformOrigin: 'top' }"
          >
            <div class="wall-face">
              <span v-if="!outdoor" class="window" />
              <span v-if="!outdoor" class="doorway">الباب</span>
              <span v-if="!outdoor" class="window" />
              <template v-if="outdoor">
                <span v-for="post in 14" :key="post" class="rail-post" />
              </template>
            </div>
          </div>

          <div
            class="wall wall-side"
            :style="{ height: `${WALL}px`, width: `${height}px`, transformOrigin: 'top left' }"
          />

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

          <!-- ── furniture ─────────────────────────────────────────────────
               Far tables first so near ones overlap them, and within each
               table: back chairs, shadow, top, front chairs. -->
          <div
            v-for="table in ordered"
            :key="table.table_id"
            class="table-slot"
            :style="styleFor(table)"
          >
            <span class="table-shadow" :style="shadowStyle(table)" />

            <span
              v-for="(seat, index) in seatsOf(table).behind"
              :key="`b-${index}`"
              class="chair"
              :class="seat.occupied ? 'is-taken' : 'is-empty'"
              :style="seatStyle(seat, table)"
            >
              <span class="chair-back" />
              <span class="chair-seat" />
              <span v-if="seat.occupied" class="person" :style="{ transform: `rotateX(${-tilt}deg)` }">
                <span class="person-head" />
                <span class="person-body" />
              </span>
            </span>

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
              <!-- Counter-rotated, so the number stays upright however the
                   table is turned and however far the room is tilted. -->
              <span
                class="table-face"
                :style="{ transform: `rotate(${-table.rotation}deg) rotateX(${-tilt}deg)` }"
              >
                <span class="table-number">{{ table.number }}</span>
                <span class="table-seats">{{ table.seated_count }}/{{ table.seats }}</span>
              </span>
            </button>

            <span
              v-for="(seat, index) in seatsOf(table).infront"
              :key="`f-${index}`"
              class="chair chair-front"
              :class="seat.occupied ? 'is-taken' : 'is-empty'"
              :style="seatStyle(seat, table)"
            >
              <span class="chair-back" />
              <span class="chair-seat" />
              <span v-if="seat.occupied" class="person" :style="{ transform: `rotateX(${-tilt}deg)` }">
                <span class="person-head" />
                <span class="person-body" />
              </span>
            </span>
          </div>

          <!-- ── the pass ──────────────────────────────────────────────────
               Kitchen stations along the back, drawn as a counter rather than
               floating labels: they are physically there, and a late station
               is somewhere a waiter walks to. -->
          <div
            v-for="(station, index) in stations"
            :key="station.id"
            class="station"
            :class="station.late_tickets ? 'is-late' : ''"
            :style="{
              transform: `translate(${index * (CELL * 2.15)}px, ${height - CELL * 0.75}px)`,
              width: `${CELL * 2}px`,
            }"
          >
            <span class="station-face" :style="{ transform: `rotateX(${-tilt}deg)` }">
              <span class="station-name">{{ station.name_ar }}</span>
              <span class="station-count">
                {{ station.open_tickets }} تذكرة
                <template v-if="station.late_tickets">· {{ station.late_tickets }} متأخرة</template>
              </span>
            </span>
          </div>
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
        <span class="legend-person" /> شخص جالس
      </span>
    </div>
  </div>
</template>

<style scoped>
.room-stage {
  overflow: auto;
  padding: 7rem 1.5rem 4rem;
  border-radius: 1rem;
  perspective: 1500px;
  perspective-origin: 50% 30%;
  border: 1px solid var(--border);
}

.is-indoor {
  background: radial-gradient(130% 100% at 50% 0%, #efe7db, var(--surface-sunken));
}

/* Outside reads as outside before anybody has read a tab. */
.is-outdoor {
  background: radial-gradient(130% 100% at 50% 0%, #dbe7e4, #c9dbd6);
}

.room-camera {
  transform-style: preserve-3d;
  transform-origin: 50% 100%;
  width: max-content;
  margin-inline: auto;
}

.room-floor {
  position: relative;
  transform-style: preserve-3d;
  border-radius: 2px;
  box-shadow: 0 40px 70px -25px var(--shadow-room);
}

/* Tiles and decking, drawn rather than imaged: repeating gradients cost nothing
   to load and stay crisp at any zoom. */
.floor-tile {
  background-color: var(--floor-tile);
  background-image:
    linear-gradient(45deg, var(--floor-tile-alt) 25%, transparent 25%),
    linear-gradient(-45deg, var(--floor-tile-alt) 25%, transparent 25%),
    linear-gradient(45deg, transparent 75%, var(--floor-tile-alt) 75%),
    linear-gradient(-45deg, transparent 75%, var(--floor-tile-alt) 75%);
  background-size: 46px 46px;
  background-position:
    0 0,
    0 23px,
    23px -23px,
    -23px 0;
}

.floor-deck {
  background-color: #b98a5c;
  background-image: repeating-linear-gradient(
    90deg,
    #b98a5c 0 34px,
    #ad7f52 34px 36px,
    #c2936a 36px 70px,
    #ad7f52 70px 72px
  );
}

/* ── the shell ────────────────────────────────────────────────────────────
   Planes standing up from the floor edges. `transform-origin` at the shared
   edge is what makes them hinge rather than float. */
.wall {
  position: absolute;
  inset-inline-start: 0;
  top: 0;
  transform: rotateX(-90deg);
  transform-style: preserve-3d;
}

.wall-back {
  background: linear-gradient(#f4ece0, #e3d5c2);
  border-bottom: 3px solid var(--border-strong);
  box-shadow: inset 0 -18px 26px -18px rgba(42, 26, 22, 0.35);
}

.wall-side {
  inset-inline-start: 0;
  transform: rotateX(-90deg) rotateY(90deg);
  background: linear-gradient(#efe6d8, #ddcdb8);
  opacity: 0.92;
}

.wall-face {
  display: flex;
  align-items: flex-end;
  justify-content: center;
  gap: 3.5rem;
  height: 100%;
  padding-bottom: 0.75rem;
}

.window {
  width: 130px;
  height: 74px;
  border-radius: 6px 6px 2px 2px;
  border: 4px solid #cbb99c;
  background: linear-gradient(160deg, #cfe4ee, #a8cadb 60%, #93bacd);
  box-shadow: inset 0 0 0 2px rgba(255, 255, 255, 0.5);
}

.doorway {
  width: 96px;
  height: 108px;
  border-radius: 4px;
  border: 4px solid var(--wood-dark);
  background: linear-gradient(#8a5a33, #6a421f);
  color: var(--gold-200);
  font-size: 11px;
  font-weight: 700;
  display: grid;
  place-items: center;
  align-content: end;
  padding-bottom: 6px;
}

.rail-post {
  width: 7px;
  height: 78px;
  border-radius: 3px;
  background: linear-gradient(#e8e2d6, #cfc5b3);
}

.room-cell {
  position: absolute;
  inset-inline-start: 0;
  top: 0;
  border: 1px dashed rgba(42, 26, 22, 0.14);
  border-radius: 0.4rem;
}

/* ── furniture ────────────────────────────────────────────────────────────── */
.table-slot {
  position: absolute;
  inset-inline-start: 0;
  top: 0;
  transform-style: preserve-3d;
}

/* A soft ellipse on the floor. The first version used a hard offset shadow,
   which reads as a sticker with a border rather than an object above a surface. */
.table-shadow {
  position: absolute;
  inset-inline-start: 50%;
  top: 50%;
  border-radius: 50%;
  background: radial-gradient(closest-side, rgba(42, 26, 22, 0.34), transparent 72%);
  pointer-events: none;
}

.chair {
  position: absolute;
  inset-inline-start: 50%;
  top: 50%;
  width: 26px;
  height: 26px;
  margin-inline-start: -13px;
  margin-top: -13px;
  transform-style: preserve-3d;
  pointer-events: none;
}

/* A back and a seat. A square block beside a table is a square block. */
.chair-seat {
  position: absolute;
  inset: 6px 2px 2px;
  border-radius: 4px;
  background: linear-gradient(160deg, var(--chair), #6f4728);
  box-shadow: 0 3px 0 var(--wood-edge);
}

.chair-back {
  position: absolute;
  inset: 0 1px auto;
  height: 9px;
  border-radius: 4px 4px 2px 2px;
  background: linear-gradient(#9a6739, #7a5029);
  box-shadow: 0 2px 3px rgba(42, 26, 22, 0.35);
}

.is-taken .chair-seat {
  background: linear-gradient(160deg, var(--brand-500), var(--brand-800));
  box-shadow: 0 3px 0 var(--brand-900);
}

.is-taken .chair-back {
  background: linear-gradient(var(--brand-400), var(--brand-700));
}

.is-empty {
  opacity: 0.82;
}

/* A person, not a coloured square. "How many are actually on it" is the
   question this screen exists to answer, and a figure answers it before any
   number does. Counter-tilted so it stands up out of the floor plane. */
.person {
  position: absolute;
  inset-inline-start: 50%;
  bottom: 8px;
  transform-origin: bottom center;
  display: grid;
  justify-items: center;
  margin-inline-start: -7px;
  pointer-events: none;
}

.person-head {
  width: 11px;
  height: 11px;
  border-radius: 50%;
  background: #d8a273;
  border: 1.5px solid #a9764b;
}

.person-body {
  width: 15px;
  height: 13px;
  margin-top: -2px;
  border-radius: 7px 7px 3px 3px;
  background: linear-gradient(var(--brand-600), var(--brand-800));
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
  /* The solid edge below is the table's thickness; the blurred one is contact
     with the floor. Together they read as an object standing on something. */
  box-shadow:
    0 9px 0 var(--wood-dark),
    0 14px 18px -6px rgba(42, 26, 22, 0.4);
  transition:
    box-shadow 120ms ease,
    filter 120ms ease;
}

.table-top:hover {
  filter: brightness(1.05);
  box-shadow:
    0 11px 0 var(--wood-dark),
    0 18px 24px -6px rgba(42, 26, 22, 0.45);
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
  border-radius: 8px;
}
.shape-booth {
  border-radius: 8px 8px 24px 24px;
}
.shape-bar {
  border-radius: 6px;
}

/* Grain, so a table top is a material rather than a swatch. */
.is-free {
  background:
    repeating-linear-gradient(92deg, rgba(255, 255, 255, 0.5) 0 6px, transparent 6px 13px),
    linear-gradient(160deg, #fffdf9, #efe3d2);
}
.is-light {
  background:
    repeating-linear-gradient(92deg, rgba(255, 255, 255, 0.4) 0 6px, transparent 6px 13px),
    linear-gradient(160deg, #fdf0f1, var(--table-busy));
}
.is-busy {
  background:
    repeating-linear-gradient(92deg, rgba(255, 255, 255, 0.32) 0 6px, transparent 6px 13px),
    linear-gradient(160deg, var(--table-busy), #e6a0a6);
}
.is-full {
  background:
    repeating-linear-gradient(92deg, rgba(255, 255, 255, 0.26) 0 6px, transparent 6px 13px),
    linear-gradient(160deg, #e6a0a6, var(--brand-300));
}
.is-cleaning {
  background: repeating-linear-gradient(
    45deg,
    var(--warning-bg),
    var(--warning-bg) 9px,
    #f4dfbc 9px,
    #f4dfbc 18px
  );
}

.is-selected {
  outline: 3px solid var(--gold-500);
  outline-offset: 2px;
}

/* Dimmed, not hidden: "this one will not fit your party of six" is useful, and
   removing it would make the room look wrong. */
.is-dimmed {
  filter: grayscale(0.8) opacity(0.4);
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
  text-shadow: 0 1px 0 rgba(255, 255, 255, 0.7);
}

.table-seats {
  font-size: 11px;
  font-weight: 700;
  color: var(--ink-muted);
  font-variant-numeric: tabular-nums;
}

/* ── the pass ───────────────────────────────────────────────────────────── */
.station {
  position: absolute;
  inset-inline-start: 0;
  top: 0;
  height: 52px;
  display: grid;
  place-items: center;
  border-radius: 6px;
  border: 2px solid var(--wood-edge);
  background:
    repeating-linear-gradient(90deg, rgba(255, 255, 255, 0.08) 0 8px, transparent 8px 17px),
    linear-gradient(160deg, #b8823f, var(--wood-dark));
  box-shadow:
    0 12px 0 var(--wood-edge),
    0 20px 26px -10px rgba(42, 26, 22, 0.45);
  transform-style: preserve-3d;
}

.station.is-late {
  border-color: var(--danger);
  box-shadow:
    0 12px 0 var(--danger),
    0 20px 26px -10px rgba(42, 26, 22, 0.45);
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

.legend-person {
  width: 11px;
  height: 11px;
  border-radius: 50% 50% 4px 4px;
  background: var(--brand-700);
  display: inline-block;
}

/* Flat for anyone who asked the OS not to tilt things. The room still works —
   it stops pretending to have depth, and the walls fold away rather than
   standing edge-on as unreadable lines. */
@media (prefers-reduced-motion: reduce) {
  .room-camera {
    transform: none !important;
  }
  .wall {
    display: none;
  }
  .table-face,
  .station-face,
  .person {
    transform: none !important;
  }
  .table-top {
    transition: none;
  }
}
</style>
