<script setup lang="ts">
/**
 * Payment methods — which tenders this café accepts.
 *
 * **The screen with the largest hole behind it.** These rows are the pay buttons
 * at the till: `PaymentSheet` draws one per active method, and a branch with none
 * cannot settle a bill at all. They are rows and not an enum precisely so that
 * adding InstaPay needs no deployment (C10) — and there was nowhere to add one.
 * Full CRUD on the server, no screen anywhere, so in practice the tenders were
 * whatever the seed had written.
 *
 * Three flags decide real behaviour, which is why each says what it does here
 * rather than being a bare label:
 *
 *   * `counts_as_cash` is the one that touches money. It decides whether a
 *     payment is expected in the drawer at close, so a card method marked as cash
 *     makes every shift look short by exactly the card takings — a variance
 *     nobody can explain and everybody suspects the cashier of.
 *   * `opens_drawer` fires the kick-out. Wrong on a card method means the drawer
 *     springs open on every card sale, which is how a till gets robbed politely.
 *   * `requires_reference` forces the cashier to key the approval number, which
 *     is the only thing that makes a disputed card payment traceable afterwards.
 *
 * Retired, never deleted — a method is on historical payments, and history with a
 * dangling tender is a Z-report that cannot say how the money arrived.
 */
import { computed, onMounted, ref } from 'vue'

import { ApiError, api } from '@/api/client'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiBadge from '@/components/ui/UiBadge.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiInput from '@/components/ui/UiInput.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import { useAuthStore } from '@/stores/auth'

interface Method {
  id: string
  code: string
  name_ar: string
  opens_drawer: boolean
  requires_reference: boolean
  counts_as_cash: boolean
  is_active: boolean
  sort_order: number
}

type Draft = Omit<Method, 'id'> & { id?: string }

const EMPTY: Draft = {
  code: '',
  name_ar: '',
  opens_drawer: false,
  requires_reference: false,
  counts_as_cash: false,
  is_active: true,
  sort_order: 0,
}

const auth = useAuthStore()
const mayEdit = computed(() => auth.can('branch.edit_settings'))

const methods = ref<Method[]>([])
const loading = ref(true)
const error = ref('')
const notice = ref('')
const saving = ref(false)
const draft = ref<Draft>({ ...EMPTY })
const editing = ref(false)

const sorted = computed(() =>
  methods.value
    .slice()
    .sort(
      (a, b) =>
        Number(b.is_active) - Number(a.is_active) ||
        a.sort_order - b.sort_order ||
        a.name_ar.localeCompare(b.name_ar, 'ar'),
    ),
)

/** How many tenders the till can actually offer. Zero means it cannot settle. */
const activeCount = computed(() => methods.value.filter((m) => m.is_active).length)

async function load() {
  loading.value = true
  try {
    // `is_active=all`, not the default.
    //
    // The endpoint returns only ACTIVE methods unless asked, because the till
    // reads the same list to draw its buttons and must never be offered a tender
    // the branch retired. A management screen needs the opposite: a method it
    // cannot see is one it cannot switch back on.
    methods.value = await api.get<Method[]>('/payments/methods/', { is_active: 'all' })
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تحميل طرق الدفع.'
  } finally {
    loading.value = false
  }
}

function edit(method: Method) {
  draft.value = { ...method }
  editing.value = true
}

function reset() {
  draft.value = { ...EMPTY }
  editing.value = false
}

function flash(message: string) {
  notice.value = message
  setTimeout(() => (notice.value = ''), 4000)
}

async function save() {
  const name = draft.value.name_ar.trim()
  const code = draft.value.code.trim()
  if (!name || !code) {
    error.value = 'الاسم والكود مطلوبان.'
    return
  }

  saving.value = true
  try {
    const body = {
      code,
      name_ar: name,
      opens_drawer: draft.value.opens_drawer,
      requires_reference: draft.value.requires_reference,
      counts_as_cash: draft.value.counts_as_cash,
      is_active: draft.value.is_active,
      sort_order: draft.value.sort_order,
    }
    if (draft.value.id) {
      await api.patch(`/payments/methods/${draft.value.id}/`, body)
      flash(`تم حفظ «${name}».`)
    } else {
      await api.post('/payments/methods/', body)
      flash(`تمت إضافة «${name}».`)
    }
    reset()
    await load()
    error.value = ''
  } catch (e) {
    // The server's message, not a generic one: a duplicate code is the common
    // failure here and it names the field.
    error.value = e instanceof ApiError ? e.message : 'تعذّر حفظ طريقة الدفع.'
  } finally {
    saving.value = false
  }
}

/**
 * Retire a tender, or bring it back.
 *
 * Confirmed on the way OUT only, and the sentence says what breaks: retiring the
 * last method leaves a till that cannot take money, and that is discovered by a
 * cashier with a customer in front of them.
 */
async function toggleActive(method: Method) {
  if (method.is_active) {
    const last = activeCount.value === 1
    const warning = last
      ? '\n\nهذه آخر طريقة دفع مفعّلة — بإيقافها لن يستطيع الكاشير تحصيل أي فاتورة.'
      : ''
    if (!window.confirm(`إيقاف «${method.name_ar}»؟${warning}`)) return
  }

  try {
    await api.patch(`/payments/methods/${method.id}/`, { is_active: !method.is_active })
    flash(method.is_active ? `تم إيقاف «${method.name_ar}».` : `تم تفعيل «${method.name_ar}».`)
    await load()
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تغيير حالة طريقة الدفع.'
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-bold text-ink">طرق الدفع</h1>
      <p class="mt-1 text-sm text-ink-muted">
        هذه هي أزرار الدفع في نقطة البيع. الكاشير لا يستطيع تحصيل أي فاتورة بدون طريقة دفع
        واحدة على الأقل.
      </p>
    </div>

    <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>
    <UiAlert v-if="notice" tone="success">{{ notice }}</UiAlert>

    <!--
      The one state worth interrupting for. Not a validation message — nothing is
      being submitted — but a branch in this state cannot take money at all, and
      the till reports it as a missing button rather than as a cause.
    -->
    <UiAlert v-if="!loading && activeCount === 0" tone="warning">
      لا توجد طريقة دفع مفعّلة — نقطة البيع لن تعرض زر الدفع إطلاقاً.
    </UiAlert>

    <UiSkeleton v-if="loading" :rows="4" />

    <template v-else>
      <UiEmpty
        v-if="!methods.length"
        icon="cash"
        title="لا توجد طرق دفع"
        description="أضف «نقدي» على الأقل، وإلا لن تستطيع نقطة البيع تحصيل أي فاتورة."
      />

      <div v-else class="grid gap-3">
        <UiCard
          v-for="method in sorted"
          :key="method.id"
          :class="method.is_active ? '' : 'opacity-60'"
        >
          <div class="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div class="flex flex-wrap items-center gap-2">
                <span class="text-lg font-bold text-ink">{{ method.name_ar }}</span>
                <UiBadge tone="neutral">
                  <span dir="ltr">{{ method.code }}</span>
                </UiBadge>
                <UiBadge v-if="!method.is_active" tone="warning">موقوفة</UiBadge>
                <UiBadge v-if="method.counts_as_cash" tone="success">تُحسب في الدرج</UiBadge>
                <UiBadge v-if="method.requires_reference" tone="info">تتطلب رقم مرجع</UiBadge>
              </div>
              <p class="mt-1 text-sm text-ink-muted">
                {{ method.opens_drawer ? 'تفتح الدرج' : 'لا تفتح الدرج' }}
                · الترتيب {{ method.sort_order }}
              </p>
            </div>

            <div v-if="mayEdit" class="flex flex-wrap items-center gap-2">
              <UiButton size="sm" variant="secondary" @click="edit(method)">تعديل</UiButton>
              <UiButton size="sm" variant="ghost" @click="toggleActive(method)">
                {{ method.is_active ? 'إيقاف' : 'تفعيل' }}
              </UiButton>
            </div>
          </div>
        </UiCard>
      </div>

      <UiCard v-if="mayEdit">
        <h2 class="text-sm font-semibold text-ink">
          {{ editing ? 'تعديل طريقة دفع' : 'إضافة طريقة دفع' }}
        </h2>

        <form class="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3" @submit.prevent="save">
          <UiInput v-model="draft.name_ar" label="الاسم" required />
          <UiInput
            v-model="draft.code"
            label="الكود"
            hint="يظهر في التقارير وتقرير الوردية."
            ltr
            required
          />
          <UiInput v-model.number="draft.sort_order" label="الترتيب" type="number" />

          <label class="flex items-start gap-2 text-sm text-ink">
            <input v-model="draft.counts_as_cash" type="checkbox" class="mt-1 h-4 w-4 rounded" />
            <span>
              تُحسب ضمن نقدية الدرج
              <span class="block text-xs text-ink-faint">
                تفعيلها على بطاقة يجعل كل وردية تبدو ناقصة بمقدار مبيعات البطاقات.
              </span>
            </span>
          </label>

          <label class="flex items-start gap-2 text-sm text-ink">
            <input v-model="draft.opens_drawer" type="checkbox" class="mt-1 h-4 w-4 rounded" />
            <span>
              تفتح درج النقدية
              <span class="block text-xs text-ink-faint">للنقدي فقط عادةً.</span>
            </span>
          </label>

          <label class="flex items-start gap-2 text-sm text-ink">
            <input
              v-model="draft.requires_reference"
              type="checkbox"
              class="mt-1 h-4 w-4 rounded"
            />
            <span>
              تتطلب رقم مرجع
              <span class="block text-xs text-ink-faint">
                رقم موافقة البطاقة — وهو الشيء الوحيد الذي يجعل دفعة متنازعاً عليها قابلة
                للتتبع.
              </span>
            </span>
          </label>

          <div class="flex items-center gap-2 sm:col-span-2 lg:col-span-3">
            <UiButton type="submit" :loading="saving">
              {{ editing ? 'حفظ' : 'إضافة' }}
            </UiButton>
            <UiButton v-if="editing" variant="ghost" @click="reset">إلغاء</UiButton>
          </div>
        </form>

        <p class="mt-3 text-xs text-ink-faint">
          طريقة الدفع تُوقَف ولا تُحذَف — فهي مسجّلة على دفعات سابقة، وتقرير وردية بمرجع محذوف
          لا يعرف كيف وصلت النقود. الموقوفة تظهر في «المحذوفات».
        </p>
      </UiCard>
    </template>
  </div>
</template>
