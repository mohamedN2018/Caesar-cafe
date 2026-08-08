<script setup lang="ts">
/**
 * The floor: the live room, and the editor for it.
 *
 * One screen with two modes rather than two screens, because they answer the
 * same question from opposite ends — "where should I seat these six people"
 * and "where should this table be so that question is easy". Splitting them
 * meant every layout change had to be checked by navigating somewhere else.
 *
 * The live mode is the source of `pos_x`/`pos_y` too: the Desktop's floor map
 * lays tables out on those coordinates, so what is arranged here is what a
 * waiter sees on the terminal.
 *
 * **Nothing saves while you drag.** A PATCH per mousemove would be hundreds of
 * writes and a change log the Desktop then has to pull. Positions are collected
 * and sent in one explicit save, and the button says how many are unsaved so
 * leaving the page cannot silently lose the layout.
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'

import { ApiError, api } from '@/api/client'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiInput from '@/components/ui/UiInput.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import { money } from '@/lib/format'
import RoomView, { type RoomStation, type RoomTable } from '@/modules/floor/RoomView.vue'
import { useAuthStore } from '@/stores/auth'

interface Area {
  id: string
  name_ar: string
  sort_order: number
  is_active: boolean
}

interface Table extends RoomTable {
  id: string
  area: string
  area_name: string
  is_active: boolean
}

interface LiveTable {
  table_id: string
  seated_count: number
  status: string
  order_count: number
  total_due: string
  waiter: string | null
  seated_minutes: number | null
}

interface Station {
  id: string
  name_ar: string
  is_active: boolean
}

interface Ticket {
  station_id: string
  status: string
  is_late: boolean
}

const SHAPES = [
  { key: 'ROUND', label: 'دائرية' },
  { key: 'SQUARE', label: 'مربعة' },
  { key: 'RECT', label: 'مستطيلة' },
  { key: 'BOOTH', label: 'كنبة' },
  { key: 'BAR', label: 'بار' },
] as const

const REFRESH_MS = 15_000

const auth = useAuthStore()
const mayEdit = computed(() => auth.can('branch.manage_tables'))

const areas = ref<Area[]>([])
const tables = ref<Table[]>([])
const live = ref<Record<string, LiveTable>>({})
const stations = ref<RoomStation[]>([])
const selectedArea = ref('')
const selectedId = ref<string | null>(null)
const editing = ref(false)
const loading = ref(true)
const error = ref('')
const saving = ref(false)
let timer: number | undefined

/** Table id → the geometry it had when loaded. Anything different is unsaved. */
const original = ref<Record<string, { x: number; y: number; shape: string; seats: number }>>({})

const newTable = ref({ number: '', seats: 4, shape: 'SQUARE' })
const creating = ref(false)

const visible = computed(() =>
  tables.value
    .filter((t) => t.is_active && (!selectedArea.value || t.area === selectedArea.value))
    .map((t) => ({ ...t, ...(live.value[t.id] ?? {}), table_id: t.id })),
)

/**
 * Whether this area is outside, decided from its name.
 *
 * A guess, and a cheap one — but the alternative is a database column somebody
 * has to remember to set, and getting it wrong costs a decking texture rather
 * than anything that matters.
 */
const OUTDOOR_WORDS = ['تراس', 'خارج', 'حديق', 'جاردن', 'terrace', 'garden', 'outdoor']
const isOutdoor = computed(() => {
  const name = areas.value.find((a) => a.id === selectedArea.value)?.name_ar?.toLowerCase() ?? ''
  return OUTDOOR_WORDS.some((word) => name.includes(word))
})

const selected = computed(() => tables.value.find((t) => t.id === selectedId.value) ?? null)
const selectedLive = computed(() =>
  selectedId.value ? (live.value[selectedId.value] ?? null) : null,
)

const changed = computed(() =>
  tables.value.filter((t) => {
    const was = original.value[t.id]
    return (
      was &&
      (was.x !== t.pos_x || was.y !== t.pos_y || was.shape !== t.shape || was.seats !== t.seats)
    )
  }),
)

/** Two tables in one cell reads wrong on the Desktop, which renders a grid. */
const collisions = computed(() => {
  const seen = new Map<string, number>()
  for (const table of visible.value) {
    const key = `${table.pos_x},${table.pos_y}`
    seen.set(key, (seen.get(key) ?? 0) + 1)
  }
  return [...seen.values()].filter((n) => n > 1).length
})

// ── loading ─────────────────────────────────────────────────────────────────

function snapshot() {
  original.value = Object.fromEntries(
    tables.value.map((t) => [t.id, { x: t.pos_x, y: t.pos_y, shape: t.shape, seats: t.seats }]),
  )
}

async function load() {
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
  await refreshLive()
}

/**
 * Live state is a separate, non-fatal call. The editor still works when the
 * status endpoint is down; the room just shows no occupancy rather than
 * refusing to draw.
 */
async function refreshLive() {
  try {
    const rows = await api.get<LiveTable[]>('/floor/status/')
    live.value = Object.fromEntries(rows.map((r) => [r.table_id, r]))
  } catch {
    live.value = {}
  }

  // Stations only matter to somebody who can see the kitchen. Asking anyway
  // would earn a 403 for a decoration.
  if (!auth.can('kitchen.view')) return
  try {
    const [stationRows, tickets] = await Promise.all([
      api.get<Station[]>('/kitchen/stations/'),
      api.get<Ticket[]>('/kitchen/tickets/'),
    ])
    stations.value = stationRows
      .filter((s) => s.is_active)
      .map((s) => ({
        id: s.id,
        name_ar: s.name_ar,
        open_tickets: tickets.filter((t) => t.station_id === s.id).length,
        late_tickets: tickets.filter((t) => t.station_id === s.id && t.is_late).length,
      }))
  } catch {
    stations.value = []
  }
}

// ── editing ─────────────────────────────────────────────────────────────────

function move({ id, x, y }: { id: string; x: number; y: number }) {
  const table = tables.value.find((t) => t.id === id)
  if (!table) return

  // Swap rather than stack: dropping onto an occupied cell is almost always
  // "these two should trade places", and overlapping them produces a layout
  // that looks fine here and wrong on the Desktop.
  const other = visible.value.find((t) => t.pos_x === x && t.pos_y === y && t.id !== id)
  if (other) {
    const target = tables.value.find((t) => t.id === other.id)
    if (target) {
      target.pos_x = table.pos_x
      target.pos_y = table.pos_y
    }
  }

  table.pos_x = x
  table.pos_y = y
}

function setShape(shape: string) {
  if (!selected.value) return
  selected.value.shape = shape as Table['shape']
  // A bar is a counter and a booth is against a wall; both are wider than deep,
  // and leaving them 1×1 draws chairs where nobody sits.
  selected.value.span_x = shape === 'BAR' ? 3 : shape === 'RECT' ? 2 : 1
  selected.value.span_y = 1
}

async function save() {
  if (!changed.value.length) return
  saving.value = true
  try {
    await Promise.all(
      changed.value.map((t) =>
        api.patch(`/floor/tables/${t.id}/`, {
          pos_x: t.pos_x,
          pos_y: t.pos_y,
          shape: t.shape,
          span_x: t.span_x,
          span_y: t.span_y,
          seats: t.seats,
          rotation: t.rotation,
        }),
      ),
    )
    snapshot()
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
    if (!was) continue
    table.pos_x = was.x
    table.pos_y = was.y
    table.shape = was.shape as Table['shape']
    table.seats = was.seats
  }
}

function firstFreeCell(): { x: number; y: number } {
  for (let y = 0; y < 8; y += 1) {
    for (let x = 0; x < 10; x += 1) {
      if (!visible.value.some((t) => t.pos_x === x && t.pos_y === y)) return { x, y }
    }
  }
  return { x: 0, y: 0 }
}

async function addTable() {
  if (!newTable.value.number.trim() || !selectedArea.value) return
  creating.value = true
  try {
    // Dropped into the first free cell rather than 0,0 — a new table landing
    // under an existing one looks like it failed to appear.
    const free = firstFreeCell()
    await api.post('/floor/tables/', {
      area: selectedArea.value,
      number: newTable.value.number.trim(),
      seats: newTable.value.seats,
      shape: newTable.value.shape,
      span_x: newTable.value.shape === 'BAR' ? 3 : newTable.value.shape === 'RECT' ? 2 : 1,
      pos_x: free.x,
      pos_y: free.y,
    })
    newTable.value = { number: '', seats: 4, shape: 'SQUARE' }
    await load()
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر إضافة الطاولة.'
  } finally {
    creating.value = false
  }
}

onMounted(async () => {
  await load()
  timer = window.setInterval(refreshLive, REFRESH_MS)
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 class="text-2xl font-bold text-ink">🪑 الصالة</h1>
        <p class="mt-1 text-sm text-ink-muted">
          الشكل الحقيقي للغرفة — الكراسي المرسومة هي الكراسي الموجودة، والملوّنة عليها ناس دلوقتي.
        </p>
      </div>

      <div v-if="mayEdit" class="flex items-center gap-2">
        <UiButton :variant="editing ? 'primary' : 'secondary'" @click="editing = !editing">
          {{ editing ? 'إنهاء التعديل' : 'تعديل المخطط' }}
        </UiButton>
        <template v-if="editing">
          <UiButton v-if="changed.length" variant="ghost" @click="revert">تراجع</UiButton>
          <UiButton :disabled="!changed.length || saving" :loading="saving" @click="save">
            {{ changed.length ? `حفظ ${changed.length} تغيير` : 'لا توجد تغييرات' }}
          </UiButton>
        </template>
      </div>
    </div>

    <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>
    <UiAlert v-else-if="editing && changed.length" tone="warning">
      {{ changed.length }} طاولة غير محفوظة — لن تصل إلى الأجهزة قبل الحفظ.
    </UiAlert>
    <UiAlert v-if="editing && collisions" tone="warning">
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
              : 'bg-surface text-ink ring-[var(--border)] hover:bg-surface-muted'
          "
          @click="selectedArea = area.id"
        >
          {{ area.name_ar }}
        </button>
      </div>

      <div class="grid gap-4 xl:grid-cols-[1fr_20rem]">
        <RoomView
          :tables="visible"
          :stations="editing ? [] : stations"
          :editable="editing && mayEdit"
          :outdoor="isOutdoor"
          :selected-id="selectedId"
          @select="selectedId = $event.table_id"
          @move="move"
        />

        <div class="space-y-4">
          <UiCard v-if="selected">
            <div class="flex items-start justify-between gap-2">
              <h2 class="text-lg font-bold text-ink">طاولة {{ selected.number }}</h2>
              <span class="text-sm text-ink-muted">{{ selected.area_name }}</span>
            </div>

            <dl class="mt-3 space-y-1.5 text-sm">
              <div class="flex justify-between">
                <dt class="text-ink-muted">الكراسي</dt>
                <dd class="font-semibold tabular-nums">
                  {{ selectedLive?.seated_count ?? 0 }} / {{ selected.seats }}
                </dd>
              </div>
              <div v-if="selectedLive?.waiter" class="flex justify-between">
                <dt class="text-ink-muted">الويتر</dt>
                <dd>{{ selectedLive.waiter }}</dd>
              </div>
              <div v-if="selectedLive?.seated_minutes" class="flex justify-between">
                <dt class="text-ink-muted">جالسين من</dt>
                <dd class="tabular-nums">{{ selectedLive.seated_minutes }} دقيقة</dd>
              </div>
              <div v-if="selectedLive?.order_count" class="flex justify-between">
                <dt class="text-ink-muted">المستحق</dt>
                <dd class="font-semibold">{{ money(selectedLive.total_due) }}</dd>
              </div>
            </dl>

            <template v-if="editing && mayEdit">
              <div class="mt-4 border-t border-[var(--border)] pt-3">
                <span class="mb-1.5 block text-sm font-medium text-ink">الشكل</span>
                <div class="flex flex-wrap gap-1.5">
                  <button
                    v-for="option in SHAPES"
                    :key="option.key"
                    class="rounded-lg px-2.5 py-1.5 text-xs font-medium ring-1 ring-inset transition"
                    :class="
                      selected.shape === option.key
                        ? 'bg-brand-700 text-white ring-brand-700'
                        : 'bg-surface text-ink ring-[var(--border)] hover:bg-surface-muted'
                    "
                    @click="setShape(option.key)"
                  >
                    {{ option.label }}
                  </button>
                </div>

                <label class="mt-3 block">
                  <span class="mb-1.5 block text-sm font-medium text-ink">
                    عدد الكراسي: {{ selected.seats }}
                  </span>
                  <input
                    v-model.number="selected.seats"
                    type="range"
                    min="1"
                    max="12"
                    class="w-full"
                  />
                </label>

                <label class="mt-2 block">
                  <span class="mb-1.5 block text-sm font-medium text-ink">
                    الدوران: {{ selected.rotation }}°
                  </span>
                  <input
                    v-model.number="selected.rotation"
                    type="range"
                    min="0"
                    max="345"
                    step="15"
                    class="w-full"
                  />
                </label>

                <p class="mt-2 text-xs text-ink-faint">
                  الكراسي بتترتب لوحدها حسب الشكل — ستة حوالين مستطيل يبقوا ٣ و٣، مش ٢ على كل ضلع.
                </p>
              </div>
            </template>
          </UiCard>

          <UiCard v-else>
            <p class="text-sm text-ink-muted">اضغط على طاولة لعرض تفاصيلها.</p>
          </UiCard>

          <UiCard v-if="editing && mayEdit">
            <h2 class="text-sm font-semibold text-ink">إضافة طاولة</h2>
            <form class="mt-3 space-y-3" @submit.prevent="addTable">
              <UiInput v-model="newTable.number" label="رقم الطاولة" required />
              <UiInput v-model.number="newTable.seats" label="عدد الكراسي" type="number" />
              <label class="block">
                <span class="mb-1.5 block text-sm font-medium text-ink">الشكل</span>
                <select
                  v-model="newTable.shape"
                  class="min-h-[44px] w-full rounded-lg border border-strong bg-surface px-3"
                >
                  <option v-for="option in SHAPES" :key="option.key" :value="option.key">
                    {{ option.label }}
                  </option>
                </select>
              </label>
              <UiButton type="submit" :loading="creating" :disabled="!newTable.number.trim()">
                إضافة
              </UiButton>
            </form>
          </UiCard>
        </div>
      </div>
    </template>
  </div>
</template>
