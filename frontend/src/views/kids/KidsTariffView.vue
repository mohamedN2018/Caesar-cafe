<script setup lang="ts">
/**
 * The tariff builder, with worked examples.
 *
 * The examples come from the SERVER — `/kids/tariffs/{id}/preview/` runs the
 * same `compute_charge` a real checkout runs. Calculating them here would be a
 * second pricing implementation, and a second implementation is exactly how the
 * number an admin sees while designing a rule drifts from the number a parent
 * is charged under it.
 *
 * **The write side used to be missing entirely.** This route is gated on
 * `kids.manage_tariffs` and the screen could only read: a manage permission on a
 * screen that managed nothing. The endpoint had accepted POST, PATCH and DELETE
 * the whole time, so the rules a café charges children by were whatever the seed
 * wrote.
 *
 * `priority` is the field worth typing carefully: when two tariffs both apply,
 * the higher priority wins. Two rules at the same priority is a charge that
 * depends on row order, which is a different price on different days.
 */
import { computed, onMounted, ref } from 'vue'

import { ApiError, api } from '@/api/client'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiBadge from '@/components/ui/UiBadge.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiInput from '@/components/ui/UiInput.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import UiTable from '@/components/ui/UiTable.vue'
import { money } from '@/lib/format'
import { useAuthStore } from '@/stores/auth'

interface Tariff {
  id: string
  area: string
  name_ar: string
  mode: 'TIMED' | 'PACKAGE' | 'OPEN_DAY'
  entry_fee: string
  included_minutes: number
  package_minutes: number
  block_minutes: number
  block_rate: string
  grace_minutes: number | null
  daily_cap: string
  applies_days: number[]
  applies_from: string | null
  applies_to: string | null
  priority: number
  is_default: boolean
  is_active: boolean
}

interface Preview {
  minutes: number
  charge: string
  billable_minutes: number
  blocks: number
  capped: boolean
}

const DAY_NAMES = ['الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت', 'الأحد']

const MODE_LABELS: Record<Tariff['mode'], string> = {
  TIMED: 'عدّاد',
  PACKAGE: 'باقة',
  OPEN_DAY: 'يوم مفتوح',
}

type TariffDraft = {
  id?: string
  area: string
  name_ar: string
  mode: 'TIMED' | 'PACKAGE' | 'OPEN_DAY'
  entry_fee: string
  included_minutes: number
  package_minutes: number
  block_minutes: number
  block_rate: string
  grace_minutes: number
  daily_cap: string
  priority: number
  is_default: boolean
}

const MODES = [
  { value: 'TIMED', label: 'بالوقت — دخول ثم كل فترة برسم' },
  { value: 'PACKAGE', label: 'باكيدج — مدة محددة بسعر واحد' },
  { value: 'OPEN_DAY', label: 'يوم مفتوح — سعر واحد لليوم' },
] as const

const EMPTY_TARIFF: TariffDraft = {
  area: '',
  name_ar: '',
  mode: 'TIMED',
  entry_fee: '0.00',
  included_minutes: 0,
  package_minutes: 0,
  block_minutes: 30,
  block_rate: '0.00',
  grace_minutes: 5,
  daily_cap: '0.00',
  priority: 0,
  is_default: false,
}

const auth = useAuthStore()
const mayEdit = computed(() => auth.can('kids.manage_tariffs'))

const areas = ref<{ id: string; name_ar: string }[]>([])
const error = ref('')
const notice = ref('')
const saving = ref(false)
const draft = ref<TariffDraft>({ ...EMPTY_TARIFF })
const editing = ref(false)
const formOpen = ref(false)

function flash(message: string) {
  notice.value = message
  setTimeout(() => (notice.value = ''), 4000)
}

function newTariff() {
  draft.value = { ...EMPTY_TARIFF, area: areas.value[0]?.id ?? '' }
  editing.value = false
  formOpen.value = true
}

function editTariff(tariff: Tariff) {
  draft.value = {
    id: tariff.id,
    area: tariff.area,
    name_ar: tariff.name_ar,
    mode: tariff.mode,
    entry_fee: tariff.entry_fee,
    included_minutes: tariff.included_minutes,
    package_minutes: tariff.package_minutes,
    block_minutes: tariff.block_minutes,
    block_rate: tariff.block_rate,
    grace_minutes: tariff.grace_minutes ?? 0,
    daily_cap: tariff.daily_cap,
    priority: tariff.priority,
    is_default: tariff.is_default,
  }
  editing.value = true
  formOpen.value = true
}

function closeForm() {
  draft.value = { ...EMPTY_TARIFF }
  editing.value = false
  formOpen.value = false
}

async function saveTariff() {
  const name = draft.value.name_ar.trim()
  if (!name) {
    error.value = 'اسم التعريفة مطلوب.'
    return
  }
  if (!draft.value.area) {
    error.value = 'اختر منطقة الأطفال — التعريفة تتبع منطقة.'
    return
  }
  // Checked here because the server would accept it and the result is a rule that
  // charges nothing: a TIMED tariff with no block rate and no entry fee is a free
  // session that looks configured.
  if (
    draft.value.mode === 'TIMED' &&
    !Number(draft.value.block_rate) &&
    !Number(draft.value.entry_fee)
  ) {
    error.value = 'تعريفة بالوقت بلا رسم دخول وبلا سعر فترة تعني جلسة مجانية.'
    return
  }

  saving.value = true
  try {
    const body = {
      area: draft.value.area,
      name_ar: name,
      mode: draft.value.mode,
      entry_fee: draft.value.entry_fee || '0.00',
      included_minutes: draft.value.included_minutes,
      package_minutes: draft.value.package_minutes,
      block_minutes: draft.value.block_minutes,
      block_rate: draft.value.block_rate || '0.00',
      grace_minutes: draft.value.grace_minutes,
      daily_cap: draft.value.daily_cap || '0.00',
      priority: draft.value.priority,
      is_default: draft.value.is_default,
    }
    if (draft.value.id) {
      await api.patch(`/kids/tariffs/${draft.value.id}/`, body)
      flash(`تم حفظ «${name}».`)
    } else {
      await api.post('/kids/tariffs/', body)
      flash(`تمت إضافة «${name}».`)
    }
    closeForm()
    await load()
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر حفظ التعريفة.'
  } finally {
    saving.value = false
  }
}

/**
 * Retire a tariff, or bring it back.
 *
 * The default one is refused: check-in resolves a tariff for every session, and
 * with no default an area whose rules do not match the day charges nothing at
 * all. Make another one the default first.
 */
async function toggleTariff(tariff: Tariff) {
  if (tariff.is_active) {
    if (tariff.is_default) {
      error.value = 'لا يمكن إيقاف التعريفة الافتراضية — اجعل غيرها افتراضية أولاً.'
      return
    }
    // `globalThis`, not `window`: this file declares its own `window(tariff)`
    // helper for a tariff's applicable hours, which shadows the global — so
    // `window.confirm` resolves to a function that returns a string, and the
    // guard would have been permanently true. The typechecker caught it; a
    // browser would have deleted without asking.
    if (!globalThis.confirm(`إيقاف التعريفة «${tariff.name_ar}»؟`)) return
  }

  try {
    if (tariff.is_active) {
      await api.delete(`/kids/tariffs/${tariff.id}/`)
      flash(`تم إيقاف «${tariff.name_ar}» — يمكن استرجاعها من «المحذوفات».`)
    } else {
      await api.patch(`/kids/tariffs/${tariff.id}/`, { is_active: true })
      flash(`تم تفعيل «${tariff.name_ar}».`)
    }
    await load()
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تغيير حالة التعريفة.'
  }
}

const tariffs = ref<Tariff[]>([])
const previews = ref<Record<string, Preview[]>>({})
const expanded = ref<string>('')
const loading = ref(true)

const previewColumns = [
  { key: 'minutes', label: 'المدة' },
  { key: 'charge', label: 'المستحق', align: 'end' as const },
  { key: 'why', label: 'التفسير' },
]

/** A one-line statement of the rule, built from the fields — no arithmetic. */
function describe(tariff: Tariff): string {
  if (tariff.mode === 'OPEN_DAY') {
    return `${money(tariff.entry_fee)} لليوم كاملاً بدون حد للمدة.`
  }
  const covered = tariff.mode === 'PACKAGE' ? tariff.package_minutes : tariff.included_minutes
  const parts = [`${money(tariff.entry_fee)} تشمل ${covered} دقيقة`]
  if (tariff.block_minutes > 0) {
    parts.push(`ثم ${money(tariff.block_rate)} لكل ${tariff.block_minutes} دقيقة`)
  }
  if (tariff.grace_minutes !== null) parts.push(`سماح ${tariff.grace_minutes} دقيقة`)
  if (Number(tariff.daily_cap) > 0) parts.push(`بحد أقصى ${money(tariff.daily_cap)}`)
  return `${parts.join('، ')}.`
}

function window(tariff: Tariff): string {
  const days = tariff.applies_days?.length
    ? tariff.applies_days.map((d) => DAY_NAMES[d] ?? d).join('، ')
    : 'كل الأيام'
  const hours =
    tariff.applies_from && tariff.applies_to
      ? `${tariff.applies_from.slice(0, 5)} — ${tariff.applies_to.slice(0, 5)}`
      : 'طوال اليوم'
  return `${days} · ${hours}`
}

function explain(row: Preview, tariff: Tariff): string {
  if (row.capped) return 'وصل الحد الأقصى'
  if (tariff.mode === 'OPEN_DAY') return 'سعر ثابت'
  if (row.blocks === 0) return 'داخل الفترة المشمولة أو مهلة السماح'
  return `${row.blocks} فترة إضافية · محتسب ${row.billable_minutes} دقيقة`
}

async function toggle(tariff: Tariff) {
  expanded.value = expanded.value === tariff.id ? '' : tariff.id
  if (expanded.value && !previews.value[tariff.id]) {
    previews.value[tariff.id] = await api.get<Preview[]>(`/kids/tariffs/${tariff.id}/preview/`)
  }
}

/**
 * Named, because the write actions reload through it.
 *
 * It was inline in `onMounted` — fine for a screen that only reads once, and not
 * once saving exists: after a PATCH the list has to come back from the server
 * rather than being patched in memory, or the worked examples beside each tariff
 * would still be the OLD rule's.
 */
async function load() {
  loading.value = true
  try {
    const [rows, playAreas] = await Promise.all([
      api.get<Tariff[]>('/kids/tariffs/'),
      api.optional<{ id: string; name_ar: string }[]>('/kids/areas/'),
    ])
    tariffs.value = rows
    areas.value = playAreas ?? []
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تحميل التعريفات.'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-bold text-ink">تعريفات صالة الأطفال</h1>
      <p class="mt-1 text-sm text-ink-muted">
        الأمثلة محسوبة على الخادم بنفس الكود الذي يحاسب به الكاشير.
      </p>
    </div>

    <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>
    <UiAlert v-if="notice" tone="success">{{ notice }}</UiAlert>

    <div v-if="mayEdit" class="flex items-center gap-2">
      <UiButton @click="newTariff">تعريفة جديدة</UiButton>
    </div>

    <!--
      The form, inline like every other management screen in this admin.

      The fields shown follow the MODE, because most of them are meaningless in
      the other two: a package has a duration and one price, a timed tariff has an
      entry fee and a rate per block, and showing all of it at once is how
      somebody fills in `block_rate` on an open-day rule and wonders why it
      changed nothing.
    -->
    <UiCard v-if="formOpen">
      <h2 class="text-sm font-semibold text-ink">
        {{ editing ? 'تعديل تعريفة' : 'تعريفة جديدة' }}
      </h2>

      <form class="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3" @submit.prevent="saveTariff">
        <UiInput v-model="draft.name_ar" label="الاسم" required />

        <label class="text-sm text-ink">
          <span class="mb-1 block font-medium">المنطقة</span>
          <select
            v-model="draft.area"
            class="w-full rounded-lg border border-line-strong bg-surface px-3 py-2.5 text-sm"
            required
          >
            <option value="" disabled>اختر المنطقة…</option>
            <option v-for="a in areas" :key="a.id" :value="a.id">{{ a.name_ar }}</option>
          </select>
        </label>

        <label class="text-sm text-ink">
          <span class="mb-1 block font-medium">النوع</span>
          <select
            v-model="draft.mode"
            class="w-full rounded-lg border border-line-strong bg-surface px-3 py-2.5 text-sm"
          >
            <option v-for="m in MODES" :key="m.value" :value="m.value">{{ m.label }}</option>
          </select>
        </label>

        <UiInput
          v-model="draft.entry_fee"
          label="رسم الدخول"
          type="number"
          step="0.01"
          hint="يُحصَّل مرة واحدة عند الدخول."
        />

        <template v-if="draft.mode === 'TIMED'">
          <UiInput
            v-model.number="draft.included_minutes"
            label="دقائق مشمولة"
            type="number"
            hint="مشمولة في رسم الدخول قبل بداية الحساب."
          />
          <UiInput v-model.number="draft.block_minutes" label="طول الفترة (دقيقة)" type="number" />
          <UiInput v-model="draft.block_rate" label="سعر الفترة" type="number" step="0.01" />
          <UiInput
            v-model.number="draft.grace_minutes"
            label="سماح (دقيقة)"
            type="number"
            hint="دقائق لا تُحاسب في بداية فترة جديدة."
          />
        </template>

        <template v-if="draft.mode === 'PACKAGE'">
          <UiInput v-model.number="draft.package_minutes" label="مدة الباكيدج (دقيقة)" type="number" />
        </template>

        <UiInput
          v-model="draft.daily_cap"
          label="الحد الأقصى اليومي"
          type="number"
          step="0.01"
          hint="صفر يعني بلا حد."
        />
        <UiInput
          v-model.number="draft.priority"
          label="الأولوية"
          type="number"
          hint="عند تطابق تعريفتين تفوز الأعلى. تعريفتان بنفس الأولوية تعنيان سعراً يتبع ترتيب الصفوف."
        />

        <label class="flex items-center gap-2 self-end pb-2 text-sm text-ink">
          <input v-model="draft.is_default" type="checkbox" class="h-4 w-4 rounded" />
          التعريفة الافتراضية للمنطقة
        </label>

        <div class="flex items-center gap-2 sm:col-span-2 lg:col-span-3">
          <UiButton type="submit" :loading="saving">{{ editing ? 'حفظ' : 'إضافة' }}</UiButton>
          <UiButton variant="ghost" @click="closeForm">إلغاء</UiButton>
        </div>
      </form>

      <p class="mt-3 text-xs text-ink-faint">
        الأمثلة المحسوبة أسفل كل تعريفة تأتي من السيرفر — نفس الحساب الذي يُطبَّق على أي طفل
        فعلياً. بعد الحفظ راجعها: هي أسرع طريقة لاكتشاف قاعدة لا تعني ما تظنه.
      </p>
    </UiCard>

    <UiSkeleton v-if="loading" :rows="5" />

    <UiEmpty
      v-else-if="!tariffs.length"
      icon="ticket"
      title="لا توجد تعريفات"
      description="أضف تعريفة واحدة على الأقل حتى يمكن تسجيل دخول طفل."
    />

    <div v-else class="grid gap-3">
      <UiCard v-for="tariff in tariffs" :key="tariff.id">
        <div class="flex flex-wrap items-start justify-between gap-4">
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <span class="text-lg font-bold text-ink">{{ tariff.name_ar }}</span>
              <UiBadge tone="info">{{ MODE_LABELS[tariff.mode] }}</UiBadge>
              <UiBadge v-if="tariff.is_default" tone="success">افتراضية</UiBadge>
              <UiBadge v-if="!tariff.is_active" tone="neutral">موقوفة</UiBadge>
            </div>
            <p class="mt-1 text-sm text-ink-muted">{{ describe(tariff) }}</p>
            <p class="mt-0.5 text-xs text-ink-faint">
              {{ window(tariff) }} · أولوية {{ tariff.priority }}
            </p>
          </div>

          <div class="flex flex-wrap items-center gap-2">
            <button
              class="rounded-lg px-3 py-2 text-sm font-medium text-brand-800 ring-1 ring-inset ring-brand-200 hover:bg-brand-50"
              @click="toggle(tariff)"
            >
              {{ expanded === tariff.id ? 'إخفاء الأمثلة' : 'أمثلة محسوبة' }}
            </button>
            <template v-if="mayEdit">
              <UiButton size="sm" variant="secondary" @click="editTariff(tariff)">تعديل</UiButton>
              <UiButton size="sm" variant="ghost" @click="toggleTariff(tariff)">
                {{ tariff.is_active ? 'إيقاف' : 'تفعيل' }}
              </UiButton>
            </template>
          </div>
        </div>

        <div v-if="expanded === tariff.id" class="mt-4 border-t border-line pt-4">
          <UiSkeleton v-if="!previews[tariff.id]" :rows="4" />
          <UiTable v-else :columns="previewColumns">
            <tr v-for="row in previews[tariff.id]" :key="row.minutes">
              <td class="px-4 py-2 tabular-nums text-ink">{{ row.minutes }} دقيقة</td>
              <td class="px-4 py-2 text-end font-medium tabular-nums text-ink">
                {{ money(row.charge) }}
              </td>
              <td class="px-4 py-2 text-sm text-ink-muted">{{ explain(row, tariff) }}</td>
            </tr>
          </UiTable>
        </div>
      </UiCard>
    </div>
  </div>
</template>
