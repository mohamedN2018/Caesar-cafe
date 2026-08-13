<script setup lang="ts">
/**
 * Notifications: turning them on, and what has been sent.
 *
 * The screen exists because a permission prompt fired on page load is a
 * permission prompt refused. A browser gives a site **one** chance; if it is
 * dismissed the only way back is through settings nobody finds. So the ask
 * happens here, behind a button that says what it is for, next to the list of
 * things it will actually send.
 *
 * Showing the alert history beside the switch is deliberate too. "What will
 * this send me?" is the question somebody asks before granting permission, and
 * answering it with real examples from their own cafe beats any description.
 */
import { computed, onMounted, ref } from 'vue'

import { ApiError, api } from '@/api/client'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiBadge from '@/components/ui/UiBadge.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import { dateTime } from '@/lib/format'
import * as push from '@/modules/push'
import { useAuthStore } from '@/stores/auth'

interface Subscription {
  id: string
  label: string
  last_sent_at: string | null
  created_at: string
}

interface Alert {
  id: string
  kind: string
  kind_label: string
  title: string
  body: string
  url: string
  delivered: number
  created_at: string
}

/** What the switch will actually send. Answering this is most of the screen. */
const WHAT_IT_SENDS = [
  { icon: 'cash', title: 'فرق في الدرج', body: 'وردية أُغلقت بعجز أو زيادة فوق الحد المسموح.' },
  { icon: 'kitchen', title: 'تأخير في المطبخ', body: 'تذكرة تجاوزت الوقت المستهدف لمحطتها بفارق كبير.' },
  { icon: 'kids', title: 'طفل تجاوز وقته', body: 'جلسة لعب استمرت بعد وقتها المتوقع.' },
  { icon: 'monitor', title: 'جهاز غير متصل', body: 'كاشير توقف عن المزامنة لفترة طويلة.' },
  { icon: 'save', title: 'فشل نسخة احتياطية', body: 'أهدأ عطل خطير في النظام — يصل حتى في ساعات الصمت.' },
]

const auth = useAuthStore()
const mayReadHistory = computed(() => auth.can('reports.sales'))

const state = ref<push.PushState>('available')
const devices = ref<Subscription[]>([])
const history = ref<Alert[]>([])
const loading = ref(true)
const busy = ref(false)
const error = ref('')

const isOn = computed(() => state.value === 'subscribed')
const needsInstall = computed(
  () => state.value === 'unsupported' && /iPhone|iPad/i.test(navigator.userAgent),
)

async function load() {
  try {
    state.value = await push.currentState()
  } catch {
    state.value = 'unsupported'
  }

  try {
    devices.value = await api.get<Subscription[]>('/notifications/subscriptions/')
  } catch {
    devices.value = []
  }

  if (mayReadHistory.value) {
    // `optional`: somebody without the reporting code simply gets no history
    // panel rather than a refusal about a request they did not make.
    history.value = (await api.optional<Alert[]>('/notifications/alerts/')) ?? []
  }
  loading.value = false
}

async function turnOn() {
  busy.value = true
  error.value = ''
  try {
    state.value = await push.enable()
    if (state.value === 'subscribed') {
      devices.value = await api.get<Subscription[]>('/notifications/subscriptions/')
    }
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تفعيل الإشعارات.'
  } finally {
    busy.value = false
  }
}

async function turnOff(id?: string) {
  busy.value = true
  try {
    await push.disable(id)
    state.value = await push.currentState()
    devices.value = await api.get<Subscription[]>('/notifications/subscriptions/')
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر إيقاف الإشعارات.'
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-bold text-ink">الإشعارات</h1>
      <p class="mt-1 text-sm text-ink-muted">
        تابع الكافيه وأنت بعيد. الإشعارات تصل حتى والتطبيق مقفول.
      </p>
    </div>

    <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>

    <UiSkeleton v-if="loading" :rows="5" />

    <template v-else>
      <!-- ── the switch ──────────────────────────────────────────────────── -->
      <UiCard>
        <div class="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 class="text-lg font-bold text-ink">
              {{ isOn ? 'الإشعارات مفعّلة على هذا الجهاز' : 'فعّل الإشعارات على هذا الجهاز' }}
            </h2>
            <p class="mt-1 text-sm text-ink-muted">
              المتصفح هيسألك مرة واحدة بس — لو رفضت، إعادة التفعيل بتبقى من إعدادات المتصفح نفسه.
            </p>
          </div>

          <UiButton
            v-if="state === 'available'"
            :loading="busy"
            @click="turnOn"
          >
            تفعيل الإشعارات
          </UiButton>
          <UiBadge v-else-if="isOn" tone="success">مفعّلة</UiBadge>
        </div>

        <!-- Each refusal gets the sentence that names the actual way out. -->
        <UiAlert v-if="state === 'denied'" tone="warning" class="mt-3">
          المتصفح رافض الإشعارات لهذا الموقع. افتح إعدادات الموقع في المتصفح واسمح بالإشعارات،
          وبعدين ارجع هنا.
        </UiAlert>

        <UiAlert v-else-if="state === 'unconfigured'" tone="warning" class="mt-3">
          الخادم لسه مش مظبوط لإرسال الإشعارات. المسؤول التقني يشغّل
          <code class="font-mono text-xs">manage.py generate_vapid_keys</code> ويحط المفاتيح في
          إعدادات الخادم.
        </UiAlert>

        <UiAlert v-else-if="needsInstall" tone="info" class="mt-3">
          على الآيفون، الإشعارات بتشتغل بعد ما تضيف التطبيق للشاشة الرئيسية: زرار المشاركة ←
          «إضافة إلى الشاشة الرئيسية»، وبعدين افتحه من هناك.
        </UiAlert>

        <UiAlert v-else-if="state === 'unsupported'" tone="info" class="mt-3">
          المتصفح ده مش بيدعم الإشعارات. جرّب كروم أو إيدج أو سفاري حديث.
        </UiAlert>
      </UiCard>

      <!-- ── what it sends ───────────────────────────────────────────────── -->
      <UiCard>
        <h2 class="text-sm font-semibold text-ink">إيه اللي هيوصلك</h2>
        <p class="mt-1 text-xs text-ink-muted">
          قائمة قصيرة عن قصد. تنبيهات كتير معناها إنك هتقفلها كلها، وساعتها اللي يهم بيتقفل معاها.
        </p>
        <ul class="mt-3 grid gap-2 sm:grid-cols-2">
          <li
            v-for="item in WHAT_IT_SENDS"
            :key="item.title"
            class="flex gap-3 rounded-lg bg-surface-muted px-3 py-2"
          >
            <span aria-hidden="true">{{ item.icon }}</span>
            <span>
              <span class="block text-sm font-medium text-ink">{{ item.title }}</span>
              <span class="block text-xs text-ink-muted">{{ item.body }}</span>
            </span>
          </li>
        </ul>
        <p class="mt-3 text-xs text-ink-faint">
          الحدود وساعات الصمت تتظبط من الإعدادات ← التنبيهات.
        </p>
      </UiCard>

      <!-- ── devices ─────────────────────────────────────────────────────── -->
      <UiCard v-if="devices.length">
        <h2 class="text-sm font-semibold text-ink">الأجهزة المشتركة</h2>
        <ul class="mt-3 divide-y divide-line divide-[var(--border)]">
          <li
            v-for="device in devices"
            :key="device.id"
            class="flex flex-wrap items-center justify-between gap-2 py-2.5"
          >
            <div>
              <span class="text-sm font-medium text-ink">{{ device.label || 'متصفح' }}</span>
              <span class="ms-2 text-xs text-ink-muted">
                اشترك {{ dateTime(device.created_at) }}
                <template v-if="device.last_sent_at">
                  · آخر إشعار {{ dateTime(device.last_sent_at) }}
                </template>
              </span>
            </div>
            <UiButton size="sm" variant="ghost" :loading="busy" @click="turnOff(device.id)">
              إيقاف
            </UiButton>
          </li>
        </ul>
      </UiCard>

      <!-- ── history ─────────────────────────────────────────────────────── -->
      <UiCard v-if="mayReadHistory">
        <h2 class="text-sm font-semibold text-ink">آخر التنبيهات</h2>

        <UiEmpty
          v-if="!history.length"
          icon="silent"
          title="لا توجد تنبيهات"
          description="مفيش حاجة استدعت تنبيهاً — وده هو المطلوب."
        />

        <ul v-else class="mt-3 divide-y divide-line divide-[var(--border)]">
          <li v-for="alert in history" :key="alert.id" class="py-3">
            <div class="flex flex-wrap items-center gap-2">
              <UiBadge tone="neutral">{{ alert.kind_label }}</UiBadge>
              <span class="text-sm font-medium text-ink">{{ alert.title }}</span>
              <span class="text-xs text-ink-muted">{{ dateTime(alert.created_at) }}</span>
            </div>
            <p class="mt-0.5 text-sm text-ink-muted">{{ alert.body }}</p>
            <p v-if="!alert.delivered" class="mt-0.5 text-xs text-[var(--warning)]">
              سُجِّل ولم يُرسَل — مفيش جهاز مشترك وقتها.
            </p>
          </li>
        </ul>
      </UiCard>
    </template>
  </div>
</template>
