<script setup lang="ts">
/**
 * The live board — what the owner watches from home.
 *
 * Colour follows time state but never carries the meaning alone: the warning
 * text and the countdown say the same thing, for colour-blind users and for the
 * washed-out screens these actually run on.
 *
 * The running charge is displayed exactly as the server sent it. This screen
 * does no pricing arithmetic of its own — the server computes, the client
 * displays, and a second implementation here is how the two start disagreeing.
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'

import { api } from '@/api/client'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiBadge from '@/components/ui/UiBadge.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import { money, time } from '@/lib/format'

interface Session {
  id: string
  tag_number: string
  status: string
  child_name: string
  age_months: number
  guardian_name: string
  guardian_phone: string
  tariff_name: string
  checked_in_at: string
  expected_end_at: string | null
  elapsed_minutes: number
  remaining_minutes: number | null
  is_overdue: boolean
  running_charge: string
  capped: boolean
  medical_notes: string
}

interface Board {
  area_id: string
  area_name: string
  occupancy: number
  capacity: number
  sessions: Session[]
}

interface Area {
  id: string
  name_ar: string
  max_capacity: number
  is_active: boolean
}

const REFRESH_MS = 15_000
/** Matches the `kids.warn_before_end_minutes` default; purely cosmetic here. */
const WARN_MINUTES = 10

const areas = ref<Area[]>([])
const selected = ref<string>('')
const board = ref<Board | null>(null)
const loading = ref(true)
const error = ref('')
let timer: number | undefined

const occupancyPercent = computed(() => {
  if (!board.value?.capacity) return 0
  return Math.min(100, Math.round((board.value.occupancy / board.value.capacity) * 100))
})

const isFull = computed(() => (board.value ? board.value.occupancy >= board.value.capacity : false))

const revenueRunning = computed(() =>
  (board.value?.sessions ?? []).reduce((sum, s) => sum + Number(s.running_charge), 0),
)

function elapsed(minutes: number): string {
  const hours = Math.floor(minutes / 60)
  return `${hours}:${String(minutes % 60).padStart(2, '0')}`
}

function tone(session: Session): 'danger' | 'warning' | 'success' {
  if (session.is_overdue) return 'danger'
  if (session.remaining_minutes !== null && session.remaining_minutes <= WARN_MINUTES) {
    return 'warning'
  }
  return 'success'
}

function stateLabel(session: Session): string {
  if (session.is_overdue) return 'تجاوز الوقت'
  if (session.remaining_minutes !== null && session.remaining_minutes <= WARN_MINUTES) {
    return `باقي ${session.remaining_minutes} د`
  }
  if (session.remaining_minutes !== null) return `باقي ${session.remaining_minutes} د`
  return 'مفتوحة'
}

async function loadBoard() {
  if (!selected.value) return
  try {
    board.value = await api.get<Board>(`/kids/areas/${selected.value}/board/`)
    error.value = ''
  } catch {
    // Keep the last board on screen — a blank screen during a blip is worse
    // than a slightly stale one, as long as it says so.
    error.value = 'تعذّر تحديث اللوحة — البيانات المعروضة قد تكون قديمة.'
  }
}

async function pick(id: string) {
  selected.value = id
  await loadBoard()
}

onMounted(async () => {
  try {
    areas.value = (await api.get<Area[]>('/kids/areas/')).filter((a) => a.is_active)
    if (areas.value.length) await pick(areas.value[0].id)
    timer = window.setInterval(loadBoard, REFRESH_MS)
  } finally {
    loading.value = false
  }
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 class="text-2xl font-bold text-ink">صالة الأطفال</h1>
        <p class="mt-1 text-sm text-ink-muted">
          يتحدّث كل {{ REFRESH_MS / 1000 }} ثانية. القيمة المعروضة محسوبة على الخادم.
        </p>
      </div>

      <div v-if="areas.length > 1" class="flex flex-wrap gap-2">
        <button
          v-for="area in areas"
          :key="area.id"
          class="rounded-lg px-3 py-2 text-sm font-medium ring-1 ring-inset transition"
          :class="
            selected === area.id
              ? 'bg-brand-50 text-brand-800 ring-brand-200'
              : 'bg-surface text-ink ring hover:bg-surface-muted'
          "
          @click="pick(area.id)"
        >
          {{ area.name_ar }}
        </button>
      </div>
    </div>

    <UiAlert v-if="error" tone="warning">{{ error }}</UiAlert>

    <UiSkeleton v-if="loading" :rows="6" />

    <UiEmpty
      v-else-if="!areas.length"
      icon="kids"
      title="لا توجد صالة أطفال"
      description="أضف صالة من الإعدادات لتظهر هنا."
    />

    <template v-else-if="board">
      <div class="grid gap-4 sm:grid-cols-3">
        <UiCard>
          <p class="text-sm text-ink-muted">الإشغال</p>
          <p class="mt-1 text-2xl font-bold" :class="isFull ? 'text-danger' : 'text-ink'">
            {{ board.occupancy }} / {{ board.capacity }}
          </p>
          <div class="mt-3 h-2 overflow-hidden rounded-full bg-surface-sunken">
            <div
              class="h-full rounded-full transition-all"
              :class="isFull ? 'bg-danger' : 'bg-brand-600'"
              :style="{ width: `${occupancyPercent}%` }"
            />
          </div>
          <p v-if="isFull" class="mt-2 text-xs font-medium text-danger">
            ممتلئة — لا يمكن إدخال طفل آخر
          </p>
        </UiCard>
        <UiCard>
          <p class="text-sm text-ink-muted">مستحق جارٍ</p>
          <p class="mt-1 text-2xl font-bold text-ink">{{ money(revenueRunning) }}</p>
          <p class="mt-2 text-xs text-ink-faint">يُحتسب عند الخروج، وليس قبله.</p>
        </UiCard>
        <UiCard>
          <p class="text-sm text-ink-muted">متجاوزون الوقت</p>
          <p
            class="mt-1 text-2xl font-bold"
            :class="board.sessions.some((s) => s.is_overdue) ? 'text-warning' : 'text-ink'"
          >
            {{ board.sessions.filter((s) => s.is_overdue).length }}
          </p>
        </UiCard>
      </div>

      <UiEmpty
        v-if="!board.sessions.length"
        icon="balloon"
        title="الصالة فارغة"
        description="لا يوجد أطفال بالداخل الآن."
      />

      <div v-else class="grid gap-3">
        <UiCard
          v-for="session in board.sessions"
          :key="session.id"
          class="border-s-4"
          :class="{
            'border-s-red-500': tone(session) === 'danger',
            'border-s-amber-400': tone(session) === 'warning',
            'border-s-emerald-500': tone(session) === 'success',
          }"
        >
          <div class="flex flex-wrap items-start justify-between gap-4">
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <span class="font-mono text-sm font-bold text-ink-muted" dir="ltr">
                  #{{ session.tag_number }}
                </span>
                <span class="text-lg font-bold text-ink">{{ session.child_name }}</span>
                <UiBadge :tone="tone(session)">{{ stateLabel(session) }}</UiBadge>
                <UiBadge v-if="session.capped" tone="info">وصل الحد الأقصى</UiBadge>
              </div>

              <p class="mt-1 text-sm text-ink-muted">
                ولي الأمر: {{ session.guardian_name }}
                <span v-if="session.guardian_phone" class="font-mono text-ink-faint" dir="ltr">
                  · {{ session.guardian_phone }}
                </span>
              </p>
              <p class="mt-0.5 text-sm text-ink-muted">
                {{ session.tariff_name }} · دخول {{ time(session.checked_in_at) }}
                <span v-if="session.expected_end_at">
                  · تنتهي {{ time(session.expected_end_at) }}
                </span>
              </p>
              <p
                v-if="session.medical_notes"
                class="mt-2 rounded-lg bg-warning-bg px-3 py-1.5 text-sm text-warning"
              >
                ⚕️ {{ session.medical_notes }}
              </p>
            </div>

            <div class="text-end">
              <p class="font-mono text-2xl font-bold tabular-nums text-ink" dir="ltr">
                {{ elapsed(session.elapsed_minutes) }}
              </p>
              <p class="mt-1 text-sm font-medium text-ink">
                {{ money(session.running_charge) }}
              </p>
            </div>
          </div>
        </UiCard>
      </div>
    </template>
  </div>
</template>
