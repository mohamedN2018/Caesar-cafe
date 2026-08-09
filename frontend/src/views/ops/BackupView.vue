<script setup lang="ts">
/**
 * Backups.
 *
 * The page leads with "how long since the last one succeeded", because that is
 * the number that matters. A backup system reporting "last run: COMPLETE" while
 * the last run was in April is the failure this screen exists to make impossible
 * to miss.
 *
 * There is no restore button and no download link. A route that replaces the
 * database is one somebody eventually clicks by mistake, and the file holds every
 * order, phone number and staff record — it belongs on the host and off-site,
 * not behind a session cookie. Restore is a documented command (docs/13).
 */
import { computed, onMounted, ref } from 'vue'

import { ApiError, api } from '@/api/client'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiBadge from '@/components/ui/UiBadge.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import UiTable from '@/components/ui/UiTable.vue'
import { dateTime } from '@/lib/format'

interface Backup {
  id: number
  filename: string
  size_mb: string
  encrypted: boolean
  status: 'RUNNING' | 'COMPLETE' | 'FAILED'
  error: string
  started_at: string
  duration_seconds: number
  triggered_by_name: string | null
}

interface State {
  configured: boolean
  directory: string
  total: number
  failed: number
  last_success: string | null
  last_filename: string | null
  last_size_mb: string | null
  hours_since_last: string | null
  backups: Backup[]
}

/** Past this, the nightly job has missed at least one run. */
const STALE_HOURS = 30

const state = ref<State | null>(null)
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const notice = ref('')

const columns = [
  { key: 'started', label: 'وقت البدء' },
  { key: 'file', label: 'الملف' },
  { key: 'size', label: 'الحجم', align: 'end' as const },
  { key: 'duration', label: 'المدة', align: 'end' as const },
  { key: 'by', label: 'بواسطة' },
]

const hours = computed(() =>
  state.value?.hours_since_last === null || state.value?.hours_since_last === undefined
    ? null
    : Number(state.value.hours_since_last),
)
const isStale = computed(() => hours.value === null || hours.value > STALE_HOURS)

async function load() {
  try {
    state.value = await api.get<State>('/ops/backups/')
    error.value = ''
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.message : 'تعذّر تحميل حالة النسخ الاحتياطي.'
  }
}

async function runNow() {
  busy.value = true
  notice.value = ''
  try {
    const record = await api.post<Backup>('/ops/backups/')
    notice.value = `تمت النسخة: ${record.filename} (${record.size_mb} ميجابايت)`
    await load()
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.message : 'فشلت النسخة الاحتياطية.'
  } finally {
    busy.value = false
  }
}

async function verify(backup: Backup) {
  busy.value = true
  try {
    const result = await api.post<{ verified: boolean; note_ar: string }>(
      `/ops/backups/${backup.id}/verify/`,
    )
    notice.value = `${backup.filename} — ${result.note_ar}`
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  await load()
  loading.value = false
})
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 class="text-2xl font-bold text-slate-900">النسخ الاحتياطي</h1>
        <p class="mt-1 text-sm text-slate-500">
          نسخة كاملة كل يوم الساعة ٣ فجراً — قبل بداية يوم العمل، فيوم الأمس مكتمل.
        </p>
      </div>
      <UiButton :loading="busy" @click="runNow">خُذ نسخة الآن</UiButton>
    </div>

    <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>
    <UiAlert v-if="notice" tone="success">{{ notice }}</UiAlert>

    <UiSkeleton v-if="loading" :rows="6" />

    <template v-else-if="state">
      <UiAlert v-if="!state.configured" tone="error">
        <strong>التشفير غير مضبوط.</strong>
        النسخ تُكتب بدون تشفير — وهي تحتوي كل طلب ورقم هاتف وبيانات كل موظف.
        اضبط <code dir="ltr">BACKUP_ENCRYPTION_KEY</code> فوراً؛ الخادم في وضع
        الإنتاج يرفض العمل بدونها.
      </UiAlert>

      <UiAlert v-if="isStale" tone="warning">
        <template v-if="hours === null">
          لا توجد أي نسخة احتياطية ناجحة. فقدان الخادم الآن يعني فقدان كل شيء.
        </template>
        <template v-else>
          آخر نسخة ناجحة منذ {{ hours }} ساعة — المهمة الليلية فوّتت تشغيلاً على الأقل.
        </template>
      </UiAlert>

      <div class="grid gap-4 sm:grid-cols-3">
        <UiCard>
          <p class="text-sm text-slate-500">آخر نسخة ناجحة</p>
          <p
            class="mt-1 text-2xl font-bold"
            :class="isStale ? 'text-red-700' : 'text-slate-900'"
          >
            {{ hours === null ? 'لا يوجد' : `منذ ${hours} ساعة` }}
          </p>
          <p v-if="state.last_size_mb" class="mt-1 text-sm text-slate-500">
            {{ state.last_size_mb }} ميجابايت
          </p>
        </UiCard>
        <UiCard>
          <p class="text-sm text-slate-500">إجمالي النسخ المحفوظة</p>
          <p class="mt-1 text-2xl font-bold text-slate-900">{{ state.total }}</p>
          <p class="mt-1 text-xs text-slate-400">٣٠ يومية + أول نسخة من كل شهر (١٢ شهراً)</p>
        </UiCard>
        <UiCard>
          <p class="text-sm text-slate-500">محاولات فاشلة</p>
          <p
            class="mt-1 text-2xl font-bold"
            :class="state.failed ? 'text-red-700' : 'text-slate-900'"
          >
            {{ state.failed }}
          </p>
        </UiCard>
      </div>

      <UiAlert tone="info">
        الاستعادة ليست زراً في هذه الصفحة بقصد — إنها أمر موثّق في
        <code dir="ltr">docs/13</code>، لأن زراً يستبدل قاعدة البيانات هو زر سيُضغط
        بالخطأ يوماً ما. كذلك لا يوجد رابط تنزيل: الملف يحتوي كل شيء ومكانه الخادم
        والتخزين الخارجي، لا متصفح.
      </UiAlert>

      <UiCard>
        <UiEmpty
          v-if="!state.backups.length"
          icon="save"
          title="لا توجد نسخ بعد"
          description="ستظهر أول نسخة بعد تشغيل المهمة الليلية، أو اضغط «خُذ نسخة الآن»."
        />
        <UiTable v-else :columns="columns">
          <tr v-for="backup in state.backups" :key="backup.id" class="hover:bg-slate-50">
            <td class="px-4 py-3 text-sm text-slate-500">{{ dateTime(backup.started_at) }}</td>
            <td class="px-4 py-3">
              <div class="flex flex-wrap items-center gap-2">
                <span class="font-mono text-xs text-slate-700" dir="ltr">
                  {{ backup.filename }}
                </span>
                <UiBadge :tone="backup.status === 'COMPLETE' ? 'success' : 'danger'">
                  {{ backup.status === 'COMPLETE' ? 'مكتملة' : 'فاشلة' }}
                </UiBadge>
                <UiBadge :tone="backup.encrypted ? 'info' : 'warning'">
                  {{ backup.encrypted ? 'مشفّرة' : 'غير مشفّرة' }}
                </UiBadge>
              </div>
              <p v-if="backup.error" class="mt-1 text-xs text-red-700">{{ backup.error }}</p>
            </td>
            <td class="px-4 py-3 text-end tabular-nums">{{ backup.size_mb }}</td>
            <td class="px-4 py-3 text-end tabular-nums text-slate-500">
              {{ backup.duration_seconds }}ث
            </td>
            <td class="px-4 py-3 text-sm text-slate-500">
              {{ backup.triggered_by_name ?? 'المهمة الليلية' }}
              <button
                v-if="backup.status === 'COMPLETE'"
                class="ms-2 text-brand-700 hover:underline"
                :disabled="busy"
                @click="verify(backup)"
              >
                تحقّق
              </button>
            </td>
          </tr>
        </UiTable>
      </UiCard>
    </template>
  </div>
</template>
