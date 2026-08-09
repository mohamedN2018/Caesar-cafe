<script setup lang="ts">
/**
 * Guardians and children.
 *
 * The register behind the play area. Two things make it worth a screen of its
 * own rather than a tab on check-in:
 *
 *   * **A returning guardian turns a check-in into three fields.** Staff search
 *     by phone, pick the family, and the child's name, age and medical notes
 *     come with it. A parent holding a restless child will not tolerate a long
 *     form twice.
 *   * **Medical notes live here, not on the session.** An allergy is a property
 *     of the child, not of today's visit, and re-typing it every time is how it
 *     eventually gets typed wrong — or left blank.
 *
 * Search is by phone because that is what staff have: the parent says a number,
 * not a name spelled the same way twice.
 */
import { computed, onMounted, ref, watch } from 'vue'

import { ApiError, api } from '@/api/client'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiBadge from '@/components/ui/UiBadge.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiInput from '@/components/ui/UiInput.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import { useAuthStore } from '@/stores/auth'

interface Guardian {
  id: string
  full_name: string
  phone: string
  national_id: string
  visit_count: number
  notes: string
}

interface Child {
  id: string
  guardian: string
  first_name: string
  birth_date: string | null
  age_months_snapshot: number | null
  medical_notes: string
  consent_recorded: boolean
}

const auth = useAuthStore()
const mayEdit = computed(() => auth.can('kids.checkin'))

const guardians = ref<Guardian[]>([])
const children = ref<Child[]>([])
const selected = ref<Guardian | null>(null)
const phoneQuery = ref('')
const loading = ref(true)
const error = ref('')
const saving = ref(false)

const newChild = ref({ first_name: '', birth_date: '', medical_notes: '' })

const sorted = computed(() =>
  guardians.value.slice().sort((a, b) => b.visit_count - a.visit_count),
)

/** Children with something staff must read before the child goes in. */
const flagged = computed(() => children.value.filter((c) => c.medical_notes.trim()))

async function load() {
  loading.value = true
  try {
    guardians.value = await api.get<Guardian[]>(
      '/kids/guardians/',
      phoneQuery.value.trim() ? { phone: phoneQuery.value.trim() } : undefined,
    )
    error.value = ''
    // Keep a selection only while it is still in the result set, otherwise the
    // detail panel describes a family the list no longer shows.
    if (selected.value && !guardians.value.some((g) => g.id === selected.value?.id)) {
      selected.value = null
      children.value = []
    }
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تحميل السجل.'
  } finally {
    loading.value = false
  }
}

async function pick(guardian: Guardian) {
  selected.value = guardian
  children.value = []
  try {
    children.value = await api.get<Child[]>('/kids/children/', { guardian: guardian.id })
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تحميل بيانات الأطفال.'
  }
}

async function addChild() {
  if (!selected.value || !newChild.value.first_name.trim()) return
  saving.value = true
  try {
    await api.post('/kids/children/', {
      guardian: selected.value.id,
      first_name: newChild.value.first_name.trim(),
      birth_date: newChild.value.birth_date || null,
      medical_notes: newChild.value.medical_notes.trim(),
    })
    newChild.value = { first_name: '', birth_date: '', medical_notes: '' }
    await pick(selected.value)
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر إضافة الطفل.'
  } finally {
    saving.value = false
  }
}

async function saveNotes(child: Child) {
  try {
    await api.patch(`/kids/children/${child.id}/`, { medical_notes: child.medical_notes })
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر حفظ الملاحظات.'
  }
}

let debounce: number | undefined
watch(phoneQuery, () => {
  if (debounce) window.clearTimeout(debounce)
  debounce = window.setTimeout(load, 350)
})

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-bold text-slate-900">أولياء الأمور والأطفال</h1>
      <p class="mt-1 text-sm text-slate-500">
        البحث بالهاتف — وهو ما لدى الموظف فعلاً؛ الاسم يُكتب بأكثر من صورة.
      </p>
    </div>

    <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>

    <UiInput
      v-model="phoneQuery"
      label="بحث برقم الهاتف"
      placeholder="0100..."
      ltr
      class="max-w-sm"
    />

    <UiSkeleton v-if="loading" :rows="5" />

    <template v-else>
      <UiEmpty
        v-if="!guardians.length"
        icon="guardians"
        title="لا توجد نتائج"
        description="سيظهر ولي الأمر هنا بعد أول دخول للطفل."
      />

      <div v-else class="grid gap-4 lg:grid-cols-2">
        <div class="space-y-2">
          <button
            v-for="guardian in sorted"
            :key="guardian.id"
            class="w-full rounded-lg border px-4 py-3 text-start transition"
            :class="
              selected?.id === guardian.id
                ? 'border-brand-300 bg-brand-50'
                : 'border-slate-200 bg-white hover:bg-slate-50'
            "
            @click="pick(guardian)"
          >
            <div class="flex flex-wrap items-center justify-between gap-2">
              <span class="font-semibold text-slate-900">{{ guardian.full_name }}</span>
              <UiBadge tone="neutral">{{ guardian.visit_count }} زيارة</UiBadge>
            </div>
            <p class="mt-0.5 font-mono text-sm text-slate-500" dir="ltr">
              {{ guardian.phone || '—' }}
            </p>
          </button>
        </div>

        <UiCard v-if="selected">
          <div class="flex flex-wrap items-start justify-between gap-2">
            <div>
              <h2 class="text-lg font-bold text-slate-900">{{ selected.full_name }}</h2>
              <p class="mt-0.5 font-mono text-sm text-slate-500" dir="ltr">
                {{ selected.phone || '—' }}
              </p>
            </div>
            <UiBadge v-if="flagged.length" tone="warning">
              {{ flagged.length }} ملاحظة طبية
            </UiBadge>
          </div>

          <p v-if="selected.notes" class="mt-2 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-700">
            {{ selected.notes }}
          </p>

          <UiEmpty
            v-if="!children.length"
            icon="child"
            title="لا يوجد أطفال مسجلون"
            description="أضف طفلاً ليظهر تلقائياً عند الدخول القادم."
          />

          <div v-else class="mt-4 space-y-3">
            <div
              v-for="child in children"
              :key="child.id"
              class="rounded-lg border border-slate-200 px-4 py-3"
              :class="child.medical_notes.trim() ? 'bg-amber-50/50' : ''"
            >
              <div class="flex flex-wrap items-center gap-2">
                <span class="font-semibold text-slate-900">{{ child.first_name }}</span>
                <UiBadge v-if="child.age_months_snapshot" tone="neutral">
                  {{ Math.floor(child.age_months_snapshot / 12) }} سنة
                </UiBadge>
                <UiBadge v-if="!child.consent_recorded" tone="warning">بدون إقرار</UiBadge>
              </div>

              <label class="mt-2 block">
                <span class="mb-1 block text-xs font-medium text-slate-600">
                  ملاحظات طبية — تظهر على شاشة الصالة دائماً
                </span>
                <textarea
                  v-model="child.medical_notes"
                  rows="2"
                  :disabled="!mayEdit"
                  class="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm
                         focus:border-brand-700 focus:outline-none focus:ring-2 focus:ring-brand-700/30"
                  placeholder="حساسية، دواء، حالة يجب أن يعرفها الموظف"
                  @blur="mayEdit && saveNotes(child)"
                />
              </label>
            </div>
          </div>

          <form v-if="mayEdit" class="mt-4 space-y-3 border-t border-slate-200 pt-4" @submit.prevent="addChild">
            <h3 class="text-sm font-semibold text-slate-900">إضافة طفل</h3>
            <div class="grid gap-3 sm:grid-cols-2">
              <UiInput v-model="newChild.first_name" label="الاسم" required />
              <UiInput v-model="newChild.birth_date" label="تاريخ الميلاد" type="date" />
            </div>
            <UiInput v-model="newChild.medical_notes" label="ملاحظات طبية" />
            <UiButton type="submit" :loading="saving" :disabled="!newChild.first_name.trim()">
              إضافة
            </UiButton>
          </form>
        </UiCard>

        <UiCard v-else>
          <p class="text-sm text-slate-500">اختر ولي أمر لعرض الأطفال والملاحظات الطبية.</p>
        </UiCard>
      </div>
    </template>
  </div>
</template>
