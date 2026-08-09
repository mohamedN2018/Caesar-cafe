<script setup lang="ts">
/**
 * The branch's printers.
 *
 * Before this screen a printer was a string typed into each Desktop. Three
 * terminals meant three places to fix a typo, and the day the receipt printer
 * was replaced somebody walked to every till. Defined here, it reaches all of
 * them on the next config pull.
 *
 * Two things on this screen are worth reading twice.
 *
 * **The kind is not the paper size.** `RECEIPT` and `KITCHEN` describe what the
 * printer is FOR, which is what decides where its jobs come from; the width in
 * millimetres describes the roll. A cafe with one physical machine doing both
 * has two rows pointing at the same device, because the two jobs route
 * differently even when they land in the same place.
 *
 * **The device path here is the branch's default, not this machine's.**
 * `\\.\COM3` on the till by the door is not the same port as on the one at the
 * back, so each terminal can override it locally. Saying so on the screen is
 * cheaper than the support call from somebody who typed a port that is right on
 * exactly one of their three tills.
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
import { useAuthStore } from '@/stores/auth'

type Kind = 'RECEIPT' | 'KITCHEN' | 'REPORT'
type Connection = 'USB' | 'NETWORK' | 'SERIAL' | 'WINDOWS'

interface Printer {
  id: string
  name_ar: string
  code: string
  kind: Kind
  connection: Connection
  host: string
  port: number
  device_path: string
  paper_width_mm: number
  dots: number
  copies: number
  cut_after: boolean
  stations: string[]
  station_names: string[]
  is_default: boolean
  is_active: boolean
}

interface Station {
  id: string
  name_ar: string
  is_active: boolean
}

type Draft = Omit<Printer, 'id' | 'dots' | 'station_names'> & { id?: string }

const KINDS: { value: Kind; label: string; hint: string }[] = [
  { value: 'RECEIPT', label: 'فاتورة', hint: 'ما يأخذه العميل معه.' },
  { value: 'KITCHEN', label: 'مطبخ', hint: 'تذاكر التحضير.' },
  { value: 'REPORT', label: 'تقارير', hint: 'تقفيل الوردية وما شابه.' },
]

const CONNECTIONS: { value: Connection; label: string }[] = [
  { value: 'USB', label: 'USB' },
  { value: 'NETWORK', label: 'شبكة' },
  { value: 'SERIAL', label: 'سيريال' },
  { value: 'WINDOWS', label: 'ويندوز (مشاركة)' },
]

const EMPTY: Draft = {
  name_ar: '',
  code: '',
  kind: 'RECEIPT',
  connection: 'USB',
  host: '',
  port: 9100,
  device_path: '',
  paper_width_mm: 80,
  copies: 1,
  cut_after: true,
  stations: [],
  is_default: false,
  is_active: true,
}

const auth = useAuthStore()
const mayEdit = computed(() => auth.can('branch.manage_printers'))

const printers = ref<Printer[]>([])
const stations = ref<Station[]>([])
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const draft = ref<Draft>({ ...EMPTY })
const editing = ref(false)

const byKind = computed(() =>
  KINDS.map((kind) => ({
    ...kind,
    printers: printers.value.filter((p) => p.kind === kind.value),
  })).filter((group) => group.printers.length > 0),
)

/**
 * A kitchen with printers but none marked default is the case that ends with a
 * ticket sitting in the queue and nobody cooking, so it is called out rather
 * than left to be discovered during service.
 */
const kindsWithoutADefault = computed(() =>
  byKind.value
    .filter((group) => group.printers.some((p) => p.is_active) && !group.printers.some((p) => p.is_default && p.is_active))
    .map((group) => group.label),
)

const isNetwork = computed(() => draft.value.connection === 'NETWORK')
const isKitchen = computed(() => draft.value.kind === 'KITCHEN')

async function load() {
  loading.value = true
  try {
    printers.value = await api.get<Printer[]>('/printers/')
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تحميل الطابعات.'
  } finally {
    loading.value = false
  }

  // Optional: a manager who may edit printers but not stations still gets the
  // rest of the screen rather than an error about a list they cannot see.
  stations.value = (await api.optional<Station[]>('/kitchen/stations/')) ?? []
}

function edit(printer: Printer) {
  draft.value = { ...printer, stations: [...printer.stations] }
  editing.value = true
}

function reset() {
  draft.value = { ...EMPTY, stations: [] }
  editing.value = false
}

function toggleStation(id: string) {
  const chosen = draft.value.stations
  draft.value.stations = chosen.includes(id) ? chosen.filter((s) => s !== id) : [...chosen, id]
}

async function save() {
  if (!draft.value.name_ar.trim() || !draft.value.code.trim()) return
  saving.value = true
  try {
    const body = {
      name_ar: draft.value.name_ar.trim(),
      code: draft.value.code.trim(),
      kind: draft.value.kind,
      connection: draft.value.connection,
      host: isNetwork.value ? draft.value.host.trim() : '',
      port: draft.value.port,
      device_path: draft.value.device_path.trim(),
      paper_width_mm: draft.value.paper_width_mm,
      copies: draft.value.copies,
      cut_after: draft.value.cut_after,
      // Only a kitchen printer belongs to stations; sending them for a receipt
      // roll would route tickets to the counter.
      stations: isKitchen.value ? draft.value.stations : [],
      is_default: draft.value.is_default,
      is_active: draft.value.is_active,
    }
    if (draft.value.id) {
      await api.patch(`/printers/${draft.value.id}/`, body)
    } else {
      await api.post('/printers/', body)
    }
    reset()
    await load()
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر حفظ الطابعة.'
  } finally {
    saving.value = false
  }
}

async function makeDefault(printer: Printer) {
  try {
    await api.patch(`/printers/${printer.id}/`, { is_default: true })
    await load()
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تعيين الطابعة الافتراضية.'
  }
}

async function deactivate(printer: Printer) {
  try {
    await api.delete(`/printers/${printer.id}/`)
    await load()
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر إيقاف الطابعة.'
  }
}

function reach(printer: Printer): string {
  if (printer.connection === 'NETWORK') return `${printer.host}:${printer.port}`
  return printer.device_path || '—'
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-bold text-slate-900">🖨️ الطابعات</h1>
      <p class="mt-1 text-sm text-slate-500">
        تُعرَّف الطابعة هنا مرة واحدة للفرع، وتصل إلى كل كاشير في المزامنة التالية — بدل كتابتها
        على كل جهاز على حدة.
      </p>
    </div>

    <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>

    <UiAlert v-for="label in kindsWithoutADefault" :key="label" tone="warning">
      لا توجد طابعة افتراضية من نوع «{{ label }}». المهام التي لا تجد طابعة تبقى منتظرة في الطابور
      ولا تُطبع على طابعة أخرى.
    </UiAlert>

    <UiSkeleton v-if="loading" :rows="5" />

    <template v-else>
      <UiEmpty
        v-if="!printers.length"
        icon="🖨️"
        title="لا توجد طابعات معرَّفة"
        description="حتى تُعرَّف طابعة، يطبع كل كاشير على طابعته المحلية كما كان."
      />

      <div v-for="group in byKind" :key="group.value" class="space-y-2">
        <h2 class="text-sm font-semibold text-slate-700">
          {{ group.label }}
          <span class="font-normal text-slate-400">— {{ group.hint }}</span>
        </h2>

        <UiCard
          v-for="printer in group.printers"
          :key="printer.id"
          :class="printer.is_active ? '' : 'opacity-60'"
        >
          <div class="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div class="flex flex-wrap items-center gap-2">
                <span class="text-lg font-bold text-slate-900">{{ printer.name_ar }}</span>
                <UiBadge tone="neutral">
                  <span dir="ltr">{{ printer.code }}</span>
                </UiBadge>
                <UiBadge v-if="printer.is_default" tone="success">افتراضية</UiBadge>
                <UiBadge v-if="!printer.is_active" tone="warning">موقوفة</UiBadge>
              </div>

              <p class="mt-1 text-sm text-slate-500">
                <span dir="ltr">{{ reach(printer) }}</span>
                · {{ printer.paper_width_mm }}مم ({{ printer.dots }} نقطة)
                <span v-if="printer.copies > 1"> · {{ printer.copies }} نسخ</span>
              </p>

              <p v-if="printer.station_names.length" class="mt-1 text-sm text-slate-500">
                المحطات: {{ printer.station_names.join('، ') }}
              </p>
            </div>

            <div v-if="mayEdit" class="flex items-center gap-2">
              <UiButton size="sm" variant="secondary" @click="edit(printer)">تعديل</UiButton>
              <UiButton
                v-if="!printer.is_default && printer.is_active"
                size="sm"
                variant="ghost"
                @click="makeDefault(printer)"
              >
                اجعلها الافتراضية
              </UiButton>
              <UiButton v-if="printer.is_active" size="sm" variant="ghost" @click="deactivate(printer)">
                إيقاف
              </UiButton>
            </div>
          </div>
        </UiCard>
      </div>

      <UiCard v-if="mayEdit">
        <h2 class="text-sm font-semibold text-slate-900">
          {{ editing ? 'تعديل طابعة' : 'إضافة طابعة' }}
        </h2>

        <form class="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3" @submit.prevent="save">
          <UiInput v-model="draft.name_ar" label="الاسم" required />
          <UiInput v-model="draft.code" label="الكود" hint="يظهر في السجلات." ltr required />

          <label class="text-sm">
            <span class="mb-1 block font-medium text-slate-700">النوع</span>
            <select v-model="draft.kind" class="w-full rounded-lg border border-slate-300 px-3 py-2">
              <option v-for="kind in KINDS" :key="kind.value" :value="kind.value">
                {{ kind.label }}
              </option>
            </select>
          </label>

          <label class="text-sm">
            <span class="mb-1 block font-medium text-slate-700">الاتصال</span>
            <select
              v-model="draft.connection"
              class="w-full rounded-lg border border-slate-300 px-3 py-2"
            >
              <option v-for="option in CONNECTIONS" :key="option.value" :value="option.value">
                {{ option.label }}
              </option>
            </select>
          </label>

          <UiInput
            v-if="isNetwork"
            v-model="draft.host"
            label="عنوان IP"
            hint="طابعة الشبكة بلا عنوان طابعة لا يصلها شيء."
            ltr
            required
          />
          <UiInput v-if="isNetwork" v-model.number="draft.port" label="المنفذ" type="number" ltr />

          <UiInput
            v-else
            v-model="draft.device_path"
            label="مسار الجهاز (افتراضي الفرع)"
            hint="لكل كاشير أن يغيّره محلياً — المنفذ صفة الجهاز لا صفة الفرع."
            ltr
          />

          <label class="text-sm">
            <span class="mb-1 block font-medium text-slate-700">عرض الورق</span>
            <select
              v-model.number="draft.paper_width_mm"
              class="w-full rounded-lg border border-slate-300 px-3 py-2"
            >
              <option :value="80">80 مم</option>
              <option :value="58">58 مم</option>
            </select>
          </label>

          <UiInput v-model.number="draft.copies" label="عدد النسخ" type="number" />

          <div v-if="isKitchen" class="sm:col-span-2 lg:col-span-3">
            <span class="mb-1 block text-sm font-medium text-slate-700">المحطات</span>
            <p class="mb-2 text-xs text-slate-400">
              التذكرة تذهب إلى طابعة محطتها أولاً. بلا محطة، تذهب إلى الافتراضية.
            </p>
            <div class="flex flex-wrap gap-2">
              <button
                v-for="station in stations"
                :key="station.id"
                type="button"
                class="rounded-full border px-3 py-1 text-sm"
                :class="
                  draft.stations.includes(station.id)
                    ? 'border-transparent bg-slate-900 text-white'
                    : 'border-slate-300 text-slate-700'
                "
                @click="toggleStation(station.id)"
              >
                {{ station.name_ar }}
              </button>
            </div>
          </div>

          <label class="flex items-center gap-2 self-end pb-2 text-sm text-slate-700">
            <input v-model="draft.cut_after" type="checkbox" class="h-4 w-4 rounded" />
            قصّ الورق بعد الطباعة
          </label>

          <label class="flex items-center gap-2 self-end pb-2 text-sm text-slate-700">
            <input v-model="draft.is_default" type="checkbox" class="h-4 w-4 rounded" />
            الافتراضية لهذا النوع
          </label>

          <div class="flex items-center gap-2 sm:col-span-2 lg:col-span-3">
            <UiButton type="submit" :loading="saving">
              {{ editing ? 'حفظ' : 'إضافة' }}
            </UiButton>
            <UiButton v-if="editing" variant="ghost" @click="reset">إلغاء</UiButton>
          </div>
        </form>

        <p class="mt-3 text-xs text-slate-400">
          الطابعة تُوقَف ولا تُحذَف — قد تكون هناك مهمة في الطابور تحمل اسمها، ومهمة تشير إلى صف
          محذوف فاتورة تختفي دون سبب معلن.
        </p>
      </UiCard>
    </template>
  </div>
</template>
