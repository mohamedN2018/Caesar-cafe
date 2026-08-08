<script setup lang="ts">
/**
 * The floor plan editor — the screen that draws the room.
 *
 * This is the source of `pos_x`/`pos_y`, which is why it exists: the Desktop's
 * floor map lays tables out on those coordinates, and a waiter finding "table 7"
 * by the shape of the room is meaningfully faster than reading it off a list.
 * Without this screen every table falls back to a flow layout and the map is
 * just a list with bigger buttons.
 *
 * Two decisions worth stating:
 *
 *   * **Nothing saves while you drag.** A PATCH per mousemove would be hundreds
 *     of writes and a change log the Desktop then has to pull. Positions are
 *     collected and sent in one explicit save, and the button says how many are
 *     unsaved so leaving the page cannot silently lose the layout.
 *   * **The grid is coarse on purpose.** Coordinates snap to whole cells because
 *     the Desktop renders into a QGridLayout — pixel-perfect placement here would
 *     be a promise the client cannot keep.
 *
 * Live state (occupied, due) is shown but never edited here. Moving a tile moves
 * the furniture, not the bill.
 */
import { computed, onMounted, ref } from 'vue'

import { ApiError, api } from '@/api/client'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiInput from '@/components/ui/UiInput.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import { money } from '@/lib/format'
import { useAuthStore } from '@/stores/auth'

interface Area {
  id: string
  name_ar: string
  sort_order: number
  is_active: boolean
}

interface Table {
  id: string
  area: string
  area_name: string
  number: string
  seats: number
  status: string
  pos_x: number
  pos_y: number
  is_active: boolean
}

interface LiveTable {
  table_id: string
  status: string
  order_count: number
  total_due: string
  waiter: string | null
}

/** Cells across and down. Matches the Desktop's grid, which is what renders it. */
const COLUMNS = 10
const ROWS = 8

const auth = useAuthStore()
const mayEdit = computed(() => auth.can('branch.manage_tables'))

const areas = ref<Area[]>([])
const tables = ref<Table[]>([])
const live = ref<Record<string, LiveTable>>({})
const selectedArea = ref('')
const loading = ref(true)
const error = ref('')
const saving = ref(false)
const saved = ref(false)

/** Table id → the position it had when loaded. Anything that differs is unsaved. */
const original = ref<Record<string, { x: number; y: number }>>({})
const dragging = ref<string | null>(null)

const newTable = ref({ number: '', seats: 4 })
const creating = ref(false)

const visible = computed(() =>
  tables.value.filter((t) => t.is_active && (!selectedArea.value || t.area === selectedArea.value)),
)

const moved = computed(() =>
  visible.value.filter((t) => {
    const was = original.value[t.id]
    return was && (was.x !== t.pos_x || was.y !== t.pos_y)
  }),
)

/** Two tables in one cell is a layout that reads wrong on the Desktop. */
const collisions = computed(() => {
  const seen = new Map<string, number>()
  for (const table of visible.value) {
    const key = `${table.pos_x},${table.pos_y}`
    seen.set(key, (seen.get(key) ?? 0) + 1)
  }
  return [...seen.values()].filter((n) => n > 1).length
})

function at(x: number, y: number): Table | undefined {
  return visible.value.find((t) => t.pos_x === x && t.pos_y === y)
}

function tone(table: Table): string {
  const state = live.value[table.id]
  if (!state || state.order_count === 0) return 'bg-white ring-slate-200 text-slate-700'
  if (state.status === 'READY') return 'bg-emerald-50 ring-emerald-300 text-emerald-900'
  return 'bg-brand-50 ring-brand-300 text-brand-900'
}

// ── dragging ────────────────────────────────────────────────────────────────

function startDrag(table: Table, event: DragEvent) {
  if (!mayEdit.value) return
  dragging.value = table.id
  event.dataTransfer?.setData('text/plain', table.id)
}

function drop(x: number, y: number) {
  const id = dragging.value
  dragging.value = null
  if (!id || !mayEdit.value) return

  const table = tables.value.find((t) => t.id === id)
  if (!table) return

  // Swap rather than stack: dropping onto an occupied cell is almost always
  // "these two should trade places", and silently overlapping them would produce
  // a layout that looks fine here and wrong on the Desktop.
  const other = at(x, y)
  if (other && other.id !== id) {
    other.pos_x = table.pos_x
    other.pos_y = table.pos_y
  }

  table.pos_x = x
  table.pos_y = y
  saved.value = false
}

// ── loading and saving ──────────────────────────────────────────────────────

function snapshot() {
  original.value = Object.fromEntries(
    tables.value.map((t) => [t.id, { x: t.pos_x, y: t.pos_y }]),
  )
}

async function load() {
  loading.value = true
  try {
    const [areaRows, tableRows] = await Promise.all([
      api.get<Area[]>('/floor/areas/'),
      api.get<Table[]>('/floor/tables/'),
    ])
    areas.value = areaRows.filter((a) => a.is_active).sort((a, b) => a.sort_order - b.sort_order)
    tables.value = tableRows
    if (!selectedArea.value && areas.value.length) selectedArea.value = areas.value[0].id
    snapshot()
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تحميل المخطط.'
  } finally {
    loading.value = false
  }

  // Live state is a separate, non-fatal call: the editor still works when the
  // status endpoint is unavailable, it just shows no occupancy.
  try {
    const rows = await api.get<LiveTable[]>('/floor/status/')
    live.value = Object.fromEntries(rows.map((r) => [r.table_id, r]))
  } catch {
    live.value = {}
  }
}

async function save() {
  if (!moved.value.length) return
  saving.value = true
  try {
    // One PATCH per moved table, not per drag. The change log the Desktop pulls
    // gets one row per table that actually moved.
    await Promise.all(
      moved.value.map((t) => api.patch(`/floor/tables/${t.id}/`, { pos_x: t.pos_x, pos_y: t.pos_y })),
    )
    snapshot()
    saved.value = true
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر حفظ المخطط.'
  } finally {
    saving.value = false
  }
}

function revert() {
  for (const table of tables.value) {
    const was = original.value[table.id]
    if (was) {
      table.pos_x = was.x
      table.pos_y = was.y
    }
  }
  saved.value = false
}

async function addTable() {
  if (!newTable.value.number.trim() || !selectedArea.value) return
  creating.value = true
  try {
    // Dropped into the first free cell rather than at 0,0 — a new table landing
    // under an existing one looks like it failed to appear.
    const free = firstFreeCell()
    await api.post('/floor/tables/', {
      area: selectedArea.value,
      number: newTable.value.number.trim(),
      seats: newTable.value.seats,
      pos_x: free.x,
      pos_y: free.y,
    })
    newTable.value = { number: '', seats: 4 }
    await load()
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر إضافة الطاولة.'
  } finally {
    creating.value = false
  }
}

function firstFreeCell(): { x: number; y: number } {
  for (let y = 0; y < ROWS; y += 1) {
    for (let x = 0; x < COLUMNS; x += 1) {
      if (!at(x, y)) return { x, y }
    }
  }
  return { x: 0, y: 0 }
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 class="text-2xl font-bold text-slate-900">🪑 مخطط الصالة</h1>
        <p class="mt-1 text-sm text-slate-500">
          اسحب الطاولة إلى مكانها في الغرفة. هذا التوزيع هو ما يظهر على شاشة الكاشير.
        </p>
      </div>

      <div v-if="mayEdit" class="flex items-center gap-2">
        <UiButton v-if="moved.length" variant="ghost" @click="revert">تراجع</UiButton>
        <UiButton :disabled="!moved.length || saving" :loading="saving" @click="save">
          {{ moved.length ? `حفظ ${moved.length} تغيير` : 'لا توجد تغييرات' }}
        </UiButton>
      </div>
    </div>

    <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>
    <UiAlert v-else-if="saved && !moved.length" tone="success">
      تم الحفظ. ستظهر على الأجهزة بعد المزامنة التالية.
    </UiAlert>
    <UiAlert v-else-if="moved.length" tone="warning">
      {{ moved.length }} طاولة غير محفوظة — لن تصل إلى الأجهزة قبل الحفظ.
    </UiAlert>
    <UiAlert v-if="collisions" tone="warning">
      {{ collisions }} خانة بها أكثر من طاولة — ستظهر متداخلة على الكاشير.
    </UiAlert>

    <UiSkeleton v-if="loading" :rows="8" />

    <UiEmpty
      v-else-if="!areas.length"
      icon="🪑"
      title="لا توجد مناطق"
      description="أضف منطقة (صالة، تراس) من الإعدادات ثم عُد لرسم المخطط."
    />

    <template v-else>
      <div v-if="areas.length > 1" class="flex flex-wrap gap-2">
        <button
          v-for="area in areas"
          :key="area.id"
          class="rounded-lg px-3 py-2 text-sm font-medium ring-1 ring-inset transition"
          :class="
            selectedArea === area.id
              ? 'bg-brand-50 text-brand-800 ring-brand-200'
              : 'bg-white text-slate-700 ring-slate-200 hover:bg-slate-50'
          "
          @click="selectedArea = area.id"
        >
          {{ area.name_ar }}
        </button>
      </div>

      <UiCard>
        <div
          class="grid gap-2 overflow-x-auto"
          :style="{ gridTemplateColumns: `repeat(${COLUMNS}, minmax(5.5rem, 1fr))` }"
        >
          <template v-for="y in ROWS" :key="`row-${y}`">
            <div
              v-for="x in COLUMNS"
              :key="`${x}-${y}`"
              class="aspect-square rounded-lg border border-dashed border-slate-200 p-1"
              @dragover.prevent
              @drop.prevent="drop(x - 1, y - 1)"
            >
              <div
                v-if="at(x - 1, y - 1)"
                :draggable="mayEdit"
                class="flex h-full w-full flex-col items-center justify-center rounded-lg ring-1 ring-inset transition"
                :class="[tone(at(x - 1, y - 1)!), mayEdit ? 'cursor-move' : 'cursor-default']"
                @dragstart="startDrag(at(x - 1, y - 1)!, $event)"
              >
                <span class="text-base font-bold">{{ at(x - 1, y - 1)!.number }}</span>
                <span class="text-[11px] opacity-70">{{ at(x - 1, y - 1)!.seats }} أفراد</span>
                <span
                  v-if="live[at(x - 1, y - 1)!.id]?.order_count"
                  class="mt-0.5 text-[11px] font-semibold"
                >
                  {{ money(live[at(x - 1, y - 1)!.id].total_due) }}
                </span>
              </div>
            </div>
          </template>
        </div>

        <p class="mt-4 text-xs text-slate-400">
          الشبكة {{ COLUMNS }}×{{ ROWS }} خانة — نفس الشبكة التي يرسمها جهاز الكاشير.
        </p>
      </UiCard>

      <UiCard v-if="mayEdit">
        <h2 class="text-sm font-semibold text-slate-900">إضافة طاولة</h2>
        <form class="mt-3 flex flex-wrap items-end gap-3" @submit.prevent="addTable">
          <UiInput v-model="newTable.number" label="رقم الطاولة" class="w-32" required />
          <UiInput v-model.number="newTable.seats" label="عدد الأفراد" type="number" class="w-32" />
          <UiButton type="submit" :loading="creating" :disabled="!newTable.number.trim()">
            إضافة
          </UiButton>
        </form>
      </UiCard>
    </template>
  </div>
</template>
