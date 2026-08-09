<script setup lang="ts">
/**
 * Sync health — the screen that exists so failure is never silent.
 *
 * A sync engine that fails quietly is worse than none: staff keep working,
 * confident everything is recorded, and find out a week later that a terminal
 * has been queueing since Tuesday. So this page leads with the bad news — stale
 * terminals and open conflicts first, healthy ones after.
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'

import { ApiError, api } from '@/api/client'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiBadge from '@/components/ui/UiBadge.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import UiTable from '@/components/ui/UiTable.vue'
import { useAuthStore } from '@/stores/auth'
import { dateTime, relativeMinutes } from '@/lib/format'

interface DeviceRow {
  device_id: string
  device_name: string
  status: string
  app_version: string
  last_seen_at: string | null
  last_push_at: string | null
  pending: number
  rejected: number
  open_conflicts: number
  cursors: Record<string, number>
}

interface BranchStatus {
  branch_id: string
  devices: DeviceRow[]
  stale_devices: number
  offline_alert_minutes: number
  open_conflicts: number
  heads: Record<string, number>
}

interface Conflict {
  id: string
  op_uuid: string
  entity_type: string
  device_name: string | null
  code: string
  message_ar: string
  server_state: Record<string, unknown>
  created_at: string
  resolved_at: string | null
}

const REFRESH_MS = 30_000

const CONFLICT_HELP: Record<string, string> = {
  SEQUENCE_GAP: 'ينقص حدث في الترتيب — الجهاز يعيد الإرسال تلقائياً، وغالباً لا يحتاج تدخلاً.',
  ORDER_ALREADY_CLOSED:
    'أصناف أُضيفت لطلب دفعه جهاز آخر. الأكل تم تحضيره — القرار: من يدفع؟',
  CLOCK_SKEW: 'ساعة الجهاز مختلفة عن الخادم. العملية طُبِّقت، لكن سجل التدقيق سيبدو مضلِّلاً.',
  KIDS_AREA_FULL: 'الطفل دخل بالفعل والصالة ممتلئة على الخادم. لن يخرج أحد — يحتاج قراراً.',
  SHIFT_ALREADY_OPEN: 'الجهاز فتح وردية ثانية. لا بد من تحديد أي درج هو المعتمد.',
}

const auth = useAuthStore()
const status = ref<BranchStatus | null>(null)
const conflicts = ref<Conflict[]>([])
const loading = ref(true)
const error = ref('')
const busy = ref('')
let timer: number | undefined

const canResolve = computed(() => auth.can('sync.resolve_conflicts'))

const deviceColumns = [
  { key: 'device', label: 'الجهاز' },
  { key: 'seen', label: 'آخر اتصال' },
  { key: 'pending', label: 'قيد الانتظار', align: 'end' as const },
  { key: 'rejected', label: 'مرفوضة', align: 'end' as const },
  { key: 'conflicts', label: 'تعارضات', align: 'end' as const },
]

function isStale(device: DeviceRow): boolean {
  if (!status.value) return false
  if (!device.last_seen_at) return true
  const minutes = (Date.now() - new Date(device.last_seen_at).getTime()) / 60_000
  return minutes > status.value.offline_alert_minutes
}

async function load() {
  try {
    ;[status.value, conflicts.value] = await Promise.all([
      api.get<BranchStatus>('/sync/status/'),
      api.get<Conflict[]>('/sync/conflicts/'),
    ])
    error.value = ''
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.message : 'تعذّر تحميل حالة المزامنة.'
  }
}

async function resolve(conflict: Conflict, resolution: string) {
  busy.value = conflict.id
  try {
    await api.post(`/sync/conflicts/${conflict.id}/resolve/`, { resolution })
    await load()
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.message : 'تعذّر حفظ القرار.'
  } finally {
    busy.value = ''
  }
}

onMounted(async () => {
  await load()
  loading.value = false
  timer = window.setInterval(load, REFRESH_MS)
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-bold text-slate-900">حالة المزامنة</h1>
      <p class="mt-1 text-sm text-slate-500">
        جهاز توقف عن الإرسال معناه مبيعات موجودة على قرص صلب فقط.
      </p>
    </div>

    <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>

    <UiSkeleton v-if="loading" :rows="8" />

    <template v-else-if="status">
      <UiAlert v-if="status.stale_devices" tone="warning">
        {{ status.stale_devices }} جهاز لم يتصل منذ أكثر من
        {{ status.offline_alert_minutes }} دقيقة.
      </UiAlert>

      <div class="grid gap-4 sm:grid-cols-3">
        <UiCard>
          <p class="text-sm text-slate-500">الأجهزة</p>
          <p class="mt-1 text-2xl font-bold text-slate-900">{{ status.devices.length }}</p>
        </UiCard>
        <UiCard>
          <p class="text-sm text-slate-500">غير متصلة</p>
          <p
            class="mt-1 text-2xl font-bold"
            :class="status.stale_devices ? 'text-amber-700' : 'text-slate-900'"
          >
            {{ status.stale_devices }}
          </p>
        </UiCard>
        <UiCard>
          <p class="text-sm text-slate-500">تعارضات مفتوحة</p>
          <p
            class="mt-1 text-2xl font-bold"
            :class="status.open_conflicts ? 'text-red-700' : 'text-slate-900'"
          >
            {{ status.open_conflicts }}
          </p>
        </UiCard>
      </div>

      <UiCard>
        <h2 class="mb-3 text-sm font-semibold text-slate-700">التعارضات</h2>
        <UiEmpty
          v-if="!conflicts.length"
          icon="check"
          title="لا توجد تعارضات"
          description="كل ما أرسلته الأجهزة تم تطبيقه."
        />
        <div v-else class="space-y-3">
          <div
            v-for="conflict in conflicts"
            :key="conflict.id"
            class="rounded-lg border border-red-200 bg-red-50/50 p-4"
          >
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <UiBadge tone="danger">{{ conflict.code }}</UiBadge>
                  <span class="text-sm text-slate-500">{{ conflict.entity_type }}</span>
                  <span v-if="conflict.device_name" class="text-sm text-slate-500">
                    · {{ conflict.device_name }}
                  </span>
                </div>
                <p class="mt-2 font-medium text-slate-900">{{ conflict.message_ar }}</p>
                <p v-if="CONFLICT_HELP[conflict.code]" class="mt-1 text-sm text-slate-600">
                  {{ CONFLICT_HELP[conflict.code] }}
                </p>
                <p class="mt-1 text-xs text-slate-400">{{ dateTime(conflict.created_at) }}</p>

                <pre
                  v-if="Object.keys(conflict.server_state).length"
                  class="mt-2 overflow-x-auto rounded bg-white p-2 text-xs text-slate-600"
                  dir="ltr"
                >{{ JSON.stringify(conflict.server_state, null, 2) }}</pre>
              </div>

              <div v-if="canResolve" class="flex shrink-0 flex-wrap gap-2">
                <UiButton
                  variant="secondary"
                  :disabled="busy === conflict.id"
                  @click="resolve(conflict, 'RETRIED')"
                >
                  إعادة المحاولة
                </UiButton>
                <UiButton
                  variant="secondary"
                  :disabled="busy === conflict.id"
                  @click="resolve(conflict, 'ACKNOWLEDGED')"
                >
                  تمت المراجعة
                </UiButton>
                <UiButton
                  variant="danger"
                  :disabled="busy === conflict.id"
                  @click="resolve(conflict, 'DISCARDED')"
                >
                  تجاهل
                </UiButton>
              </div>
            </div>
          </div>
        </div>
      </UiCard>

      <UiCard>
        <h2 class="mb-3 text-sm font-semibold text-slate-700">الأجهزة</h2>
        <UiEmpty
          v-if="!status.devices.length"
          icon="monitor"
          title="لا توجد أجهزة مفعّلة"
          description="فعّل جهازاً بمفتاح ترخيص ليظهر هنا."
        />
        <UiTable v-else :columns="deviceColumns">
          <tr v-for="device in status.devices" :key="device.device_id" class="hover:bg-slate-50">
            <td class="px-4 py-3">
              <div class="flex items-center gap-2">
                <span class="font-medium text-slate-900">{{ device.device_name }}</span>
                <UiBadge :tone="isStale(device) ? 'danger' : 'success'">
                  {{ isStale(device) ? 'غير متصل' : 'متصل' }}
                </UiBadge>
              </div>
              <p class="text-xs text-slate-400">
                {{ device.app_version || 'إصدار غير معروف' }}
              </p>
            </td>
            <td class="px-4 py-3 text-sm text-slate-500">
              {{ relativeMinutes(device.last_seen_at) }}
            </td>
            <td class="px-4 py-3 text-end tabular-nums">
              <span :class="device.pending ? 'font-medium text-amber-700' : 'text-slate-500'">
                {{ device.pending }}
              </span>
            </td>
            <td class="px-4 py-3 text-end tabular-nums">
              <span :class="device.rejected ? 'font-medium text-red-700' : 'text-slate-500'">
                {{ device.rejected }}
              </span>
            </td>
            <td class="px-4 py-3 text-end tabular-nums">
              <span :class="device.open_conflicts ? 'font-medium text-red-700' : 'text-slate-500'">
                {{ device.open_conflicts }}
              </span>
            </td>
          </tr>
        </UiTable>
      </UiCard>
    </template>
  </div>
</template>
