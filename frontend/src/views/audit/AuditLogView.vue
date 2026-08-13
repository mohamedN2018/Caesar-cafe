<script setup lang="ts">
/**
 * The audit trail.
 *
 * Read-only, because the log is. There is no edit control here for the same
 * reason there is no write endpoint: a trail somebody can adjust is a record of
 * what they most recently claimed.
 *
 * The action filter is built from `/audit/actions/` rather than hardcoded, so
 * adding an audited action makes it filterable without touching this file.
 */
import { computed, onMounted, ref, watch } from 'vue'

import { ApiError, api } from '@/api/client'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiBadge from '@/components/ui/UiBadge.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import { dateTime } from '@/lib/format'

interface Entry {
  id: number
  action: string
  label_ar: string
  domain: string
  severity: 'INFO' | 'NOTICE' | 'WARNING'
  actor_name: string
  approved_by_name: string
  object_type: string
  object_label: string
  changes: Record<string, [unknown, unknown]>
  detail: Record<string, unknown>
  ip_address: string | null
  request_id: string
  occurred_at: string
}

interface ActionDef {
  code: string
  domain: string
  label_ar: string
  severity: string
}

const SEVERITY_TONE: Record<Entry['severity'], 'neutral' | 'info' | 'warning'> = {
  INFO: 'neutral',
  NOTICE: 'info',
  WARNING: 'warning',
}

const entries = ref<Entry[]>([])
const actions = ref<ActionDef[]>([])
const loading = ref(true)
const error = ref('')
const expanded = ref<number | null>(null)

const filterDomain = ref('')
const filterAction = ref('')
const filterSeverity = ref('')

const domains = computed(() => [...new Set(actions.value.map((a) => a.domain))].sort())
const actionsInDomain = computed(() =>
  filterDomain.value ? actions.value.filter((a) => a.domain === filterDomain.value) : actions.value,
)
const warningCount = computed(() => entries.value.filter((e) => e.severity === 'WARNING').length)

async function load() {
  loading.value = true
  try {
    const params: Record<string, string> = {}
    if (filterAction.value) params.action = filterAction.value
    else if (filterDomain.value) params.domain = filterDomain.value
    if (filterSeverity.value) params.severity = filterSeverity.value

    entries.value = await api.get<Entry[]>('/audit/', params)
    error.value = ''
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.message : 'تعذّر تحميل السجل.'
  } finally {
    loading.value = false
  }
}

function summarise(entry: Entry): string {
  const parts = Object.entries(entry.changes).map(
    ([field, [before, after]]) => `${field}: ${before ?? '—'} → ${after ?? '—'}`,
  )
  if (parts.length) return parts.join(' · ')

  const reason = entry.detail.reason
  return typeof reason === 'string' && reason ? reason : ''
}

watch([filterDomain, filterAction, filterSeverity], load)

onMounted(async () => {
  actions.value = await api.get<ActionDef[]>('/audit/actions/').catch(() => [])
  await load()
})
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-bold text-ink">سجل التدقيق</h1>
      <p class="mt-1 text-sm text-ink-muted">
        آخر ٣٠ يوماً. السجل للقراءة فقط — لا يمكن تعديل سطر ولا حذفه.
      </p>
    </div>

    <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>

    <UiCard>
      <div class="flex flex-wrap items-end gap-3">
        <label class="text-sm text-ink">
          المجال
          <select
            v-model="filterDomain"
            class="mt-1 block rounded-lg border border-line-strong px-3 py-2 text-sm"
            @change="filterAction = ''"
          >
            <option value="">الكل</option>
            <option v-for="domain in domains" :key="domain" :value="domain">{{ domain }}</option>
          </select>
        </label>
        <label class="text-sm text-ink">
          الإجراء
          <select
            v-model="filterAction"
            class="mt-1 block rounded-lg border border-line-strong px-3 py-2 text-sm"
          >
            <option value="">الكل</option>
            <option v-for="action in actionsInDomain" :key="action.code" :value="action.code">
              {{ action.label_ar }}
            </option>
          </select>
        </label>
        <label class="text-sm text-ink">
          الأهمية
          <select
            v-model="filterSeverity"
            class="mt-1 block rounded-lg border border-line-strong px-3 py-2 text-sm"
          >
            <option value="">الكل</option>
            <option value="WARNING">تحذير</option>
            <option value="NOTICE">ملاحظة</option>
            <option value="INFO">معلومة</option>
          </select>
        </label>
        <p v-if="!loading" class="pb-2 text-sm text-ink-muted">
          {{ entries.length }} سطر · {{ warningCount }} تحذير
        </p>
      </div>
    </UiCard>

    <UiSkeleton v-if="loading" :rows="8" />

    <UiEmpty
      v-else-if="!entries.length"
      icon="clipboard"
      title="لا توجد سجلات"
      description="لا يوجد شيء مطابق في آخر ٣٠ يوماً."
    />

    <UiCard v-else>
      <ul class="divide-y divide-line">
        <li v-for="entry in entries" :key="entry.id" class="py-3">
          <button
            class="w-full text-start"
            @click="expanded = expanded === entry.id ? null : entry.id"
          >
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <UiBadge :tone="SEVERITY_TONE[entry.severity]">{{ entry.label_ar }}</UiBadge>
                  <span v-if="entry.object_label" class="font-medium text-ink">
                    {{ entry.object_label }}
                  </span>
                </div>
                <p class="mt-1 text-sm text-ink-muted">
                  {{ entry.actor_name || 'النظام' }}
                  <span v-if="entry.approved_by_name" class="text-ink-muted">
                    · بموافقة {{ entry.approved_by_name }}
                  </span>
                </p>
                <p v-if="summarise(entry)" class="mt-0.5 text-sm text-ink-muted">
                  {{ summarise(entry) }}
                </p>
              </div>
              <div class="shrink-0 text-end">
                <p class="text-sm text-ink-muted">{{ dateTime(entry.occurred_at) }}</p>
                <p v-if="entry.ip_address" class="font-mono text-xs text-ink-faint" dir="ltr">
                  {{ entry.ip_address }}
                </p>
              </div>
            </div>
          </button>

          <div v-if="expanded === entry.id" class="mt-3 space-y-2 rounded-lg bg-surface-muted p-3">
            <p class="font-mono text-xs text-ink-muted" dir="ltr">
              {{ entry.action }} · request {{ entry.request_id || '—' }}
            </p>
            <pre
              v-if="Object.keys(entry.detail).length"
              class="overflow-x-auto rounded bg-surface p-2 text-xs text-ink"
              dir="ltr"
            >{{ JSON.stringify(entry.detail, null, 2) }}</pre>
            <pre
              v-if="Object.keys(entry.changes).length"
              class="overflow-x-auto rounded bg-surface p-2 text-xs text-ink"
              dir="ltr"
            >{{ JSON.stringify(entry.changes, null, 2) }}</pre>
          </div>
        </li>
      </ul>
    </UiCard>
  </div>
</template>
