<script setup lang="ts">
/**
 * Licences.
 *
 * The plaintext key is shown EXACTLY once, at creation. It is not recoverable
 * afterwards — the server stores only an HMAC — which is why a stolen database
 * yields nothing usable, and why losing a key means regenerating rather than
 * looking it up.
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
import { useAuthStore } from '@/stores/auth'
import { dateTime } from '@/lib/format'

interface License {
  id: string
  masked_key: string
  customer_email: string
  customer_name: string
  license_type: string
  status: string
  starts_at: string
  expires_at: string | null
  max_devices: number
  active_device_count: number
  seats_available: number
  last_activation_at: string | null
}

type Tone = 'success' | 'danger' | 'info' | 'warning' | 'neutral'

const STATUSES: Record<string, { label: string; tone: Tone }> = {
  PENDING: { label: 'بانتظار التفعيل', tone: 'info' },
  ACTIVE: { label: 'نشط', tone: 'success' },
  SUSPENDED: { label: 'موقوف', tone: 'warning' },
  EXPIRED: { label: 'منتهي', tone: 'danger' },
  REVOKED: { label: 'ملغي', tone: 'danger' },
}

const auth = useAuthStore()
const licenses = ref<License[]>([])
const loading = ref(true)
const error = ref('')
const revealedKey = ref('')

const columns = [
  { key: 'key', label: 'المفتاح' },
  { key: 'customer', label: 'العميل' },
  { key: 'type', label: 'النوع' },
  { key: 'seats', label: 'الأجهزة', align: 'end' as const },
  { key: 'expires', label: 'ينتهي' },
  { key: 'status', label: 'الحالة' },
]

const canManage = computed(() => auth.can('licenses.manage'))

function toneFor(status: string): Tone {
  return STATUSES[status]?.tone ?? 'neutral'
}

function labelFor(status: string): string {
  return STATUSES[status]?.label ?? status
}

/** Seats used out of the licence's limit — the number an admin actually needs. */
function seats(license: License): string {
  return `${license.active_device_count} / ${license.max_devices}`
}

async function load() {
  loading.value = true
  try {
    licenses.value = await api.get<License[]>('/licensing/licenses/')
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : 'تعذر تحميل التراخيص'
  } finally {
    loading.value = false
  }
}

async function act(license: License, action: string) {
  error.value = ''
  try {
    const result = await api.post<{ license_key?: string }>(
      `/licensing/licenses/${license.id}/${action}/`,
      {},
    )
    if (result.license_key) revealedKey.value = result.license_key
    await load()
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : 'تعذر تنفيذ الإجراء'
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-bold text-slate-900">التراخيص</h1>
      <p class="mt-1 text-sm text-slate-500">
        المفتاح يظهر مرة واحدة عند الإنشاء فقط — الخادم يحفظ بصمته لا نصّه.
      </p>
    </div>

    <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>

    <UiAlert v-if="revealedKey" tone="warning">
      <p class="font-semibold">احفظ هذا المفتاح الآن — لن يظهر مرة أخرى.</p>
      <p class="mt-2 select-all font-mono text-lg tracking-wider" dir="ltr">{{ revealedKey }}</p>
      <button class="mt-2 text-sm underline" @click="revealedKey = ''">إخفاء</button>
    </UiAlert>

    <UiSkeleton v-if="loading" :rows="5" />

    <UiCard v-else>
      <UiEmpty
        v-if="!licenses.length"
        icon="key"
        title="لا توجد تراخيص"
        description="أنشئ ترخيصاً ثم استخدم مفتاحه لتفعيل أول جهاز."
      />
      <UiTable v-else :columns="columns">
        <tr v-for="license in licenses" :key="license.id" class="hover:bg-slate-50">
          <td class="px-4 py-3 font-mono text-sm" dir="ltr">{{ license.masked_key }}</td>
          <td class="px-4 py-3">
            <p class="text-slate-900">{{ license.customer_name || '—' }}</p>
            <p class="text-xs text-slate-500" dir="ltr">{{ license.customer_email }}</p>
          </td>
          <td class="px-4 py-3 text-slate-600">{{ license.license_type }}</td>
          <td class="px-4 py-3 text-end tabular-nums">
            <span :class="license.seats_available === 0 && 'font-semibold text-amber-700'">
              {{ seats(license) }}
            </span>
          </td>
          <td class="whitespace-nowrap px-4 py-3 text-sm text-slate-500">
            {{ license.expires_at ? dateTime(license.expires_at) : 'مدى الحياة' }}
          </td>
          <td class="px-4 py-3">
            <div class="flex items-center gap-2">
              <UiBadge :tone="toneFor(license.status)">{{ labelFor(license.status) }}</UiBadge>
              <template v-if="canManage">
                <UiButton
                  v-if="license.status === 'ACTIVE'"
                  variant="ghost"
                  size="sm"
                  @click="act(license, 'suspend')"
                >
                  إيقاف
                </UiButton>
                <UiButton
                  v-else-if="license.status === 'SUSPENDED'"
                  variant="ghost"
                  size="sm"
                  @click="act(license, 'resume')"
                >
                  استئناف
                </UiButton>
              </template>
            </div>
          </td>
        </tr>
      </UiTable>
    </UiCard>
  </div>
</template>
