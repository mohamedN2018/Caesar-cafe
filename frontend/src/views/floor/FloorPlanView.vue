<script setup lang="ts">
/**
 * The tables, and whether anybody is on them.
 *
 * This replaced a drawn room — a tilted 3D floor with chairs, shadows and
 * figures on the seats. It looked impressive and answered the wrong question.
 * The thing a manager glances at this screen for is "which tables are free and
 * which have been sitting a long time", and a picture of the room made that
 * *harder*: the tables were the size the layout dictated rather than the size
 * their information needed, half of them were behind a wall at the far edge,
 * and it never fitted a screen it had not been drawn for.
 *
 * So: a card per table, sorted by how much attention it wants. The board is
 * skimmed for colour and read for the two numbers that matter — how long they
 * have been sitting, and what is on the bill.
 *
 * The drag-to-position editor went with it. Coordinates only ever existed to
 * feed the picture; a table's number, seats and area are what the rest of the
 * system actually uses, and those are still editable here.
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'

import { ApiError, api } from '@/api/client'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiBadge from '@/components/ui/UiBadge.vue'
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
  is_active: boolean
}

interface LiveTable {
  table_id: string
  seated_count: number
  status: string
  session_id: string | null
  order_count: number
  total_due: string
  waiter: string | null
  seated_minutes: number | null
}

type Draft = { id?: string; area: string; number: string; seats: number }

/** How long a party may sit before the card starts asking to be looked at. */
const LINGERING_MINUTES = 90

const auth = useAuthStore()
const mayEdit = computed(() => auth.can('floor.manage_tables'))

type AreaDraft = { id?: string; name_ar: string; sort_order: number }

const EMPTY_AREA: AreaDraft = { name_ar: '', sort_order: 0 }

const areaDraft = ref<AreaDraft | null>(null)
const notice = ref('')

function flashArea(message: string) {
  notice.value = message
  setTimeout(() => (notice.value = ''), 4000)
}

const areas = ref<Area[]>([])
const tables = ref<Table[]>([])
const live = ref<Record<string, LiveTable>>({})
const loading = ref(true)
const error = ref('')
const saving = ref(false)
const activeArea = ref<string | null>(null)
const draft = ref<Draft | null>(null)

let polling: number | undefined

/**
 * The areas a table may be put in.
 *
 * `areas` now carries retired ones so this screen can bring one back; every
 * picker has to use THIS instead, or a table ends up in a room that is closed.
 */
const liveAreas = computed(() => areas.value.filter((a) => a.is_active))

const shown = computed(() => {
  const inArea = activeArea.value
    ? tables.value.filter((t) => t.area === activeArea.value)
    : tables.value

  return [...inArea]
    .filter((t) => t.is_active)
    .sort((a, b) => {
      // Occupied first, longest-seated at the top. A free table needs no
      // attention, and the party that has been there two hours is the one
      // somebody should walk over to.
      const left = live.value[a.id]
      const right = live.value[b.id]
      const leftMinutes = left?.seated_minutes ?? -1
      const rightMinutes = right?.seated_minutes ?? -1
      if (leftMinutes !== rightMinutes) return rightMinutes - leftMinutes
      return a.number.localeCompare(b.number, 'ar', { numeric: true })
    })
})

const busyCount = computed(() => shown.value.filter((t) => stateOf(t) !== 'free').length)

function stateOf(table: Table): 'free' | 'busy' | 'lingering' {
  const row = live.value[table.id]
  if (!row || row.session_id === null) return 'free'
  return (row.seated_minutes ?? 0) >= LINGERING_MINUTES ? 'lingering' : 'busy'
}

function seated(table: Table): LiveTable | undefined {
  return live.value[table.id]
}

function duration(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return ''
  if (minutes < 60) return `${minutes} د`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? `${hours} س ${rest} د` : `${hours} س`
}

async function load() {
  loading.value = true
  try {
    const [a, t] = await Promise.all([
      api.get<Area[]>('/floor/areas/'),
      api.get<Table[]>('/floor/tables/'),
    ])
    // Retired areas included, and sunk to the bottom.
    //
    // This filtered them out, which is right for a picker and wrong for the
    // screen that manages them: an area switched off by accident was invisible
    // here, so the only way to bring it back was the recycle bin — if somebody
    // knew to look.
    areas.value = a
      .slice()
      .sort(
        (x, y) =>
          Number(y.is_active) - Number(x.is_active) ||
          x.sort_order - y.sort_order ||
          x.name_ar.localeCompare(y.name_ar, 'ar'),
      )
    tables.value = t
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تحميل الطاولات.'
  } finally {
    loading.value = false
  }
  await refreshLive()
}

async function refreshLive() {
  // Optional: somebody who may see the layout but not the live board gets the
  // tables without occupancy rather than an error about a thing they did not
  // ask for.
  const rows = await api.optional<LiveTable[]>('/floor/status/')
  if (!rows) return
  live.value = Object.fromEntries(rows.map((r) => [r.table_id, r]))
}

function edit(table: Table) {
  draft.value = { id: table.id, area: table.area, number: table.number, seats: table.seats }
}

function add() {
  draft.value = { area: activeArea.value ?? areas.value[0]?.id ?? '', number: '', seats: 4 }
}

async function save() {
  const current = draft.value
  if (!current?.number.trim() || !current.area) return

  saving.value = true
  try {
    const body = { area: current.area, number: current.number.trim(), seats: current.seats }
    if (current.id) {
      await api.patch(`/floor/tables/${current.id}/`, body)
    } else {
      await api.post('/floor/tables/', body)
    }
    draft.value = null
    await load()
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر حفظ الطاولة.'
  } finally {
    saving.value = false
  }
}

/** A room: the terrace, the inside hall, the mezzanine. */
function addArea() {
  areaDraft.value = { ...EMPTY_AREA, sort_order: areas.value.length }
}

function editArea(area: Area) {
  areaDraft.value = { id: area.id, name_ar: area.name_ar, sort_order: area.sort_order }
}

async function saveArea() {
  const current = areaDraft.value
  if (!current) return
  const name = current.name_ar.trim()
  if (!name) {
    error.value = 'اسم المنطقة مطلوب.'
    return
  }

  saving.value = true
  try {
    const body = { name_ar: name, sort_order: current.sort_order }
    if (current.id) {
      await api.patch(`/floor/areas/${current.id}/`, body)
      flashArea(`تم حفظ «${name}».`)
    } else {
      await api.post('/floor/areas/', body)
      flashArea(`تمت إضافة «${name}».`)
    }
    areaDraft.value = null
    await load()
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر حفظ المنطقة.'
  } finally {
    saving.value = false
  }
}

/**
 * Retire a room, or open it again.
 *
 * The confirmation names its table count, because that is the consequence and it
 * is invisible otherwise: retiring an area takes every table in it off the floor
 * screen, and the person doing it is thinking about a room.
 *
 * Refused for the last live one — a branch with no area has nowhere to put a
 * table, and the table form's select would be empty with nothing saying why.
 */
async function toggleArea(area: Area) {
  if (area.is_active) {
    if (liveAreas.value.length <= 1) {
      error.value = 'لا يمكن إيقاف آخر منطقة — الطاولات لا بد أن تكون في منطقة.'
      return
    }
    const count = tables.value.filter((x) => x.area === area.id).length
    const carries = count ? `\n\nستختفي ${count} طاولة من شاشة الصالة.` : ''
    if (!window.confirm(`إيقاف المنطقة «${area.name_ar}»؟${carries}`)) return
  }

  try {
    if (area.is_active) {
      await api.delete(`/floor/areas/${area.id}/`)
      flashArea(`تم إيقاف «${area.name_ar}» — يمكن استرجاعها من «المحذوفات».`)
    } else {
      await api.patch(`/floor/areas/${area.id}/`, { is_active: true })
      flashArea(`تم تفعيل «${area.name_ar}».`)
    }
    if (activeArea.value === area.id) activeArea.value = null
    await load()
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تغيير حالة المنطقة.'
  }
}

async function deactivate(table: Table) {
  if (stateOf(table) !== 'free') {
    error.value = 'الطاولة مشغولة — لا يمكن إيقافها وعليها جلسة مفتوحة.'
    return
  }
  try {
    await api.delete(`/floor/tables/${table.id}/`)
    await load()
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر إيقاف الطاولة.'
  }
}

onMounted(async () => {
  await load()
  // Ten seconds: long enough not to hammer the API, short enough that a table
  // that just cleared does not stay red while somebody is standing at it.
  polling = window.setInterval(refreshLive, 10_000)
})
onUnmounted(() => window.clearInterval(polling))
</script>

<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 class="text-2xl font-bold text-ink">الطاولات</h1>
        <p class="mt-1 text-sm text-ink-muted">
          {{ busyCount }} مشغولة من {{ shown.length }}
        </p>
      </div>
      <div v-if="mayEdit" class="flex items-center gap-2">
        <UiButton variant="secondary" @click="addArea">إضافة منطقة</UiButton>
        <UiButton :disabled="!liveAreas.length" @click="add">إضافة طاولة</UiButton>
      </div>
    </div>

    <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>
    <UiAlert v-if="notice" tone="success">{{ notice }}</UiAlert>

    <!--
      The rooms, above the tables in them.
      
      This screen could add, move and retire a TABLE and had no way to create the
      area a table needs — so a café opening a terrace could not draw one, and the
      only areas that existed were the two the seed wrote.
    -->
    <UiCard v-if="mayEdit">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <h2 class="text-sm font-semibold text-ink">المناطق</h2>
        <span class="text-xs text-ink-faint">
          المنطقة تُوقَف ولا تُحذَف — طاولاتها مسجّلة على فواتير سابقة.
        </span>
      </div>

      <ul class="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        <li
          v-for="area in areas"
          :key="area.id"
          class="flex items-center justify-between gap-2 rounded-lg border border-border px-3 py-2"
          :class="area.is_active ? '' : 'opacity-60'"
        >
          <div class="min-w-0">
            <p class="truncate text-sm font-medium text-ink">{{ area.name_ar }}</p>
            <p class="text-xs text-ink-muted">
              {{ tables.filter((x) => x.area === area.id).length }} طاولة
              <span v-if="!area.is_active"> · موقوفة</span>
            </p>
          </div>
          <div class="flex shrink-0 items-center gap-1">
            <UiButton size="sm" variant="ghost" @click="editArea(area)">تعديل</UiButton>
            <UiButton size="sm" variant="ghost" @click="toggleArea(area)">
              {{ area.is_active ? 'إيقاف' : 'تفعيل' }}
            </UiButton>
          </div>
        </li>
      </ul>

      <form
        v-if="areaDraft"
        class="mt-3 grid gap-3 border-t border-border pt-3 sm:grid-cols-3"
        @submit.prevent="saveArea"
      >
        <UiInput v-model="areaDraft.name_ar" label="اسم المنطقة" required />
        <UiInput
          v-model.number="areaDraft.sort_order"
          label="الترتيب"
          type="number"
          hint="هو ترتيب تابات المناطق في شاشة الكاشير."
        />
        <div class="flex items-end gap-2">
          <UiButton type="submit" :loading="saving">
            {{ areaDraft.id ? 'حفظ' : 'إضافة' }}
          </UiButton>
          <UiButton variant="ghost" @click="areaDraft = null">إلغاء</UiButton>
        </div>
      </form>

      <p v-if="!liveAreas.length" class="mt-3 text-xs text-warning">
        لا توجد منطقة مفعّلة — لا يمكن إضافة طاولة قبل إنشاء منطقة.
      </p>
    </UiCard>

    <div v-if="areas.length > 1" class="flex flex-wrap gap-2">
      <button
        type="button"
        class="area"
        :class="{ 'is-on': activeArea === null }"
        @click="activeArea = null"
      >
        الكل
      </button>
      <button
        v-for="area in areas"
        :key="area.id"
        type="button"
        class="area"
        :class="{ 'is-on': activeArea === area.id }"
        @click="activeArea = area.id"
      >
        {{ area.name_ar }}
        <span v-if="!area.is_active" class="text-warning"> (موقوفة)</span>
      </button>
    </div>

    <UiSkeleton v-if="loading" :rows="5" />

    <template v-else>
      <UiEmpty
        v-if="!shown.length"
        icon="table"
        title="لا توجد طاولات"
        description="أضف طاولة لتظهر هنا وعلى شاشة الكاشير."
      />

      <div v-else class="tables">
        <article
          v-for="table in shown"
          :key="table.id"
          class="table-card"
          :class="`is-${stateOf(table)}`"
        >
          <header class="card-head">
            <span class="number">{{ table.number }}</span>
            <span class="seats">{{ table.seats }} كرسي</span>
          </header>

          <p class="state">
            <template v-if="stateOf(table) === 'free'">فاضية</template>
            <template v-else>
              {{ seated(table)?.seated_count || '؟' }} أشخاص
              <span v-if="seated(table)?.seated_minutes !== null">
                · {{ duration(seated(table)?.seated_minutes) }}
              </span>
            </template>
          </p>

          <p v-if="seated(table)?.waiter" class="who">{{ seated(table)?.waiter }}</p>

          <p v-if="Number(seated(table)?.total_due)" class="due tabular-nums">
            {{ money(seated(table)?.total_due ?? '0') }}
          </p>

          <UiBadge v-if="stateOf(table) === 'lingering'" tone="warning">
            قاعدين من زمان
          </UiBadge>

          <footer v-if="mayEdit" class="card-foot">
            <button type="button" @click="edit(table)">تعديل</button>
            <button type="button" @click="deactivate(table)">إيقاف</button>
          </footer>
        </article>
      </div>
    </template>

    <UiCard v-if="draft" :title="draft.id ? 'تعديل طاولة' : 'إضافة طاولة'">
      <form class="grid gap-3 sm:grid-cols-3" @submit.prevent="save">
        <UiInput v-model="draft.number" label="الرقم" required />
        <UiInput v-model.number="draft.seats" label="عدد الكراسي" type="number" />
        <label class="text-sm">
          <span class="mb-1.5 block font-medium text-ink">المنطقة</span>
          <select v-model="draft.area" class="w-full rounded-lg border border-border-line-strong px-3 py-2">
            <!--
              Live areas only. A table attached to a retired area is a table in a
              room that is closed — it would render on a floor plan nobody is
              seating anybody in.
            -->
            <option v-for="area in liveAreas" :key="area.id" :value="area.id">
              {{ area.name_ar }}
            </option>
          </select>
        </label>
        <div class="flex gap-2 sm:col-span-3">
          <UiButton type="submit" :loading="saving">حفظ</UiButton>
          <UiButton variant="ghost" @click="draft = null">إلغاء</UiButton>
        </div>
      </form>
    </UiCard>
  </div>
</template>

<style scoped>
.area {
  padding: 0.5rem 1.1rem;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--ink-muted);
  font-weight: 600;
  font-size: 0.9rem;
}
.area.is-on {
  background: var(--brand-700);
  border-color: var(--brand-700);
  color: var(--fg-on-brand);
}

.tables {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(10rem, 1fr));
  gap: 0.75rem;
}

.table-card {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  padding: 0.9rem;
  border-radius: 0.85rem;
  border: 1px solid var(--border);
  background: var(--surface);
  /* The state is carried by a thick inline edge rather than a full colour
     wash: a wall of saturated cards is unreadable, and the edge survives being
     skimmed from across a room. */
  border-inline-start: 5px solid var(--table-free);
}
.table-card.is-free {
  border-inline-start-color: var(--border-strong);
  opacity: 0.75;
}
.table-card.is-busy {
  border-inline-start-color: var(--brand-700);
}
.table-card.is-lingering {
  border-inline-start-color: var(--warning);
  background: var(--warning-bg);
}

.card-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.5rem;
}
.number {
  font-size: 1.4rem;
  font-weight: 800;
  color: var(--ink);
}
.seats {
  font-size: 0.75rem;
  color: var(--ink-faint);
}

.state {
  font-size: 0.9rem;
  color: var(--ink-muted);
}
.who {
  font-size: 0.78rem;
  color: var(--ink-faint);
}
.due {
  font-size: 1.05rem;
  font-weight: 700;
  color: var(--ink);
}

.card-foot {
  display: flex;
  gap: 0.4rem;
  margin-top: 0.4rem;
}
.card-foot button {
  flex: 1 1 auto;
  padding: 0.4rem;
  border-radius: 0.45rem;
  background: var(--surface-sunken);
  color: var(--ink-muted);
  font-size: 0.8rem;
  font-weight: 600;
}
</style>
