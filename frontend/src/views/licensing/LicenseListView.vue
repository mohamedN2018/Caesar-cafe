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
  /**
   * The readable key — a non-empty string only while the server has DEMO_MODE on.
   *
   * The server decides, not this screen. A real installation stores nothing to
   * show, so there is no way to leak a key here by getting a `v-if` wrong: the
   * field simply arrives empty.
   */
  readable_key: string
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

/**
 * Copied-confirmation, because a key you cannot verify you took is a key you
 * will assume you took.
 *
 * The plaintext exists for one render and is then unrecoverable — the server
 * keeps an HMAC. That makes the copy button the single most load-bearing control
 * on this screen: miss it and the remedy is regenerating, which invalidates every
 * till already activated against the old key.
 */
const copied = ref(false)

/** Which key was last copied, so the button can confirm it. */
const copiedKey = ref('')

async function copy(value: string) {
  try {
    await navigator.clipboard.writeText(value)
    copiedKey.value = value
    setTimeout(() => (copiedKey.value = ''), 2500)
  } catch {
    error.value = 'تعذّر النسخ تلقائياً — حدّد المفتاح وانسخه يدوياً.'
  }
}

async function copyKey() {
  try {
    await navigator.clipboard.writeText(revealedKey.value)
    copied.value = true
    setTimeout(() => (copied.value = false), 2500)
  } catch {
    // Clipboard access can be refused — an insecure origin, a locked-down
    // browser. The key is `select-all` in the markup precisely so that refusing
    // this leaves the operator able to select and copy by hand rather than
    // stranded.
    error.value = 'تعذّر النسخ تلقائياً — حدّد المفتاح وانسخه يدوياً.'
  }
}

const columns = [
  { key: 'key', label: 'المفتاح' },
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

async function act(license: License, action: string, body: Record<string, unknown> = {}) {
  error.value = ''
  try {
    const result = await api.post<{ license_key?: string }>(
      `/licensing/licenses/${license.id}/${action}/`,
      body,
    )
    if (result.license_key) revealedKey.value = result.license_key
    await load()
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : 'تعذر تنفيذ الإجراء'
  }
}

/**
 * Renewal — the one licence action the backend had and no screen reached.
 *
 * An expired licence locks every till on it, so the remedy needs to be a button
 * an owner can find at the moment it happens, not a call to whoever has shell
 * access. `renew` also revives an EXPIRED or PENDING licence to ACTIVE, which is
 * exactly the state somebody is in when they come looking for it.
 *
 * The date is asked for rather than assumed. A default of "a year from today"
 * would be a guess about somebody's commercial terms, and a wrong expiry is a
 * till that stops on a day nobody expected.
 */
async function renew(license: License) {
  const current = license.expires_at ? license.expires_at.slice(0, 10) : 'مدى الحياة'
  const answer = window.prompt(
    `تجديد الترخيص «${license.masked_key}»\n\n` +
      `ينتهي حالياً: ${current}\n` +
      'اكتب تاريخ الانتهاء الجديد بالصيغة YYYY-MM-DD:',
    license.expires_at ? license.expires_at.slice(0, 10) : '',
  )
  if (!answer) return

  // Checked here as well as on the server: a malformed date would come back as a
  // field error the operator has to decode, and the format is the whole input.
  if (!/^\d{4}-\d{2}-\d{2}$/.test(answer.trim())) {
    error.value = 'صيغة التاريخ غير صحيحة — استخدم YYYY-MM-DD.'
    return
  }

  await act(license, 'renew', { expires_at: answer.trim() })
}

/**
 * Regenerating is destructive to every activated till, so it says so first.
 *
 * `window.confirm` rather than a modal because the sentence is the whole point
 * and a bespoke dialog would only be a nicer frame around the same words.
 */
async function regenerate(license: License) {
  const ok = window.confirm(
    `توليد مفتاح جديد للترخيص «${license.masked_key}»؟\n\n` +
      'المفتاح الحالي سيتوقف فوراً، وكل جهاز مفعَّل به سيحتاج إعادة تفعيل.\n' +
      'المفتاح الجديد يظهر مرة واحدة فقط.',
  )
  if (!ok) return
  await act(license, 'regenerate-key')
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-bold text-ink">التراخيص</h1>
      <p class="mt-1 text-sm text-ink-muted">
        المفتاح يظهر مرة واحدة عند الإنشاء فقط — الخادم يحفظ بصمته لا نصّه.
      </p>
    </div>

    <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>

    <UiAlert v-if="revealedKey" tone="warning">
      <p class="font-semibold">احفظ هذا المفتاح الآن — لن يظهر مرة أخرى.</p>
      <!--
        `select-all` stays on the key itself. The copy button is the fast path;
        this is the one that still works when the clipboard API is refused, which
        it is on any insecure origin.
      -->
      <p class="mt-2 select-all font-mono text-lg tracking-wider" dir="ltr">{{ revealedKey }}</p>
      <div class="mt-3 flex items-center gap-2">
        <UiButton size="sm" @click="copyKey">
          {{ copied ? 'تم النسخ' : 'نسخ المفتاح' }}
        </UiButton>
        <button class="text-sm underline" @click="((revealedKey = ''), (copied = false))">
          إخفاء
        </button>
      </div>
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
        <tr v-for="license in licenses" :key="license.id" class="hover:bg-surface-muted">
          <!--
            The readable key when the server sends one, the masked one otherwise.

            `readable_key` is non-empty only while DEMO_MODE is on — the server
            decides and a real installation stores nothing to show, so there is
            no way to leak a key here by getting a condition wrong. Selectable so
            it can be copied without a button, and with one for the common case.
          -->
          <td class="px-4 py-3 font-mono text-sm" dir="ltr">
            <template v-if="license.readable_key">
              <span class="select-all text-ink">{{ license.readable_key }}</span>
              <button
                type="button"
                class="ms-2 text-xs text-brand-700 underline"
                @click="copy(license.readable_key)"
              >
                {{ copiedKey === license.readable_key ? 'تم النسخ' : 'نسخ' }}
              </button>
            </template>
            <span v-else class="text-ink-muted">{{ license.masked_key }}</span>
          </td>
          <td class="px-4 py-3 text-ink-muted">{{ license.license_type }}</td>
          <td class="px-4 py-3 text-end tabular-nums">
            <span :class="license.seats_available === 0 && 'font-semibold text-warning'">
              {{ seats(license) }}
            </span>
          </td>
          <td class="whitespace-nowrap px-4 py-3 text-sm text-ink-muted">
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

                <!--
                  The way back from a lost key. The backend has had
                  `regenerate-key` all along and nothing on this screen reached
                  it, so an owner who mislaid a key had no route that did not
                  involve a shell.

                  It asks first, and names the real consequence rather than a
                  generic "are you sure": the old key stops working, so every
                  till already activated against it must be activated again.
                -->
                <UiButton
                  variant="ghost"
                  size="sm"
                  @click="regenerate(license)"
                >
                  مفتاح جديد
                </UiButton>

                <!--
                  Renewal. Also revives an expired or pending licence to active,
                  which is the state somebody is in when they come looking for
                  this button.
                -->
                <UiButton variant="ghost" size="sm" @click="renew(license)">
                  تجديد
                </UiButton>
              </template>
            </div>
          </td>
        </tr>
      </UiTable>
    </UiCard>
  </div>
</template>
