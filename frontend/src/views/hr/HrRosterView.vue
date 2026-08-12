<script setup lang="ts">
/**
 * The rota — a week at a time, and the patterns it is built from.
 *
 * A week rather than a month, because that is the unit a cafe actually plans in
 * and a month-wide grid on a phone is unreadable at any font size. Seven columns
 * fit; thirty-one do not.
 *
 * **Patterns are edited on this screen, not on a separate one.** A rota is
 * assembled by choosing shapes, and the moment somebody needs a shape that does
 * not exist yet, sending them to another page loses the week they were halfway
 * through building.
 *
 * A pattern is deactivated, never deleted: past rota slots point at it, and a
 * timesheet that cannot name which shift somebody was on is a timesheet that
 * cannot explain why they were late.
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
import { useAuthStore } from '@/stores/auth'

interface Pattern {
  id: string
  name_ar: string
  starts_at: string
  ends_at: string
  grace_minutes: number | null
  crosses_midnight: boolean
  scheduled_minutes: number
  is_active: boolean
}

interface Slot {
  id: string
  user: string
  user_name: string
  pattern: string
  pattern_name: string
  business_date: string
  note: string
}

interface Person {
  id: string
  full_name_ar: string
  is_active: boolean
}

const auth = useAuthStore()
const mayEdit = computed(() => auth.can('hr.manage_roster'))

const patterns = ref<Pattern[]>([])
const slots = ref<Slot[]>([])
const people = ref<Person[]>([])
const loading = ref(true)
const error = ref('')

function pad(n: number): string {
  return String(n).padStart(2, '0')
}

function isoDate(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/**
 * The Saturday that starts this week.
 *
 * Saturday, not Monday: the working week in Egypt runs Saturday to Friday, and a
 * rota that split the weekend across two pages would be wrong for every cafe
 * this system is for.
 */
function weekStart(from: Date): Date {
  const d = new Date(from)
  d.setDate(d.getDate() - ((d.getDay() + 1) % 7))
  d.setHours(0, 0, 0, 0)
  return d
}

const anchor = ref(weekStart(new Date()))

const days = computed(() => {
  const out: { iso: string; label: string; isToday: boolean }[] = []
  const today = isoDate(new Date())
  for (let i = 0; i < 7; i++) {
    const d = new Date(anchor.value)
    d.setDate(d.getDate() + i)
    out.push({
      iso: isoDate(d),
      label: d.toLocaleDateString('ar-EG', { weekday: 'short', day: 'numeric', month: 'short' }),
      isToday: isoDate(d) === today,
    })
  }
  return out
})

const activePatterns = computed(() => patterns.value.filter((p) => p.is_active))

function slotsFor(userId: string, iso: string): Slot[] {
  return slots.value.filter((s) => s.user === userId && s.business_date === iso)
}

async function load() {
  loading.value = true
  try {
    const from = days.value[0].iso
    const to = days.value[6].iso
    const [pats, rota, staff] = await Promise.all([
      api.get<Pattern[]>('/hr/patterns/'),
      api.get<Slot[]>(`/hr/roster/?date_from=${from}&date_to=${to}`),
      api.get<Person[]>('/staff/'),
    ])
    patterns.value = pats
    slots.value = rota
    people.value = staff.filter((p) => p.is_active)
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تحميل الجدول.'
  } finally {
    loading.value = false
  }
}

function shiftWeek(deltaDays: number) {
  const d = new Date(anchor.value)
  d.setDate(d.getDate() + deltaDays)
  anchor.value = d
  load()
}

// ── assigning ───────────────────────────────────────────────────────────────

const assigning = ref<{ user: Person; iso: string } | null>(null)

async function assign(patternId: string) {
  if (!assigning.value) return
  try {
    await api.post('/hr/roster/', {
      user: assigning.value.user.id,
      pattern: patternId,
      business_date: assigning.value.iso,
    })
    assigning.value = null
    await load()
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر إضافة الوردية.'
    assigning.value = null
  }
}

async function removeSlot(slot: Slot) {
  try {
    await api.delete(`/hr/roster/${slot.id}/`)
    await load()
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر حذف الوردية.'
  }
}

// ── patterns ────────────────────────────────────────────────────────────────

const EMPTY = { name_ar: '', starts_at: '08:00', ends_at: '16:00', grace_minutes: null as number | null }
const draft = ref({ ...EMPTY })
const savingPattern = ref(false)

async function savePattern() {
  savingPattern.value = true
  try {
    await api.post('/hr/patterns/', draft.value)
    draft.value = { ...EMPTY }
    await load()
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر حفظ النمط.'
  } finally {
    savingPattern.value = false
  }
}

async function togglePattern(pattern: Pattern) {
  try {
    await api.patch(`/hr/patterns/${pattern.id}/`, { is_active: !pattern.is_active })
    await load()
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تغيير حالة النمط.'
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 class="text-2xl font-bold text-ink">جدول الورديات</h1>
        <p class="mt-1 text-sm text-ink-muted">
          الأسبوع من السبت إلى الجمعة. الوردية هنا خطة عمل — وليست درج نقدية.
        </p>
      </div>
      <div class="flex items-center gap-2">
        <UiButton size="sm" variant="secondary" @click="shiftWeek(-7)">الأسبوع السابق</UiButton>
        <UiButton size="sm" variant="secondary" @click="shiftWeek(7)">الأسبوع التالي</UiButton>
      </div>
    </div>

    <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>

    <UiSkeleton v-if="loading" :rows="8" />

    <template v-else>
      <UiEmpty
        v-if="!activePatterns.length"
        icon="clock"
        title="لا توجد أنماط ورديات"
        description="أضف نمطاً واحداً على الأقل — الجدول يُبنى باختيار الأنماط، لا بكتابة الأوقات."
      />

      <UiCard v-else>
        <div class="overflow-x-auto">
          <table class="w-full min-w-[52rem] text-sm">
            <thead class="border-b border-line bg-surface-muted">
              <tr>
                <th class="px-3 py-2 text-start font-semibold text-ink-muted">الموظف</th>
                <th
                  v-for="d in days"
                  :key="d.iso"
                  class="px-3 py-2 text-center font-semibold"
                  :class="d.isToday ? 'text-brand-700' : 'text-ink-muted'"
                >
                  {{ d.label }}
                </th>
              </tr>
            </thead>
            <tbody class="divide-y divide-line">
              <tr v-for="person in people" :key="person.id">
                <td class="px-3 py-2 font-medium text-ink">{{ person.full_name_ar }}</td>
                <td v-for="d in days" :key="d.iso" class="px-2 py-2 text-center align-top">
                  <div v-for="slot in slotsFor(person.id, d.iso)" :key="slot.id" class="mb-1">
                    <UiBadge tone="info">{{ slot.pattern_name }}</UiBadge>
                    <!-- Drawn, not a `×`. The glyph is what this codebase went
                         through an emoji sweep to remove: it renders at a
                         different weight in every Arabic-capable face. -->
                    <button
                      v-if="mayEdit"
                      type="button"
                      class="ms-1 align-middle text-ink-faint hover:text-danger"
                      title="حذف الوردية"
                      @click="removeSlot(slot)"
                    >
                      <UiIcon name="close" size="0.7rem" />
                    </button>
                  </div>
                  <button
                    v-if="mayEdit && !slotsFor(person.id, d.iso).length"
                    type="button"
                    class="rounded-lg border border-dashed border-line-strong px-2 py-1 text-xs text-ink-faint hover:text-ink"
                    @click="assigning = { user: person, iso: d.iso }"
                  >
                    +
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </UiCard>

      <UiCard title="أنماط الورديات">
        <ul class="divide-y divide-line">
          <li
            v-for="pattern in patterns"
            :key="pattern.id"
            class="flex flex-wrap items-center justify-between gap-3 py-2"
            :class="pattern.is_active ? '' : 'opacity-60'"
          >
            <div>
              <p class="font-medium text-ink">
                {{ pattern.name_ar }}
                <UiBadge v-if="pattern.crosses_midnight" tone="neutral">يعبر منتصف الليل</UiBadge>
                <UiBadge v-if="!pattern.is_active" tone="warning">موقوف</UiBadge>
              </p>
              <p class="text-sm text-ink-muted">
                <span dir="ltr">{{ pattern.starts_at?.slice(0, 5) }}–{{ pattern.ends_at?.slice(0, 5) }}</span>
                · {{ Math.round(pattern.scheduled_minutes / 60) }} ساعة
                <span v-if="pattern.grace_minutes !== null">
                  · سماح {{ pattern.grace_minutes }} د
                </span>
              </p>
            </div>
            <UiButton v-if="mayEdit" size="sm" variant="ghost" @click="togglePattern(pattern)">
              {{ pattern.is_active ? 'إيقاف' : 'تفعيل' }}
            </UiButton>
          </li>
        </ul>

        <form v-if="mayEdit" class="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4" @submit.prevent="savePattern">
          <UiInput v-model="draft.name_ar" label="الاسم" required />
          <UiInput v-model="draft.starts_at" label="البداية" type="time" ltr required />
          <UiInput v-model="draft.ends_at" label="النهاية" type="time" ltr required />
          <UiInput
            v-model.number="draft.grace_minutes"
            label="سماح التأخير (د)"
            type="number"
            hint="اتركه فارغاً لاستخدام إعداد الفرع."
          />
          <div class="sm:col-span-2 lg:col-span-4">
            <UiButton type="submit" :disabled="savingPattern">إضافة نمط</UiButton>
          </div>
        </form>
      </UiCard>
    </template>

    <div v-if="assigning" class="fixed inset-0 z-50 flex items-center justify-center bg-scrim p-4">
      <UiCard class="w-full max-w-sm">
        <h2 class="text-lg font-bold text-ink">
          {{ assigning.user.full_name_ar }}
        </h2>
        <p class="mt-1 text-sm text-ink-muted">اختر النمط لهذا اليوم.</p>
        <div class="mt-4 grid gap-2">
          <UiButton
            v-for="pattern in activePatterns"
            :key="pattern.id"
            variant="secondary"
            block
            @click="assign(pattern.id)"
          >
            {{ pattern.name_ar }}
            <span dir="ltr" class="text-xs">
              ({{ pattern.starts_at?.slice(0, 5) }}–{{ pattern.ends_at?.slice(0, 5) }})
            </span>
          </UiButton>
        </div>
        <div class="mt-4 flex justify-end">
          <UiButton variant="ghost" @click="assigning = null">إلغاء</UiButton>
        </div>
      </UiCard>
    </div>
  </div>
</template>
