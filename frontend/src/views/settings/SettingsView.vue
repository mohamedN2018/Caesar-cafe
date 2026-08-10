<script setup lang="ts">
/**
 * The settings screen renders ITSELF from the server's registry (C10).
 *
 * There is no hardcoded list of settings here. Adding one on the backend is a
 * single `register(...)` call and it appears here — correctly typed, labelled,
 * grouped, and validated — with no frontend change at all. That property is
 * what makes "everything is configurable" survivable rather than a permanent
 * tax on the team.
 */
import { computed, onMounted, ref } from 'vue'

import { ApiError, api } from '@/api/client'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiBadge from '@/components/ui/UiBadge.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import { useAuthStore } from '@/stores/auth'

interface Definition {
  key: string
  type: 'string' | 'text' | 'integer' | 'decimal' | 'boolean' | 'time' | 'enum' | 'list' | 'json'
  scope: 'ORGANIZATION' | 'BRANCH' | 'DEVICE' | 'ROLE'
  default: unknown
  label_ar: string
  label_en: string
  help_ar: string
  choices: string[]
  permission: string
  high_impact: boolean
  affects_open_orders: boolean
  pushes_to_desktop: boolean
}

interface Resolved {
  value: unknown
  origin: string
  is_default: boolean
}

const auth = useAuthStore()

const groups = ref<Record<string, Definition[]>>({})
const values = ref<Record<string, Resolved>>({})
const drafts = ref<Record<string, unknown>>({})
const activeGroup = ref('')
const search = ref('')
const loading = ref(true)
const saving = ref(false)
const message = ref<{ tone: 'success' | 'error'; text: string } | null>(null)
const fieldErrors = ref<Record<string, string[]>>({})

const GROUP_LABELS: Record<string, string> = {
  organization: 'عام',
  finance: 'المالية',
  orders: 'الطلبات',
  discounts: 'الخصومات',
  payments: 'الدفع',
  floor: 'الصالة',
  kitchen: 'المطبخ',
  kids: 'صالة الأطفال',
  inventory: 'المخزون',
  purchasing: 'المشتريات',
  shifts: 'الورديات',
  licensing: 'التراخيص',
  sync: 'المزامنة',
  security: 'الأمان',
}

const groupNames = computed(() => Object.keys(groups.value).sort())

/** Search spans every group — with ~180 settings, browsing alone does not scale. */
const filtered = computed(() => {
  const term = search.value.trim().toLowerCase()
  if (!term) return groups.value[activeGroup.value] ?? []

  return Object.values(groups.value)
    .flat()
    .filter(
      (definition) =>
        definition.key.toLowerCase().includes(term) ||
        definition.label_ar.includes(term) ||
        definition.help_ar.includes(term),
    )
})

const dirty = computed(() =>
  Object.entries(drafts.value).filter(([key, value]) => value !== values.value[key]?.value),
)

onMounted(load)

async function load() {
  loading.value = true
  try {
    const [schema, resolved] = await Promise.all([
      api.get<{ groups: Record<string, Definition[]>; count: number }>('/settings/schema/'),
      api.get<{ settings: Record<string, Resolved> }>('/settings/'),
    ])
    groups.value = schema.groups
    values.value = resolved.settings
    drafts.value = Object.fromEntries(
      Object.entries(resolved.settings).map(([key, item]) => [key, item.value]),
    )
    activeGroup.value = groupNames.value[0] ?? ''
  } catch (error) {
    message.value = {
      tone: 'error',
      text: error instanceof ApiError ? error.message : 'تعذر تحميل الإعدادات',
    }
  } finally {
    loading.value = false
  }
}

function scopeIdFor(definition: Definition): string | null {
  if (definition.scope === 'ORGANIZATION') return auth.me?.organization_id ?? null
  if (definition.scope === 'BRANCH') return auth.me?.branch_id ?? null
  return null
}

async function save() {
  saving.value = true
  message.value = null
  fieldErrors.value = {}

  // Group by scope: one request per scope, because each write names one target.
  const byScope = new Map<string, Record<string, unknown>>()
  for (const [key] of dirty.value) {
    const definition = Object.values(groups.value).flat().find((d) => d.key === key)
    if (!definition) continue
    const scopeId = scopeIdFor(definition)
    if (!scopeId) continue

    const bucket = `${definition.scope}:${scopeId}`
    byScope.set(bucket, { ...(byScope.get(bucket) ?? {}), [key]: drafts.value[key] })
  }

  try {
    let failures = 0
    for (const [bucket, payload] of byScope) {
      const [scope, scopeId] = bucket.split(':')
      const result = await api.patch<{ applied: Record<string, unknown>; errors: Record<string, string[]> }>(
        '/settings/',
        { scope, scope_id: scopeId, values: payload },
      )
      if (Object.keys(result.errors).length) {
        fieldErrors.value = { ...fieldErrors.value, ...result.errors }
        failures += Object.keys(result.errors).length
      }
    }

    await load()
    message.value = failures
      ? { tone: 'error', text: `تم الحفظ مع ${failures} خطأ — راجع الحقول المميزة.` }
      : { tone: 'success', text: 'تم حفظ الإعدادات.' }
  } catch (error) {
    message.value = {
      tone: 'error',
      text: error instanceof ApiError ? error.message : 'تعذر حفظ الإعدادات',
    }
  } finally {
    saving.value = false
  }
}

async function reset(definition: Definition) {
  const scopeId = scopeIdFor(definition)
  if (!scopeId) return
  await api.delete(`/settings/${definition.key}/?scope=${definition.scope}&scope_id=${scopeId}`)
  await load()
}

function listValue(key: string): string {
  const value = drafts.value[key]
  return Array.isArray(value) ? value.join('، ') : String(value ?? '')
}

function setList(key: string, text: string) {
  drafts.value[key] = text
    .split(/[،,\n]/)
    .map((part) => part.trim())
    .filter(Boolean)
}
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 class="text-2xl font-bold text-ink">الإعدادات</h1>
        <p class="mt-1 text-sm text-ink-muted">
          كل قيمة في النظام تتغيّر من هنا — بدون تحديث للبرنامج.
        </p>
      </div>
      <UiButton :disabled="!dirty.length" :loading="saving" @click="save">
        حفظ التغييرات
        <span v-if="dirty.length" class="rounded-full bg-surface/20 px-2 text-xs">
          {{ dirty.length }}
        </span>
      </UiButton>
    </div>

    <UiAlert v-if="message" :tone="message.tone">{{ message.text }}</UiAlert>

    <UiSkeleton v-if="loading" :rows="8" />

    <div v-else class="grid gap-6 lg:grid-cols-[220px_1fr]">
      <aside class="space-y-3">
        <input
          v-model="search"
          type="search"
          placeholder="بحث في الإعدادات…"
          class="w-full rounded-lg border border-line-strong px-3 py-2.5 text-sm
                 focus:border-brand-700 focus:outline-none focus:ring-2 focus:ring-brand-700/30"
        />
        <nav v-if="!search" class="space-y-1">
          <button
            v-for="name in groupNames"
            :key="name"
            class="w-full rounded-lg px-3 py-2.5 text-start text-sm font-medium transition"
            :class="
              activeGroup === name ? 'bg-brand-50 text-brand-800' : 'text-ink hover:bg-surface-sunken'
            "
            @click="activeGroup = name"
          >
            {{ GROUP_LABELS[name] ?? name }}
            <span class="text-xs text-ink-faint">({{ groups[name].length }})</span>
          </button>
        </nav>
      </aside>

      <UiCard :title="search ? `نتائج البحث (${filtered.length})` : GROUP_LABELS[activeGroup] ?? activeGroup">
        <div class="divide-y divide-line">
          <div v-for="definition in filtered" :key="definition.key" class="py-5 first:pt-0">
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div class="min-w-0 flex-1">
                <div class="flex flex-wrap items-center gap-2">
                  <label :for="definition.key" class="font-medium text-ink">
                    {{ definition.label_ar }}
                  </label>
                  <!-- A dot marks anything changed from default: six months in,
                       "what did we actually change?" must be answerable at a glance. -->
                  <span
                    v-if="!values[definition.key]?.is_default"
                    class="h-2 w-2 rounded-full bg-brand-600"
                    title="معدّل عن الافتراضي"
                  />
                  <UiBadge v-if="definition.high_impact" tone="warning">مؤثر</UiBadge>
                  <UiBadge v-if="definition.affects_open_orders" tone="info">
                    يسري على الطلبات الجديدة
                  </UiBadge>
                </div>
                <p v-if="definition.help_ar" class="mt-1 text-sm text-ink-muted">
                  {{ definition.help_ar }}
                </p>
                <p class="mt-1 font-mono text-xs text-ink-faint" dir="ltr">
                  {{ definition.key }} · {{ values[definition.key]?.origin }}
                </p>
                <p v-if="fieldErrors[definition.key]" class="mt-1 text-sm text-danger">
                  {{ fieldErrors[definition.key].join('، ') }}
                </p>
              </div>

              <div class="flex w-full items-center gap-2 sm:w-64">
                <input
                  v-if="definition.type === 'boolean'"
                  :id="definition.key"
                  v-model="drafts[definition.key] as boolean"
                  type="checkbox"
                  class="h-6 w-6 rounded border-line-strong text-brand-700 focus:ring-brand-700"
                  :disabled="!auth.can(definition.permission)"
                />
                <select
                  v-else-if="definition.type === 'enum'"
                  :id="definition.key"
                  v-model="drafts[definition.key] as string"
                  class="w-full rounded-lg border border-line-strong px-3 py-2.5 text-sm min-h-[44px]"
                  :disabled="!auth.can(definition.permission)"
                >
                  <option v-for="choice in definition.choices" :key="choice" :value="choice">
                    {{ choice }}
                  </option>
                </select>
                <input
                  v-else-if="definition.type === 'list'"
                  :id="definition.key"
                  :value="listValue(definition.key)"
                  class="w-full rounded-lg border border-line-strong px-3 py-2.5 text-sm min-h-[44px]"
                  :disabled="!auth.can(definition.permission)"
                  @input="setList(definition.key, ($event.target as HTMLInputElement).value)"
                />
                <input
                  v-else
                  :id="definition.key"
                  v-model="drafts[definition.key] as string"
                  :type="definition.type === 'integer' || definition.type === 'decimal' ? 'number' : 'text'"
                  :step="definition.type === 'decimal' ? '0.01' : undefined"
                  :dir="definition.type === 'time' ? 'ltr' : undefined"
                  class="w-full rounded-lg border border-line-strong px-3 py-2.5 text-sm min-h-[44px]"
                  :disabled="!auth.can(definition.permission)"
                />

                <button
                  v-if="!values[definition.key]?.is_default && auth.can(definition.permission)"
                  class="shrink-0 rounded-lg px-2 py-2 text-xs text-ink-muted hover:bg-surface-sunken"
                  title="استعادة الافتراضي"
                  @click="reset(definition)"
                >
                  ↺
                </button>
              </div>
            </div>
          </div>
        </div>
      </UiCard>
    </div>
  </div>
</template>
