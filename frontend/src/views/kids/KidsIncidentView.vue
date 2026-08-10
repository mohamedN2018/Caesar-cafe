<script setup lang="ts">
/**
 * The incident log.
 *
 * The failure modes of a play area are not financial, and this is the record of
 * them: a scraped knee, a dispute between parents, a lost shoe, a capacity
 * refusal. It exists for two reasons that have nothing to do with reporting.
 *
 * The first is the venue's own protection. "Nothing was reported" is not a
 * defensible answer three weeks later; a timestamped entry naming who wrote it
 * is.
 *
 * The second is that patterns only show in aggregate. One injury is bad luck.
 * Four injuries on the same slide in a month is a slide. The counts by type on
 * this page are the whole point of logging the boring ones.
 *
 * **Entries are append-only.** There is no edit and no delete, deliberately: a
 * log that can be tidied afterwards is a log nobody can rely on. The API offers
 * only GET and POST, so this is the server's rule, not a UI convention.
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
import { dateTime } from '@/lib/format'
import { useAuthStore } from '@/stores/auth'

interface Incident {
  id: string
  area: string
  session: string | null
  incident_type: string
  description: string
  occurred_at: string
  reported_by: string | null
}

interface Area {
  id: string
  name_ar: string
  is_active: boolean
}

const TYPES: Record<string, { label: string; tone: 'danger' | 'warning' | 'info' | 'neutral' }> = {
  INJURY: { label: 'إصابة', tone: 'danger' },
  DISPUTE: { label: 'خلاف', tone: 'warning' },
  LOST_ITEM: { label: 'فقد متعلقات', tone: 'info' },
  CAPACITY: { label: 'تجاوز السعة', tone: 'warning' },
  OTHER: { label: 'أخرى', tone: 'neutral' },
}

const auth = useAuthStore()
const mayLog = computed(() => auth.can('kids.log_incident'))

const incidents = ref<Incident[]>([])
const areas = ref<Area[]>([])
const loading = ref(true)
const error = ref('')
const saving = ref(false)
const typeFilter = ref('')

const draft = ref({ area: '', incident_type: 'INJURY', description: '' })

const visible = computed(() =>
  incidents.value
    .filter((i) => !typeFilter.value || i.incident_type === typeFilter.value)
    .slice()
    .sort((a, b) => b.occurred_at.localeCompare(a.occurred_at)),
)

/** One injury is bad luck; four on the same slide in a month is a slide. */
const counts = computed(() => {
  const tally: Record<string, number> = {}
  for (const incident of incidents.value) {
    tally[incident.incident_type] = (tally[incident.incident_type] ?? 0) + 1
  }
  return tally
})

const injuriesThisMonth = computed(() => {
  const since = new Date()
  since.setDate(since.getDate() - 30)
  return incidents.value.filter(
    (i) => i.incident_type === 'INJURY' && new Date(i.occurred_at) >= since,
  ).length
})

async function load() {
  loading.value = true
  try {
    const [incidentRows, areaRows] = await Promise.all([
      api.get<Incident[]>('/kids/incidents/'),
      api.get<Area[]>('/kids/areas/'),
    ])
    incidents.value = incidentRows
    areas.value = areaRows.filter((a) => a.is_active)
    if (!draft.value.area && areas.value.length) draft.value.area = areas.value[0].id
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تحميل السجل.'
  } finally {
    loading.value = false
  }
}

async function log() {
  if (!draft.value.area || !draft.value.description.trim()) return
  saving.value = true
  try {
    await api.post('/kids/incidents/log/', {
      area: draft.value.area,
      incident_type: draft.value.incident_type,
      description: draft.value.description.trim(),
    })
    draft.value.description = ''
    await load()
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تسجيل الواقعة.'
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-bold text-ink">سجل الوقائع</h1>
      <p class="mt-1 text-sm text-ink-muted">
        القيد لا يُعدَّل ولا يُحذَف. سجل يمكن ترتيبه لاحقاً هو سجل لا يُعتمد عليه.
      </p>
    </div>

    <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>
    <UiAlert v-if="injuriesThisMonth >= 3" tone="warning">
      {{ injuriesThisMonth }} إصابات خلال ٣٠ يوماً — راجع الألعاب والإشراف.
    </UiAlert>

    <UiSkeleton v-if="loading" :rows="5" />

    <template v-else>
      <div class="grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <UiCard v-for="(meta, key) in TYPES" :key="key">
          <p class="text-sm text-ink-muted">{{ meta.label }}</p>
          <p class="mt-1 text-2xl font-bold text-ink">{{ counts[key] ?? 0 }}</p>
        </UiCard>
      </div>

      <UiCard v-if="mayLog">
        <h2 class="text-sm font-semibold text-ink">تسجيل واقعة</h2>
        <form class="mt-3 grid gap-3 sm:grid-cols-3" @submit.prevent="log">
          <label class="block">
            <span class="mb-1.5 block text-sm font-medium text-ink">الصالة</span>
            <select
              v-model="draft.area"
              class="w-full min-h-[44px] rounded-lg border border-line-strong bg-surface px-3 text-[15px]
                     focus:border-brand-700 focus:outline-none focus:ring-2 focus:ring-brand-700/30"
            >
              <option v-for="area in areas" :key="area.id" :value="area.id">
                {{ area.name_ar }}
              </option>
            </select>
          </label>

          <label class="block">
            <span class="mb-1.5 block text-sm font-medium text-ink">النوع</span>
            <select
              v-model="draft.incident_type"
              class="w-full min-h-[44px] rounded-lg border border-line-strong bg-surface px-3 text-[15px]
                     focus:border-brand-700 focus:outline-none focus:ring-2 focus:ring-brand-700/30"
            >
              <option v-for="(meta, key) in TYPES" :key="key" :value="key">{{ meta.label }}</option>
            </select>
          </label>

          <UiInput v-model="draft.description" label="ماذا حدث" required />

          <div class="sm:col-span-3">
            <UiButton type="submit" :loading="saving" :disabled="!draft.description.trim()">
              تسجيل
            </UiButton>
          </div>
        </form>
      </UiCard>

      <div class="flex flex-wrap gap-2">
        <button
          class="rounded-lg px-3 py-2 text-sm font-medium ring-1 ring-inset transition"
          :class="
            typeFilter === ''
              ? 'bg-brand-50 text-brand-800 ring-brand-200'
              : 'bg-surface text-ink ring hover:bg-surface-muted'
          "
          @click="typeFilter = ''"
        >
          الكل
        </button>
        <button
          v-for="(meta, key) in TYPES"
          :key="key"
          class="rounded-lg px-3 py-2 text-sm font-medium ring-1 ring-inset transition"
          :class="
            typeFilter === key
              ? 'bg-brand-50 text-brand-800 ring-brand-200'
              : 'bg-surface text-ink ring hover:bg-surface-muted'
          "
          @click="typeFilter = String(key)"
        >
          {{ meta.label }}
        </button>
      </div>

      <UiEmpty
        v-if="!visible.length"
        icon="balloon"
        title="لا توجد وقائع"
        description="لا شيء مسجَّل — وهذا هو المطلوب."
      />

      <div v-else class="grid gap-3">
        <UiCard v-for="incident in visible" :key="incident.id">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <UiBadge :tone="TYPES[incident.incident_type]?.tone ?? 'neutral'">
                  {{ TYPES[incident.incident_type]?.label ?? incident.incident_type }}
                </UiBadge>
                <span class="text-sm text-ink-muted">{{ dateTime(incident.occurred_at) }}</span>
              </div>
              <p class="mt-2 text-sm text-ink">{{ incident.description }}</p>
            </div>
          </div>
        </UiCard>
      </div>
    </template>
  </div>
</template>
