<script setup lang="ts">
/**
 * Demo data, switched from a screen.
 *
 * A presentation needs the site in BOTH of its honest states — a trading
 * fortnight, and a configured café with an empty ledger — and switching between
 * them used to mean a shell on the server. Two buttons now, both running the
 * seed's own tested reset machinery in the WORKER (a fortnight of trading is
 * minutes of real orders; a request that takes minutes is a gunicorn worker held
 * hostage).
 *
 * It is a REBUILD, not a visibility toggle, on purpose. A "hide the data" switch
 * would leave every report, floor board and kitchen screen to individually agree
 * about what is hidden, and the first one that forgot would contradict the
 * screen beside it. Deleting and regenerating is the toggle, implemented
 * honestly — and the seed regenerates in about two minutes.
 *
 * The one real consequence is stated ON the buttons rather than discovered
 * after: a rebuild reissues the licence, so every enrolled till dies and must
 * re-enrol. In demo mode the new key is readable on the licensing screen.
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { ApiError, api } from '@/api/client'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import UiStat from '@/components/ui/UiStat.vue'

interface Job {
  state: 'queued' | 'running' | 'done' | 'failed'
  mode: 'full' | 'empty'
  detail: string
  at: string | null
}

interface Status {
  orders: number
  products: number
  open_sessions: number
  job: Job | null
}

const status = ref<Status | null>(null)
const loading = ref(true)
const error = ref('')
const submitting = ref(false)

const job = computed(() => status.value?.job ?? null)
const busy = computed(() => job.value?.state === 'queued' || job.value?.state === 'running')

/** What the numbers say the café currently is. */
const shape = computed(() => {
  if (!status.value) return ''
  return status.value.orders > 0 ? 'full' : 'empty'
})

async function load() {
  try {
    status.value = await api.get<Status>('/ops/demo-data/')
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تحميل الحالة.'
  } finally {
    loading.value = false
  }
}

/**
 * Poll while a rebuild runs.
 *
 * Five seconds, only while a job is live — the seed takes minutes and the only
 * place its outcome is visible is this screen. A failure that lived in worker
 * logs would be a spinner that never stops.
 */
const POLL_MS = 5000
let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  load()
  timer = setInterval(() => {
    if (busy.value) load()
  }, POLL_MS)
})
onBeforeUnmount(() => clearInterval(timer))

async function rebuild(mode: 'full' | 'empty') {
  const what =
    mode === 'full'
      ? 'إعادة بناء بيانات العرض كاملة — أسبوعان من التداول وصالة مشغولة؟'
      : 'مسح كل التداول — كافيه مجهّز بالكامل وبلا أي مبيعات؟'
  const warning =
    '\n\nسيُعاد إصدار الترخيص: كل جهاز كاشير مفعَّل سيحتاج إعادة تفعيل بالمفتاح الجديد ' +
    '(يظهر في شاشة «التراخيص»).\n\nالعملية تستغرق دقائق وتعمل في الخلفية.'
  if (!window.confirm(what + warning)) return

  submitting.value = true
  try {
    await api.post('/ops/demo-data/', { mode })
    error.value = ''
    await load()
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر بدء العملية.'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-bold text-ink">بيانات العرض</h1>
      <p class="mt-1 text-sm text-ink-muted">
        بدّل بين كافيه بأسبوعين من التداول وكافيه مجهّز بلا مبيعات — لعرض الموقع في الحالتين.
      </p>
    </div>

    <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>

    <UiAlert v-if="job?.state === 'failed'" tone="error">
      فشلت آخر عملية: {{ job.detail || 'راجع سجلّ الـ worker.' }}
    </UiAlert>
    <UiAlert v-else-if="busy" tone="info">
      جارٍ {{ job?.mode === 'empty' ? 'المسح' : 'البناء' }}… تستغرق العملية دقائق، والشاشة
      تتحدّث تلقائياً.
      <template v-if="job?.state === 'queued'">
        ما زالت في الانتظار — إن بقيت هكذا فتأكد أن خدمة الـ worker تعمل.
      </template>
    </UiAlert>
    <UiAlert v-else-if="job?.state === 'done'" tone="success">
      اكتملت آخر عملية ({{ job.mode === 'empty' ? 'مسح' : 'بناء كامل' }}).
    </UiAlert>

    <UiSkeleton v-if="loading" :rows="3" />

    <template v-else-if="status">
      <div class="grid gap-3 sm:grid-cols-3">
        <UiStat label="الطلبات" :value="String(status.orders)" />
        <UiStat label="المنتجات" :value="String(status.products)" />
        <UiStat label="طاولات مشغولة" :value="String(status.open_sessions)" />
      </div>

      <div class="grid gap-3 sm:grid-cols-2">
        <UiCard :class="shape === 'full' ? 'ring-2 ring-brand-700' : ''">
          <h2 class="text-sm font-semibold text-ink">عرض ببيانات كاملة</h2>
          <p class="mt-1 text-sm text-ink-muted">
            أسبوعان من التداول، صالة مشغولة، مطبخ يعمل، وتقارير مليئة — الكافيه في يوم حقيقي.
          </p>
          <UiButton class="mt-3" :loading="submitting" :disabled="busy" @click="rebuild('full')">
            {{ shape === 'full' ? 'إعادة البناء' : 'تحميل البيانات' }}
          </UiButton>
        </UiCard>

        <UiCard :class="shape === 'empty' ? 'ring-2 ring-brand-700' : ''">
          <h2 class="text-sm font-semibold text-ink">عرض بدون بيانات</h2>
          <p class="mt-1 text-sm text-ink-muted">
            نفس الكافيه مجهّزاً — منيو وصالة وترخيص — وبلا أي مبيعات: كل شاشة بحالتها الفارغة،
            وأول فعل للكاشير فتح وردية.
          </p>
          <UiButton
            class="mt-3"
            variant="secondary"
            :loading="submitting"
            :disabled="busy"
            @click="rebuild('empty')"
          >
            مسح التداول
          </UiButton>
        </UiCard>
      </div>

      <p class="text-xs text-ink-faint">
        إعادة البناء تعيد إصدار الترخيص — كل جهاز مفعَّل يموت مع الترخيص القديم ويُعاد تفعيله
        بالمفتاح الجديد من شاشة «التراخيص». هذا سلوك «إعادة الضبط» الموثَّق، لا عرَض جانبي:
        جهاز يحمل سرّاً لترخيص لم يعد موجوداً هو اعتماد قديم ما زال يجيب.
      </p>
    </template>
  </div>
</template>
