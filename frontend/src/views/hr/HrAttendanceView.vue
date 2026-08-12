<script setup lang="ts">
/**
 * Today's attendance board.
 *
 * The screen a shift leader keeps open. It answers one question — who is here —
 * and it answers it in the order somebody standing at the counter actually
 * needs: the people still on shift, then the ones who have gone home, then the
 * rostered names nobody has seen.
 *
 * **Absence is only shown for somebody who was rostered.** A screen that listed
 * every employee not currently present would call the whole day-off list an
 * absence, and a board that cries wolf about six people is a board nobody reads.
 *
 * **The original punch stays visible beside a correction.** If a manager moved a
 * clock-in, the row says so and shows both times. A screen that displayed only
 * the corrected value would make the correction invisible, which is the one thing
 * an amendment must never be — the whole reason the server stores them side by
 * side rather than overwriting.
 */
import { computed, onMounted, ref } from 'vue'

import { ApiError, api } from '@/api/client'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiBadge from '@/components/ui/UiBadge.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiIcon from '@/components/ui/UiIcon.vue'
import UiInput from '@/components/ui/UiInput.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import { time } from '@/lib/format'
import { useAuthStore } from '@/stores/auth'

interface Attendance {
  id: string
  user: string
  user_name: string
  business_date: string
  pattern_name: string | null
  checked_in_at: string
  checked_out_at: string | null
  amended_in_at: string | null
  amended_out_at: string | null
  amendment_reason: string
  effective_in: string
  effective_out: string | null
  worked_minutes: number | null
  late_minutes: number
  is_open: boolean
  is_amended: boolean
  source: string
}

interface RosterEntry {
  id: string
  user: string
  user_name: string
  pattern_name: string
  starts_at: string
  ends_at: string
  business_date: string
}

const auth = useAuthStore()
const mayRecord = computed(() => auth.can('hr.record_attendance'))
const mayAmend = computed(() => auth.can('hr.amend_attendance'))

const attendance = ref<Attendance[]>([])
const roster = ref<RosterEntry[]>([])
const loading = ref(true)
const error = ref('')
const busy = ref('')

/** `YYYY-MM-DD` from the local clock, not `toISOString()`, which converts to UTC
 *  first — from midnight until 03:00 in Egypt that returns yesterday. */
function localToday(): string {
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

const day = ref(localToday())

/** Still on shift, and the reason the board exists. */
const present = computed(() => attendance.value.filter((a) => a.is_open))
const left = computed(() => attendance.value.filter((a) => !a.is_open))

/**
 * Rostered, and no punch at all.
 *
 * Computed against the roster rather than against the staff list, so somebody on
 * their day off is simply not on this screen.
 */
const missing = computed(() => {
  const punched = new Set(attendance.value.map((a) => a.user))
  return roster.value.filter((entry) => !punched.has(entry.user))
})

const lateCount = computed(() => attendance.value.filter((a) => a.late_minutes > 0).length)

async function load() {
  loading.value = true
  try {
    const [punches, rota] = await Promise.all([
      api.get<Attendance[]>(`/hr/attendance/?date_from=${day.value}&date_to=${day.value}`),
      api.get<RosterEntry[]>(`/hr/roster/?date_from=${day.value}&date_to=${day.value}`),
    ])
    attendance.value = punches
    roster.value = rota
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تحميل الحضور.'
  } finally {
    loading.value = false
  }
}

async function punch(userId: string, kind: 'check-in' | 'check-out') {
  busy.value = userId
  try {
    await api.post(`/hr/punch/${kind}/`, { user: userId })
    error.value = ''
    await load()
  } catch (e) {
    // Shown as the service wrote it. These refusals are already in Arabic and
    // already name the remedy; rewording them here would give one rule two
    // vocabularies.
    error.value = e instanceof ApiError ? e.message : 'تعذّر تسجيل الحركة.'
  } finally {
    busy.value = ''
  }
}

// ── amending ────────────────────────────────────────────────────────────────

const amending = ref<Attendance | null>(null)
const amendReason = ref('')
const amendIn = ref('')
const amendOut = ref('')
const amendError = ref('')

/** `datetime-local` wants `YYYY-MM-DDTHH:mm` in local time. */
function forInput(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function openAmend(row: Attendance) {
  amending.value = row
  amendReason.value = ''
  amendIn.value = forInput(row.effective_in)
  amendOut.value = forInput(row.effective_out)
  amendError.value = ''
}

async function saveAmend() {
  if (!amending.value) return
  const row = amending.value
  const body: Record<string, string> = { reason: amendReason.value }

  // Only send what actually moved. Sending an unchanged value would still be an
  // amendment on the server, putting a WARNING row in the audit trail for an
  // edit nobody made.
  if (amendIn.value && amendIn.value !== forInput(row.effective_in)) {
    body.checked_in_at = new Date(amendIn.value).toISOString()
  }
  if (amendOut.value && amendOut.value !== forInput(row.effective_out)) {
    body.checked_out_at = new Date(amendOut.value).toISOString()
  }

  try {
    await api.post(`/hr/attendance/${row.id}/amend/`, body)
    amending.value = null
    await load()
  } catch (e) {
    amendError.value = e instanceof ApiError ? e.message : 'تعذّر حفظ التعديل.'
  }
}

function hours(minutes: number | null): string {
  if (minutes === null) return '—'
  return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, '0')}`
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 class="text-2xl font-bold text-ink">الحضور والانصراف</h1>
        <p class="mt-1 text-sm text-ink-muted">
          الساعات محسوبة من البصمات في كل مرة — لا يوجد رقم مخزَّن يمكن أن يخالفها.
        </p>
      </div>
      <label class="text-sm">
        <span class="block text-ink-muted">اليوم</span>
        <input
          v-model="day"
          type="date"
          class="mt-1 block rounded-lg border border-line-strong px-3 py-2 text-sm"
          @change="load"
        />
      </label>
    </div>

    <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>

    <UiSkeleton v-if="loading" :rows="6" />

    <template v-else>
      <div class="grid gap-3 sm:grid-cols-3">
        <UiCard>
          <p class="text-sm text-ink-muted">في الوردية الآن</p>
          <p class="mt-1 text-2xl font-bold text-ink tabular-nums">{{ present.length }}</p>
        </UiCard>
        <UiCard>
          <p class="text-sm text-ink-muted">متأخر</p>
          <p class="mt-1 text-2xl font-bold tabular-nums" :class="lateCount ? 'text-warning' : 'text-ink'">
            {{ lateCount }}
          </p>
        </UiCard>
        <UiCard>
          <p class="text-sm text-ink-muted">مجدول ولم يحضر</p>
          <p class="mt-1 text-2xl font-bold tabular-nums" :class="missing.length ? 'text-danger' : 'text-ink'">
            {{ missing.length }}
          </p>
        </UiCard>
      </div>

      <UiEmpty
        v-if="!attendance.length && !roster.length"
        icon="users"
        title="لا يوجد حضور ولا جدول لهذا اليوم"
        description="أضف ورديات من الجدول، أو سجّل حضوراً يدوياً."
      />

      <!-- Still here. First, because it is the question the board is open for. -->
      <UiCard v-if="present.length" title="في الوردية الآن">
        <ul class="divide-y divide-line">
          <li v-for="row in present" :key="row.id" class="flex flex-wrap items-center justify-between gap-3 py-3">
            <div class="min-w-0">
              <p class="font-medium text-ink">
                {{ row.user_name }}
                <UiBadge v-if="row.late_minutes > 0" tone="warning">
                  متأخر {{ row.late_minutes }} د
                </UiBadge>
                <UiBadge v-if="row.is_amended" tone="info">معدَّل</UiBadge>
              </p>
              <p class="text-sm text-ink-muted">
                دخول {{ time(row.effective_in) }}
                <!-- Both times, always. The correction has to stay visible. -->
                <span v-if="row.amended_in_at" class="text-ink-faint">
                  (الأصلي {{ time(row.checked_in_at) }} — {{ row.amendment_reason }})
                </span>
                <span v-if="row.pattern_name"> · {{ row.pattern_name }}</span>
              </p>
            </div>
            <div class="flex items-center gap-2">
              <UiButton
                v-if="mayRecord"
                size="sm"
                variant="secondary"
                :disabled="busy === row.user"
                @click="punch(row.user, 'check-out')"
              >
                تسجيل انصراف
              </UiButton>
              <UiButton v-if="mayAmend" size="sm" variant="ghost" @click="openAmend(row)">تعديل</UiButton>
            </div>
          </li>
        </ul>
      </UiCard>

      <UiCard v-if="left.length" title="انصرفوا">
        <ul class="divide-y divide-line">
          <li v-for="row in left" :key="row.id" class="flex flex-wrap items-center justify-between gap-3 py-3">
            <div class="min-w-0">
              <p class="font-medium text-ink">
                {{ row.user_name }}
                <UiBadge v-if="row.late_minutes > 0" tone="warning">متأخر {{ row.late_minutes }} د</UiBadge>
                <UiBadge v-if="row.is_amended" tone="info">معدَّل</UiBadge>
              </p>
              <p class="text-sm text-ink-muted">
                {{ time(row.effective_in) }} — {{ time(row.effective_out) }}
                · <span class="tabular-nums">{{ hours(row.worked_minutes) }}</span> ساعة
                <span v-if="row.amendment_reason" class="text-ink-faint">
                  ({{ row.amendment_reason }})
                </span>
              </p>
            </div>
            <UiButton v-if="mayAmend" size="sm" variant="ghost" @click="openAmend(row)">تعديل</UiButton>
          </li>
        </ul>
      </UiCard>

      <UiCard v-if="missing.length" title="مجدول ولم يحضر">
        <ul class="divide-y divide-line">
          <li v-for="entry in missing" :key="entry.id" class="flex flex-wrap items-center justify-between gap-3 py-3">
            <div>
              <p class="font-medium text-ink">{{ entry.user_name }}</p>
              <p class="text-sm text-ink-muted">
                {{ entry.pattern_name }} · {{ entry.starts_at?.slice(0, 5) }}
              </p>
            </div>
            <UiButton
              v-if="mayRecord"
              size="sm"
              :disabled="busy === entry.user"
              @click="punch(entry.user, 'check-in')"
            >
              تسجيل حضور
            </UiButton>
          </li>
        </ul>
      </UiCard>
    </template>

    <!-- The amendment dialog. A reason is required by the server and by this
         form: an unexplained correction is indistinguishable from a mistake. -->
    <div v-if="amending" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <UiCard class="w-full max-w-lg">
        <div class="flex items-start justify-between gap-4">
          <div>
            <h2 class="text-lg font-bold text-ink">تعديل سجل {{ amending.user_name }}</h2>
            <p class="mt-1 text-sm text-ink-muted">
              البصمة الأصلية تبقى محفوظة. التعديل يُسجَّل باسمك وبالسبب.
            </p>
          </div>
          <button type="button" class="text-ink-muted" @click="amending = null">
            <UiIcon name="close" size="1rem" />
          </button>
        </div>

        <UiAlert v-if="amendError" tone="error" class="mt-3">{{ amendError }}</UiAlert>

        <form class="mt-4 space-y-3" @submit.prevent="saveAmend">
          <label class="block text-sm">
            <span class="text-ink-muted">وقت الحضور</span>
            <input
              v-model="amendIn"
              type="datetime-local"
              class="mt-1 block w-full rounded-lg border border-line-strong px-3 py-2 text-sm"
            />
          </label>
          <label class="block text-sm">
            <span class="text-ink-muted">وقت الانصراف</span>
            <input
              v-model="amendOut"
              type="datetime-local"
              class="mt-1 block w-full rounded-lg border border-line-strong px-3 py-2 text-sm"
            />
          </label>
          <UiInput v-model="amendReason" label="السبب" required hint="يظهر في سجل التدقيق." />

          <div class="flex justify-end gap-2 pt-2">
            <UiButton variant="ghost" type="button" @click="amending = null">إلغاء</UiButton>
            <UiButton type="submit" :disabled="!amendReason.trim()">حفظ التعديل</UiButton>
          </div>
        </form>
      </UiCard>
    </div>
  </div>
</template>
