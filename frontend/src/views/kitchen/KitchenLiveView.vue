<script setup lang="ts">
/**
 * The kitchen, from the office.
 *
 * This is not a second KDS. The cook's board is on the Desktop, where one tap
 * advances a ticket and nothing needs a mouse. This screen answers the owner's
 * question instead — *is the kitchen keeping up?* — and it answers it from home,
 * over the internet, which the Desktop cannot do (C11).
 *
 * So the emphasis is different. The KDS sorts oldest-first because a cook works
 * the queue; this sorts oldest-first too, but leads with **how many are late and
 * by how much**, because that is the number that decides whether to call someone
 * in. Prep times per station sit underneath, since "the coffee bar is slow after
 * 8pm" is a staffing decision, not a today decision.
 *
 * Read-only by default. An owner watching from home advancing a ticket they
 * cannot see the plate for is how a customer gets told their food is ready when
 * it is not — so the transition buttons appear only for `kitchen.update_status`,
 * and even then say what they do rather than showing an arrow.
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'

import { ApiError, api } from '@/api/client'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiBadge from '@/components/ui/UiBadge.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import { time } from '@/lib/format'
import { useAuthStore } from '@/stores/auth'

interface TicketLine {
  id: string
  name: string
  quantity: string
  modifiers: string[]
  note: string
  ready_at: string | null
}

interface Ticket {
  id: string
  ticket_number: number
  status: string
  order_number: string
  order_type: string
  table: string | null
  station_id: string
  station_name: string
  target_minutes: number
  created_at: string
  elapsed_seconds: number
  is_late: boolean
  lines: TicketLine[]
}

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

interface StationPerformance {
  count: number
  average_seconds: number
  late: number
  late_percent: number
  target_minutes: number
}

const REFRESH_MS = 10_000

const STATUS_LABELS: Record<string, string> = {
  NEW: 'جديدة',
  ACCEPTED: 'مقبولة',
  PREPARING: 'تحت التحضير',
  READY: 'جاهزة',
  SERVED: 'تم التقديم',
  CANCELLED: 'ملغاة',
}

/** What one tap does next, mirroring the server's forward transitions. */
const NEXT: Record<string, { label: string; action: string }> = {
  NEW: { label: 'بدء التحضير', action: 'start' },
  ACCEPTED: { label: 'بدء التحضير', action: 'start' },
  PREPARING: { label: 'جاهزة', action: 'ready' },
  READY: { label: 'تم التقديم', action: 'served' },
}

const auth = useAuthStore()
const mayAdvance = computed(() => auth.can('kitchen.update_status'))

const tickets = ref<Ticket[]>([])
const stations = ref<Station[]>([])
const performance = ref<Record<string, StationPerformance>>({})
const stationFilter = ref('')
const loading = ref(true)
const stale = ref('')
const busy = ref('')
let timer: number | undefined

/** Oldest first, always — the same rule as the cook's board, for the same reason. */
const visible = computed(() =>
  tickets.value
    .filter((t) => !stationFilter.value || t.station_id === stationFilter.value)
    .slice()
    .sort((a, b) => a.created_at.localeCompare(b.created_at)),
)

const lateTickets = computed(() => visible.value.filter((t) => t.is_late))

/** The worst wait on the board. An average hides the one table that is furious. */
const worstMinutes = computed(() =>
  visible.value.length ? Math.max(...visible.value.map((t) => Math.floor(t.elapsed_seconds / 60))) : 0,
)

function minutes(ticket: Ticket): number {
  return Math.floor(ticket.elapsed_seconds / 60)
}

function tone(ticket: Ticket): 'danger' | 'warning' | 'success' | 'neutral' {
  if (ticket.is_late) return 'danger'
  if (ticket.status === 'READY') return 'success'
  if (ticket.status === 'PREPARING') return 'warning'
  return 'neutral'
}

function stateLabel(ticket: Ticket): string {
  // Words beside the colour, always — the same rule the Desktop board follows.
  const base = STATUS_LABELS[ticket.status] ?? ticket.status
  return ticket.is_late ? `متأخر · ${base}` : base
}

async function loadTickets() {
  try {
    tickets.value = await api.get<Ticket[]>('/kitchen/tickets/')
    stale.value = ''
  } catch {
    // The board keeps what it has and says so. Blanking it during a blip would
    // read as "the kitchen is empty", which is the opposite of the truth.
    stale.value = 'تعذّر التحديث — المعروض قد يكون قديماً.'
  }
}

async function advance(ticket: Ticket) {
  const next = NEXT[ticket.status]
  if (!next || !mayAdvance.value) return

  busy.value = ticket.id
  try {
    await api.post(`/kitchen/tickets/${ticket.id}/${next.action}/`)
    await loadTickets()
  } catch (e) {
    stale.value = e instanceof ApiError ? e.message : 'تعذّر تحديث التذكرة.'
  } finally {
    busy.value = ''
  }
}

onMounted(async () => {
  try {
    ;[stations.value] = await Promise.all([api.get<Station[]>('/kitchen/stations/'), loadTickets()])
    try {
      performance.value = await api.get<Record<string, StationPerformance>>('/kitchen/performance/')
    } catch {
      performance.value = {}
    }
    timer = window.setInterval(loadTickets, REFRESH_MS)
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
        <h1 class="text-2xl font-bold text-ink">المطبخ الآن</h1>
        <p class="mt-1 text-sm text-ink-muted">
          يتحدّث كل {{ REFRESH_MS / 1000 }} ثانية. شاشة الطهاة على جهاز الكاشير — هذه للمتابعة.
        </p>
      </div>

      <div v-if="stations.length > 1" class="flex flex-wrap gap-2">
        <button
          class="rounded-lg px-3 py-2 text-sm font-medium ring-1 ring-inset transition"
          :class="
            stationFilter === ''
              ? 'bg-brand-50 text-brand-800 ring-brand-200'
              : 'bg-surface text-ink ring hover:bg-surface-muted'
          "
          @click="stationFilter = ''"
        >
          الكل
        </button>
        <button
          v-for="station in stations.filter((s) => s.is_active)"
          :key="station.id"
          class="rounded-lg px-3 py-2 text-sm font-medium ring-1 ring-inset transition"
          :class="
            stationFilter === station.id
              ? 'bg-brand-50 text-brand-800 ring-brand-200'
              : 'bg-surface text-ink ring hover:bg-surface-muted'
          "
          @click="stationFilter = station.id"
        >
          {{ station.name_ar }}
        </button>
      </div>
    </div>

    <UiAlert v-if="stale" tone="warning">{{ stale }}</UiAlert>

    <UiSkeleton v-if="loading" :rows="6" />

    <template v-else>
      <div class="grid gap-4 sm:grid-cols-3">
        <UiCard>
          <p class="text-sm text-ink-muted">تذاكر مفتوحة</p>
          <p class="mt-1 text-2xl font-bold text-ink">{{ visible.length }}</p>
        </UiCard>
        <UiCard>
          <p class="text-sm text-ink-muted">متأخرة</p>
          <p
            class="mt-1 text-2xl font-bold"
            :class="lateTickets.length ? 'text-danger' : 'text-ink'"
          >
            {{ lateTickets.length }}
          </p>
          <p v-if="lateTickets.length" class="mt-2 text-xs font-medium text-danger">
            تجاوزت الوقت المستهدف للمحطة
          </p>
        </UiCard>
        <UiCard>
          <p class="text-sm text-ink-muted">أطول انتظار</p>
          <p class="mt-1 text-2xl font-bold tabular-nums text-ink" dir="ltr">
            {{ worstMinutes }} د
          </p>
          <p class="mt-2 text-xs text-ink-faint">المتوسط يخفي الطاولة الغاضبة.</p>
        </UiCard>
      </div>

      <UiEmpty
        v-if="!visible.length"
        icon="check"
        title="لا توجد تذاكر"
        description="المطبخ فارغ الآن."
      />

      <div v-else class="grid gap-3">
        <UiCard
          v-for="ticket in visible"
          :key="ticket.id"
          class="border-s-4"
          :class="{
            'border-s-red-500': tone(ticket) === 'danger',
            'border-s-amber-400': tone(ticket) === 'warning',
            'border-s-emerald-500': tone(ticket) === 'success',
            'border-s-slate-300': tone(ticket) === 'neutral',
          }"
        >
          <div class="flex flex-wrap items-start justify-between gap-4">
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <span class="font-mono text-lg font-bold text-ink" dir="ltr">
                  #{{ ticket.ticket_number }}
                </span>
                <UiBadge :tone="tone(ticket)">{{ stateLabel(ticket) }}</UiBadge>
                <UiBadge tone="neutral">{{ ticket.station_name }}</UiBadge>
                <UiBadge v-if="ticket.table" tone="info">طاولة {{ ticket.table }}</UiBadge>
              </div>

              <p class="mt-1 text-sm text-ink-muted">
                طلب {{ ticket.order_number }} · أُرسل {{ time(ticket.created_at) }} · المستهدف
                {{ ticket.target_minutes }} د
              </p>

              <ul class="mt-2 space-y-1 text-sm text-ink">
                <li v-for="line in ticket.lines" :key="line.id">
                  <span class="font-semibold tabular-nums" dir="ltr">{{ line.quantity }}×</span>
                  {{ line.name }}
                  <span v-if="line.modifiers.length" class="text-ink-muted">
                    · {{ line.modifiers.join('، ') }}
                  </span>
                  <span v-if="line.note" class="font-medium text-warning">
                    · {{ line.note }}
                  </span>
                  <UiBadge v-if="line.ready_at" tone="success">جاهز</UiBadge>
                </li>
              </ul>
            </div>

            <div class="flex flex-col items-end gap-2">
              <p
                class="font-mono text-2xl font-bold tabular-nums"
                :class="ticket.is_late ? 'text-danger' : 'text-ink'"
                dir="ltr"
              >
                {{ minutes(ticket) }} د
              </p>
              <UiButton
                v-if="mayAdvance && NEXT[ticket.status]"
                size="sm"
                variant="secondary"
                :loading="busy === ticket.id"
                @click="advance(ticket)"
              >
                {{ NEXT[ticket.status].label }}
              </UiButton>
            </div>
          </div>
        </UiCard>
      </div>

      <UiCard v-if="Object.keys(performance).length">
        <h2 class="text-sm font-semibold text-ink">متوسط زمن التحضير حسب المحطة</h2>
        <p class="mt-1 text-xs text-ink-muted">
          قرار توظيف، وليس قرار اليوم — "بار القهوة بطيء بعد الثامنة" يُحل بشخص إضافي.
        </p>
        <div class="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <div
            v-for="(row, name) in performance"
            :key="name"
            class="rounded-lg bg-surface-muted px-4 py-3"
          >
            <p class="text-sm font-medium text-ink">{{ name }}</p>
            <p class="mt-1 font-mono text-xl font-bold tabular-nums text-ink" dir="ltr">
              {{ Math.round(row.average_seconds / 60) }} د
            </p>
            <p class="mt-1 text-xs text-ink-muted">
              المستهدف {{ row.target_minutes }} د · {{ row.count }} تذكرة ·
              <span :class="row.late_percent > 20 ? 'font-semibold text-danger' : ''">
                {{ row.late_percent }}% متأخرة
              </span>
            </p>
          </div>
        </div>
      </UiCard>
    </template>
  </div>
</template>
