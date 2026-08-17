<script setup lang="ts">
/**
 * Menu categories — the tabs across the top of the till.
 *
 * This screen listed them and did nothing else: no add, no edit, no way to switch
 * one off, and no error state either — the load was a `try/finally` with no
 * `catch`, so a failed request left an empty list that read as "no categories"
 * rather than as "the request failed". The endpoint had full CRUD the whole time.
 *
 * Two things here have consequences past this page:
 *
 *   * **`sort_order` IS the till's tab order.** The cashier's muscle memory is
 *     built on it, so reordering categories mid-service moves the buttons under
 *     somebody's thumb.
 *   * **Switching a category off hides every product in it** from the menu. The
 *     product count is on the row for exactly that reason, and the confirmation
 *     says the number out loud.
 *
 * Retired, never deleted: a category is on historical line items, and the
 * recycle bin is where a retired one comes back from.
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

interface Category {
  id: string
  name_ar: string
  name_en: string
  color: string
  sort_order: number
  is_active: boolean
  product_count: number
}

type Draft = {
  id?: string
  name_ar: string
  name_en: string
  color: string
  sort_order: number
}

const EMPTY: Draft = { name_ar: '', name_en: '', color: '#1f3363', sort_order: 0 }

const auth = useAuthStore()
const mayCreate = computed(() => auth.can('catalog.create'))
const mayEdit = computed(() => auth.can('catalog.edit'))

const categories = ref<Category[]>([])
const loading = ref(true)
const error = ref('')
const notice = ref('')
const saving = ref(false)
const draft = ref<Draft>({ ...EMPTY })
const editing = ref(false)
const search = ref('')

const shown = computed(() => {
  const term = search.value.trim()
  const rows = term
    ? categories.value.filter(
        (c) => c.name_ar.includes(term) || (c.name_en ?? '').toLowerCase().includes(term.toLowerCase()),
      )
    : categories.value
  // Active first, then the till's own order — so the list reads the way the till
  // does, and retired ones sink rather than interleaving.
  return rows
    .slice()
    .sort(
      (a, b) =>
        Number(b.is_active) - Number(a.is_active) ||
        a.sort_order - b.sort_order ||
        a.name_ar.localeCompare(b.name_ar, 'ar'),
    )
})

function flash(message: string) {
  notice.value = message
  setTimeout(() => (notice.value = ''), 4000)
}

async function load() {
  loading.value = true
  try {
    categories.value = await api.get<Category[]>('/catalog/categories/')
    error.value = ''
  } catch (e) {
    // This screen had no catch at all. A failed load rendered the empty state,
    // which says "there are no categories" — a different and much worse claim.
    error.value = e instanceof ApiError ? e.message : 'تعذّر تحميل الأقسام.'
  } finally {
    loading.value = false
  }
}

function edit(category: Category) {
  draft.value = {
    id: category.id,
    name_ar: category.name_ar,
    name_en: category.name_en ?? '',
    color: category.color || '#1f3363',
    sort_order: category.sort_order,
  }
  editing.value = true
}

function reset() {
  draft.value = { ...EMPTY }
  editing.value = false
}

async function save() {
  const name = draft.value.name_ar.trim()
  if (!name) {
    error.value = 'اسم القسم مطلوب.'
    return
  }

  saving.value = true
  try {
    const body = {
      name_ar: name,
      name_en: draft.value.name_en.trim(),
      color: draft.value.color,
      sort_order: draft.value.sort_order,
    }
    if (draft.value.id) {
      await api.patch(`/catalog/categories/${draft.value.id}/`, body)
      flash(`تم حفظ «${name}».`)
    } else {
      await api.post('/catalog/categories/', body)
      flash(`تمت إضافة «${name}».`)
    }
    reset()
    await load()
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر حفظ القسم.'
  } finally {
    saving.value = false
  }
}

/**
 * Retire a category, or bring it back.
 *
 * The confirmation names the number of products, because that is the consequence
 * and it is invisible otherwise: switching off a category with twelve products
 * takes twelve items off the till, and the person doing it is thinking about one
 * category.
 *
 * DELETE on the endpoint deactivates rather than removes — see
 * `BranchScopedViewSet.perform_destroy` — so retiring goes through it and
 * reactivating is a PATCH.
 */
async function toggleActive(category: Category) {
  if (category.is_active) {
    const carries = category.product_count
      ? `\n\nسيختفي ${category.product_count} منتجاً من شاشة نقطة البيع.`
      : ''
    if (!window.confirm(`إيقاف القسم «${category.name_ar}»؟${carries}`)) return
  }

  try {
    if (category.is_active) {
      await api.delete(`/catalog/categories/${category.id}/`)
      flash(`تم إيقاف «${category.name_ar}» — يمكن استرجاعه من «المحذوفات».`)
    } else {
      await api.patch(`/catalog/categories/${category.id}/`, { is_active: true })
      flash(`تم تفعيل «${category.name_ar}».`)
    }
    await load()
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تغيير حالة القسم.'
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-bold text-ink">الأقسام</h1>
      <p class="mt-1 text-sm text-ink-muted">
        ترتيب الأقسام هو ترتيب شبكة نقطة البيع — وإيقاف قسم يخفي كل منتجاته من المنيو.
      </p>
    </div>

    <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>
    <UiAlert v-if="notice" tone="success">{{ notice }}</UiAlert>

    <UiInput
      v-if="categories.length > 6"
      v-model="search"
      type="search"
      label="بحث"
      placeholder="ابحث باسم القسم…"
    />

    <UiSkeleton v-if="loading" :rows="5" />

    <template v-else>
      <UiCard>
        <UiEmpty
          v-if="!categories.length"
          icon="folders"
          title="لا توجد أقسام"
          description="القسم هو أول شيء يراه الكاشير — ابدأ بقهوة ومشروبات باردة وحلويات."
        />
        <UiEmpty
          v-else-if="!shown.length"
          icon="search"
          title="لا نتائج"
          description="لا يوجد قسم بهذا الاسم."
        />
        <ul v-else class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <li
            v-for="category in shown"
            :key="category.id"
            class="rounded-lg border border-border px-4 py-3"
            :class="category.is_active ? '' : 'opacity-60'"
          >
            <div class="flex items-center justify-between gap-3">
              <div class="flex min-w-0 items-center gap-3">
                <span
                  class="h-8 w-8 shrink-0 rounded-lg"
                  :style="{ background: category.color || '#e2e8f0' }"
                  aria-hidden="true"
                />
                <div class="min-w-0">
                  <p class="truncate font-medium text-ink">{{ category.name_ar }}</p>
                  <p class="text-xs text-ink-muted">
                    {{ category.product_count }} منتج · الترتيب {{ category.sort_order }}
                  </p>
                </div>
              </div>
              <UiBadge :tone="category.is_active ? 'success' : 'neutral'">
                {{ category.is_active ? 'مفعّل' : 'موقوف' }}
              </UiBadge>
            </div>

            <div v-if="mayEdit" class="mt-3 flex items-center gap-2">
              <UiButton size="sm" variant="secondary" @click="edit(category)">تعديل</UiButton>
              <UiButton size="sm" variant="ghost" @click="toggleActive(category)">
                {{ category.is_active ? 'إيقاف' : 'تفعيل' }}
              </UiButton>
            </div>
          </li>
        </ul>
      </UiCard>

      <UiCard v-if="mayCreate || mayEdit">
        <h2 class="text-sm font-semibold text-ink">
          {{ editing ? 'تعديل قسم' : 'إضافة قسم' }}
        </h2>

        <form class="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4" @submit.prevent="save">
          <UiInput v-model="draft.name_ar" label="الاسم" required />
          <UiInput v-model="draft.name_en" label="الاسم بالإنجليزية" ltr />
          <UiInput
            v-model.number="draft.sort_order"
            label="الترتيب"
            type="number"
            hint="هو نفسه ترتيب التابات في نقطة البيع."
          />

          <label class="text-sm text-ink">
            <span class="mb-1 block font-medium">اللون</span>
            <input
              v-model="draft.color"
              type="color"
              class="h-10 w-full cursor-pointer rounded-lg border border-border bg-surface p-1"
            />
          </label>

          <div class="flex items-center gap-2 sm:col-span-2 lg:col-span-4">
            <UiButton type="submit" :loading="saving">
              {{ editing ? 'حفظ' : 'إضافة' }}
            </UiButton>
            <UiButton v-if="editing" variant="ghost" @click="reset">إلغاء</UiButton>
          </div>
        </form>

        <p class="mt-3 text-xs text-ink-faint">
          القسم يُوقَف ولا يُحذَف — فهو مسجّل على فواتير سابقة. الموقوف يظهر في «المحذوفات»
          ويمكن استرجاعه.
        </p>
      </UiCard>
    </template>
  </div>
</template>
