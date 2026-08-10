<script setup lang="ts">
/**
 * Kitchen stations.
 *
 * A station is where an item is made — the coffee bar, the grill, the pastry
 * counter. Products point at one, and that pointer is what routes a fired order
 * into the right ticket. Which makes this a small screen with real consequences:
 * a product whose station is wrong is a coffee printed at the grill.
 *
 * `target_prep_minutes` is the number the whole lateness display is built on.
 * It is per-station because the honest targets differ — an espresso is late at
 * three minutes and a grill order is not late at ten — and a single global
 * target would make one station permanently red and the other permanently green,
 * at which point nobody reads the colour at all.
 *
 * **A station is deactivated, never deleted.** Its tickets are history, and
 * history with a dangling station reference is a report that cannot name where
 * the food was made.
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

interface Station {
  id: string
  code: string
  name_ar: string
  target_prep_minutes: number
  auto_accept: boolean
  printer_name: string
  sort_order: number
  is_active: boolean
}

type Draft = Omit<Station, 'id'> & { id?: string }

const EMPTY: Draft = {
  code: '',
  name_ar: '',
  target_prep_minutes: 8,
  auto_accept: false,
  printer_name: '',
  sort_order: 0,
  is_active: true,
}

const auth = useAuthStore()
const mayEdit = computed(() => auth.can('kitchen.manage_stations'))

const stations = ref<Station[]>([])
const loading = ref(true)
const error = ref('')
const saving = ref(false)
const draft = ref<Draft>({ ...EMPTY })
const editing = ref(false)

const sorted = computed(() =>
  stations.value.slice().sort((a, b) => a.sort_order - b.sort_order || a.name_ar.localeCompare(b.name_ar)),
)

async function load() {
  loading.value = true
  try {
    stations.value = await api.get<Station[]>('/kitchen/stations/')
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تحميل المحطات.'
  } finally {
    loading.value = false
  }
}

function edit(station: Station) {
  draft.value = { ...station }
  editing.value = true
}

function reset() {
  draft.value = { ...EMPTY }
  editing.value = false
}

async function save() {
  if (!draft.value.name_ar.trim() || !draft.value.code.trim()) return
  saving.value = true
  try {
    const body = {
      code: draft.value.code.trim(),
      name_ar: draft.value.name_ar.trim(),
      target_prep_minutes: draft.value.target_prep_minutes,
      auto_accept: draft.value.auto_accept,
      printer_name: draft.value.printer_name.trim(),
      sort_order: draft.value.sort_order,
      is_active: draft.value.is_active,
    }
    if (draft.value.id) {
      await api.patch(`/kitchen/stations/${draft.value.id}/`, body)
    } else {
      await api.post('/kitchen/stations/', body)
    }
    reset()
    await load()
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر حفظ المحطة.'
  } finally {
    saving.value = false
  }
}

async function toggleActive(station: Station) {
  try {
    await api.patch(`/kitchen/stations/${station.id}/`, { is_active: !station.is_active })
    await load()
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تغيير حالة المحطة.'
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-bold text-ink">محطات المطبخ</h1>
      <p class="mt-1 text-sm text-ink-muted">
        المحطة تحدّد أين يُحضَّر الصنف وأين تُطبع تذكرته. الوقت المستهدف هو أساس حساب التأخير.
      </p>
    </div>

    <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>

    <UiSkeleton v-if="loading" :rows="5" />

    <template v-else>
      <UiEmpty
        v-if="!stations.length"
        icon="station"
        title="لا توجد محطات"
        description="أضف محطة واحدة على الأقل، وإلا لن يُوجَّه أي صنف إلى المطبخ."
      />

      <div v-else class="grid gap-3">
        <UiCard
          v-for="station in sorted"
          :key="station.id"
          :class="station.is_active ? '' : 'opacity-60'"
        >
          <div class="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div class="flex flex-wrap items-center gap-2">
                <span class="text-lg font-bold text-ink">{{ station.name_ar }}</span>
                <UiBadge tone="neutral">
                  <span dir="ltr">{{ station.code }}</span>
                </UiBadge>
                <UiBadge v-if="!station.is_active" tone="warning">موقوفة</UiBadge>
                <UiBadge v-if="station.auto_accept" tone="info">قبول تلقائي</UiBadge>
              </div>
              <p class="mt-1 text-sm text-ink-muted">
                الوقت المستهدف {{ station.target_prep_minutes }} دقيقة
                <span v-if="station.printer_name">
                  · الطابعة <span dir="ltr">{{ station.printer_name }}</span>
                </span>
              </p>
            </div>

            <div v-if="mayEdit" class="flex items-center gap-2">
              <UiButton size="sm" variant="secondary" @click="edit(station)">تعديل</UiButton>
              <UiButton size="sm" variant="ghost" @click="toggleActive(station)">
                {{ station.is_active ? 'إيقاف' : 'تفعيل' }}
              </UiButton>
            </div>
          </div>
        </UiCard>
      </div>

      <UiCard v-if="mayEdit">
        <h2 class="text-sm font-semibold text-ink">
          {{ editing ? 'تعديل محطة' : 'إضافة محطة' }}
        </h2>

        <form class="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3" @submit.prevent="save">
          <UiInput v-model="draft.name_ar" label="الاسم" required />
          <UiInput
            v-model="draft.code"
            label="الكود"
            hint="يُستخدم في التقارير والتوجيه."
            ltr
            required
          />
          <UiInput
            v-model.number="draft.target_prep_minutes"
            label="الوقت المستهدف (دقيقة)"
            type="number"
            hint="الإسبريسو يتأخر عند ٣ دقائق، والمشويّ لا. لذلك لكل محطة رقمها."
          />
          <UiInput
            v-model="draft.printer_name"
            label="اسم الطابعة"
            hint="اتركه فارغاً إذا كانت المحطة بلا طابعة."
            ltr
          />
          <UiInput v-model.number="draft.sort_order" label="الترتيب" type="number" />

          <label class="flex items-center gap-2 self-end pb-2 text-sm text-ink">
            <input v-model="draft.auto_accept" type="checkbox" class="h-4 w-4 rounded" />
            قبول التذاكر تلقائياً
          </label>

          <div class="flex items-center gap-2 sm:col-span-2 lg:col-span-3">
            <UiButton type="submit" :loading="saving">
              {{ editing ? 'حفظ' : 'إضافة' }}
            </UiButton>
            <UiButton v-if="editing" variant="ghost" @click="reset">إلغاء</UiButton>
          </div>
        </form>

        <p class="mt-3 text-xs text-ink-faint">
          المحطة تُوقَف ولا تُحذَف — تذاكرها تاريخ، والتاريخ بمرجع محذوف تقرير لا يعرف أين حُضِّر
          الطعام.
        </p>
      </UiCard>
    </template>
  </div>
</template>
