<script setup lang="ts">
/**
 * Activated devices.
 *
 * `fingerprint_changed_count` is diagnostic, never authorization (C4): a device
 * whose fingerprint changes repeatedly has probably had its credential copied
 * across machines. Detection, not prevention.
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
import { relativeMinutes } from '@/lib/format'

interface Device {
  id: string
  device_name: string
  mode: string
  status: string
  platform: string
  app_version: string
  branch_name: string
  license_key: string
  first_activated_at: string
  last_seen_at: string | null
  fingerprint_changed_count: number
  minutes_since_seen: number | null
}

const auth = useAuthStore()
const devices = ref<Device[]>([])
const loading = ref(true)
const error = ref('')

const columns = [
  { key: 'name', label: 'الجهاز' },
  { key: 'mode', label: 'الوضع' },
  { key: 'version', label: 'الإصدار' },
  { key: 'seen', label: 'آخر اتصال' },
  { key: 'status', label: 'الحالة' },
  { key: 'actions', label: '' },
]

const canManage = computed(() => auth.can('devices.manage'))

/** Offline for over 30 minutes is worth surfacing — that is a terminal nobody noticed died. */
function isStale(device: Device): boolean {
  return device.minutes_since_seen === null || device.minutes_since_seen > 30
}

async function load() {
  loading.value = true
  try {
    devices.value = await api.get<Device[]>('/licensing/devices/')
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : 'تعذر تحميل الأجهزة'
  } finally {
    loading.value = false
  }
}

async function act(device: Device, action: string) {
  error.value = ''
  try {
    await api.post(`/licensing/devices/${device.id}/${action}/`, {})
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
      <h1 class="text-2xl font-bold text-ink">الأجهزة المفعّلة</h1>
      <p class="mt-1 text-sm text-ink-muted">
        إلغاء التفعيل يوقف الجهاز فوراً. «تحرير المقعد» يحذفه ليُفعَّل جهاز آخر مكانه.
      </p>
    </div>

    <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>

    <UiSkeleton v-if="loading" :rows="5" />

    <UiCard v-else>
      <UiEmpty
        v-if="!devices.length"
        icon="monitor"
        title="لا توجد أجهزة مفعّلة"
        description="ثبّت تطبيق الديسكتوب وفعّله بمفتاح الترخيص."
      />
      <UiTable v-else :columns="columns">
        <tr v-for="device in devices" :key="device.id" class="hover:bg-surface-muted">
          <td class="px-4 py-3">
            <p class="font-medium text-ink">{{ device.device_name }}</p>
            <p class="text-xs text-ink-muted">{{ device.platform || '—' }}</p>
            <p
              v-if="device.fingerprint_changed_count > 0"
              class="text-xs text-warning"
              title="بصمة الجهاز تغيّرت — قد تكون بيانات الاعتماد منسوخة"
            >
              ⚠ تغيّرت البصمة {{ device.fingerprint_changed_count }} مرة
            </p>
          </td>
          <td class="px-4 py-3 text-ink-muted">{{ device.mode }}</td>
          <td class="px-4 py-3 font-mono text-sm text-ink-muted" dir="ltr">
            {{ device.app_version || '—' }}
          </td>
          <td class="px-4 py-3 text-sm" :class="isStale(device) ? 'text-warning' : 'text-ink-muted'">
            {{ relativeMinutes(device.last_seen_at) }}
          </td>
          <td class="px-4 py-3">
            <UiBadge :tone="device.status === 'ACTIVE' ? 'success' : 'danger'">
              {{ device.status === 'ACTIVE' ? 'نشط' : device.status }}
            </UiBadge>
          </td>
          <td class="px-4 py-3">
            <div v-if="canManage" class="flex justify-end gap-2">
              <UiButton
                v-if="device.status === 'ACTIVE'"
                variant="ghost"
                size="sm"
                @click="act(device, 'revoke')"
              >
                إلغاء التفعيل
              </UiButton>
              <UiButton variant="ghost" size="sm" @click="act(device, 'reset')">
                تحرير المقعد
              </UiButton>
            </div>
          </td>
        </tr>
      </UiTable>
    </UiCard>
  </div>
</template>
