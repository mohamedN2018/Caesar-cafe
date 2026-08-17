<script setup lang="ts">
/**
 * The menu itself.
 *
 * This screen could change a product's PHOTO and nothing else. Not its name, not
 * its category, not its station, not whether it sells — and there was no way to
 * add a product or retire one. The endpoints had all of it; the admin had a
 * gallery.
 *
 * Two rules the forms obey, both from the money side of a product:
 *
 *   * **Cost and margin are never entered.** They are computed from the recipe,
 *     and a field that let somebody type a cost would be a margin that disagrees
 *     with the ingredients it was made from.
 *   * **A price change is its own action**, not a field on the edit form. It
 *     needs `catalog.change_price`, writes a `PriceHistory` row and takes a
 *     reason — a receipt is a legal record of what was sold at what price, and
 *     that trail is what lets last Monday's total be explained.
 *
 * A product is retired, never deleted: it is on historical line items.
 */
import { computed, onMounted, ref } from 'vue'

import { ApiError, api } from '@/api/client'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiBadge from '@/components/ui/UiBadge.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiIcon from '@/components/ui/UiIcon.vue'
import UiInput from '@/components/ui/UiInput.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import UiTable from '@/components/ui/UiTable.vue'
import { money, percent } from '@/lib/format'
import { useAuthStore } from '@/stores/auth'

interface Variant {
  id: string
  name_ar: string
  sku: string
  price: string
  cost: string
  margin: string
  margin_percent: string
  is_default: boolean
  is_active: boolean
}

interface Product {
  id: string
  sku: string
  barcode?: string
  name_ar: string
  name_en?: string
  category: string
  category_name: string
  station: string | null
  station_name: string | null
  image: string | null
  is_active: boolean
  is_sellable: boolean
  is_tax_exempt?: boolean
  track_inventory: boolean
  sort_order?: number
  variants: Variant[]
}

interface Named {
  id: string
  name_ar: string
  is_active?: boolean
}

type ProductDraft = {
  id?: string
  name_ar: string
  name_en: string
  sku: string
  barcode: string
  category: string
  station: string
  is_sellable: boolean
  track_inventory: boolean
  is_tax_exempt: boolean
  sort_order: number
  /** Create only — a product with no variant has no price and cannot be sold. */
  price: string
}

const EMPTY_PRODUCT: ProductDraft = {
  name_ar: '',
  name_en: '',
  sku: '',
  barcode: '',
  category: '',
  station: '',
  is_sellable: true,
  track_inventory: false,
  is_tax_exempt: false,
  sort_order: 0,
  price: '',
}

const auth = useAuthStore()

const products = ref<Product[]>([])
const categories = ref<Named[]>([])
const stations = ref<Named[]>([])
const loading = ref(true)
const search = ref('')
const uploading = ref('')
const uploadError = ref('')
const notice = ref('')
const saving = ref(false)
const draft = ref<ProductDraft>({ ...EMPTY_PRODUCT })
const editing = ref(false)
const formOpen = ref(false)

/** Whoever may edit a product may put a face on it. Same permission, no new one. */
const canEdit = computed(() => auth.can('catalog.edit'))
const canCreate = computed(() => auth.can('catalog.create'))
const canPrice = computed(() => auth.can('catalog.change_price'))

function flash(message: string) {
  notice.value = message
  setTimeout(() => (notice.value = ''), 4000)
}

async function reload() {
  products.value = await api.get<Product[]>('/catalog/products/')
}

function newProduct() {
  draft.value = { ...EMPTY_PRODUCT, category: categories.value[0]?.id ?? '' }
  editing.value = false
  formOpen.value = true
}

function edit(product: Product) {
  draft.value = {
    id: product.id,
    name_ar: product.name_ar,
    name_en: product.name_en ?? '',
    sku: product.sku,
    barcode: product.barcode ?? '',
    category: product.category,
    station: product.station ?? '',
    is_sellable: product.is_sellable,
    track_inventory: product.track_inventory,
    is_tax_exempt: product.is_tax_exempt ?? false,
    sort_order: product.sort_order ?? 0,
    price: '',
  }
  editing.value = true
  formOpen.value = true
}

function closeForm() {
  draft.value = { ...EMPTY_PRODUCT }
  editing.value = false
  formOpen.value = false
}

async function save() {
  const name = draft.value.name_ar.trim()
  const sku = draft.value.sku.trim()
  if (!name || !sku) {
    uploadError.value = 'الاسم والكود مطلوبان.'
    return
  }
  if (!draft.value.category) {
    uploadError.value = 'اختر القسم — بدونه لا يظهر المنتج في أي تاب بنقطة البيع.'
    return
  }
  if (!editing.value && !Number(draft.value.price)) {
    uploadError.value = 'السعر مطلوب — منتج بلا سعر لا يمكن بيعه.'
    return
  }

  saving.value = true
  try {
    const body: Record<string, unknown> = {
      name_ar: name,
      name_en: draft.value.name_en.trim(),
      sku,
      barcode: draft.value.barcode.trim(),
      category: draft.value.category,
      // An empty select means "no station" — null, not '', which the serializer
      // would try to read as a uuid.
      station: draft.value.station || null,
      is_sellable: draft.value.is_sellable,
      track_inventory: draft.value.track_inventory,
      is_tax_exempt: draft.value.is_tax_exempt,
      sort_order: draft.value.sort_order,
    }

    if (draft.value.id) {
      await api.patch(`/catalog/products/${draft.value.id}/`, body)
      flash(`تم حفظ «${name}».`)
    } else {
      const created = await api.post<Product>('/catalog/products/', body)
      // A product with no variant has no price and cannot be rung, so the first
      // one is created with it rather than left as a step somebody forgets.
      await api.post(`/catalog/products/${created.id}/variants/`, {
        name_ar: '',
        sku: `${sku}-1`,
        price: Number(draft.value.price).toFixed(2),
        is_default: true,
      })
      flash(`تمت إضافة «${name}».`)
    }
    closeForm()
    await reload()
    uploadError.value = ''
  } catch (e) {
    uploadError.value = e instanceof ApiError ? e.message : 'تعذّر حفظ المنتج.'
  } finally {
    saving.value = false
  }
}

/** Retire a product, or bring it back. DELETE deactivates — see the viewset. */
async function toggleProduct(product: Product) {
  if (product.is_active) {
    const warning = 'سيختفي من نقطة البيع، ويظل على الفواتير السابقة.'
    if (!window.confirm(`إيقاف «${product.name_ar}»؟\n\n${warning}`)) return
  }
  try {
    if (product.is_active) {
      await api.delete(`/catalog/products/${product.id}/`)
      flash(`تم إيقاف «${product.name_ar}» — يمكن استرجاعه من «المحذوفات».`)
    } else {
      await api.patch(`/catalog/products/${product.id}/`, { is_active: true })
      flash(`تم تفعيل «${product.name_ar}».`)
    }
    await reload()
    uploadError.value = ''
  } catch (e) {
    uploadError.value = e instanceof ApiError ? e.message : 'تعذّر تغيير حالة المنتج.'
  }
}

/**
 * Change a price, through the endpoint that records it.
 *
 * `/catalog/variants/change/`, not a PATCH: it needs `catalog.change_price`,
 * writes a `PriceHistory` row and takes a reason. A price moved through the
 * ordinary update path leaves no trail, and then a receipt from last Monday
 * cannot be explained.
 */
async function changePrice(product: Product, variant: Variant) {
  const label = variant.name_ar ? `${product.name_ar} — ${variant.name_ar}` : product.name_ar
  const raw = window.prompt(`السعر الجديد لـ «${label}»`, variant.price)
  if (raw === null) return
  const next = Number(raw)
  if (!Number.isFinite(next) || next < 0) {
    uploadError.value = 'سعر غير صحيح.'
    return
  }
  const reason = window.prompt('سبب تغيير السعر (يُسجَّل في تاريخ الأسعار):') ?? ''

  try {
    await api.post('/catalog/variants/change/', {
      variant: variant.id,
      new_price: next.toFixed(2),
      reason: reason.trim(),
    })
    flash('تم تغيير السعر وتسجيله في تاريخ الأسعار.')
    await reload()
    uploadError.value = ''
  } catch (e) {
    uploadError.value = e instanceof ApiError ? e.message : 'تعذّر تغيير السعر.'
  }
}

/** A second size: a large, a double. */
async function addVariant(product: Product) {
  const name = window.prompt(`اسم الحجم الجديد لـ «${product.name_ar}» (مثل: كبير)`)
  if (!name?.trim()) return
  const raw = window.prompt('السعر:')
  if (raw === null) return
  const price = Number(raw)
  if (!Number.isFinite(price) || price < 0) {
    uploadError.value = 'سعر غير صحيح.'
    return
  }

  try {
    await api.post(`/catalog/products/${product.id}/variants/`, {
      name_ar: name.trim(),
      sku: `${product.sku}-${product.variants.length + 1}`,
      price: price.toFixed(2),
      is_default: false,
    })
    flash(`تمت إضافة «${name.trim()}».`)
    await reload()
    uploadError.value = ''
  } catch (e) {
    uploadError.value = e instanceof ApiError ? e.message : 'تعذّر إضافة الحجم.'
  }
}

/**
 * Retire one size.
 *
 * Refused for the last active one: a product with no variant has no price and
 * cannot be rung, so this would leave a button on the till that fails when
 * pressed. Retiring the PRODUCT is what somebody actually wants there.
 */
async function removeVariant(product: Product, variant: Variant) {
  if (product.variants.filter((v) => v.is_active).length <= 1) {
    uploadError.value =
      'لا يمكن إيقاف آخر حجم — المنتج بلا حجم لا سعر له. أوقف المنتج نفسه بدلاً من ذلك.'
    return
  }
  if (!window.confirm(`إيقاف الحجم «${variant.name_ar || variant.sku}»؟`)) return

  try {
    await api.delete(`/catalog/variants/${variant.id}/`)
    flash('تم إيقاف الحجم.')
    await reload()
    uploadError.value = ''
  } catch (e) {
    uploadError.value = e instanceof ApiError ? e.message : 'تعذّر إيقاف الحجم.'
  }
}

const columns = [
  { key: 'name', label: 'المنتج' },
  { key: 'category', label: 'القسم' },
  { key: 'price', label: 'السعر', align: 'end' as const },
  { key: 'cost', label: 'التكلفة', align: 'end' as const },
  { key: 'margin', label: 'الهامش', align: 'end' as const },
  { key: 'status', label: 'الحالة' },
  { key: 'actions', label: '', align: 'end' as const },
]

const filtered = computed(() => {
  const term = search.value.trim().toLowerCase()
  if (!term) return products.value
  return products.value.filter(
    (product) =>
      product.name_ar.includes(term) || product.sku.toLowerCase().includes(term),
  )
})

onMounted(async () => {
  try {
    // The category is required and the station routes the ticket, so both lists
    // load with the products rather than on opening the form — a select that
    // populates a moment after it appears is one somebody submits empty.
    const [rows, cats, stns] = await Promise.all([
      api.get<Product[]>('/catalog/products/'),
      api.get<Named[]>('/catalog/categories/'),
      api.optional<Named[]>('/kitchen/stations/'),
    ])
    products.value = rows
    categories.value = cats.filter((c) => c.is_active !== false)
    stations.value = (stns ?? []).filter((s) => s.is_active !== false)
  } catch (e) {
    uploadError.value = e instanceof ApiError ? e.message : 'تعذّر تحميل المنتجات.'
  } finally {
    loading.value = false
  }
})

/** The POS hides the variant selector when there is only one — mirror that here. */
function displayVariants(product: Product): Variant[] {
  return product.variants.length ? product.variants : []
}

/** Above this, the browser is uploading a phone camera's full-size photo. */
const MAX_IMAGE_BYTES = 4 * 1024 * 1024

/**
 * Attach a photo to a product.
 *
 * Checked here as well as on the server, not instead of it. The point of the
 * client-side check is not security — it is that a 12MB photo off a phone takes
 * a visible while to upload before the server can even start refusing it, and a
 * manager doing the menu photo by photo would sit through that every time.
 */
async function pickImage(product: Product, event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  // Reset immediately, so choosing the SAME file again after a failure still
  // fires `change` — the browser suppresses it when the value has not moved.
  input.value = ''
  if (!file) return

  if (file.size > MAX_IMAGE_BYTES) {
    uploadError.value = `الصورة كبيرة (${(file.size / 1024 / 1024).toFixed(1)} م.ب). الحد ٤ م.ب.`
    return
  }

  uploadError.value = ''
  uploading.value = product.id
  try {
    const form = new FormData()
    form.append('image', file)
    const updated = await api.upload<Product>(`/catalog/products/${product.id}/`, form)
    product.image = updated.image
  } catch (e) {
    uploadError.value = e instanceof ApiError ? e.message : 'تعذّر رفع الصورة.'
  } finally {
    uploading.value = ''
  }
}

async function clearImage(product: Product): Promise<void> {
  uploading.value = product.id
  try {
    // An empty string, not null: a multipart body has no way to carry a JSON
    // null, and Django's ImageField reads the empty value as "clear it".
    const form = new FormData()
    form.append('image', '')
    await api.upload<Product>(`/catalog/products/${product.id}/`, form)
    product.image = null
  } catch (e) {
    uploadError.value = e instanceof ApiError ? e.message : 'تعذّر حذف الصورة.'
  } finally {
    uploading.value = ''
  }
}
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 class="text-2xl font-bold text-ink">المنتجات</h1>
        <p class="mt-1 text-sm text-ink-muted">
          التكلفة والهامش محسوبان من الوصفة — لا يُدخلان يدوياً.
          <template v-if="canEdit">الصورة تظهر خلف زر المنتج في نقطة البيع.</template>
        </p>
      </div>
      <div class="flex w-full items-center gap-2 sm:w-auto">
        <input
          v-model="search"
          type="search"
          placeholder="بحث بالاسم أو الكود…"
          class="w-full rounded-lg border border-line-strong px-3 py-2.5 text-sm sm:w-64
                 focus:border-brand-700 focus:outline-none focus:ring-2 focus:ring-brand-700/30"
        />
        <UiButton v-if="canCreate" @click="newProduct">منتج جديد</UiButton>
      </div>
    </div>

    <UiAlert v-if="uploadError" tone="error">{{ uploadError }}</UiAlert>
    <UiAlert v-if="notice" tone="success">{{ notice }}</UiAlert>

    <!--
      The form, above the list rather than in a modal.

      This project has no modal component and does not need one here: every
      other management screen in the admin uses an inline panel, and a dialog
      would be a second interaction pattern for the same job.
    -->
    <UiCard v-if="formOpen">
      <h2 class="text-sm font-semibold text-ink">
        {{ editing ? 'تعديل منتج' : 'منتج جديد' }}
      </h2>

      <form class="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3" @submit.prevent="save">
        <UiInput v-model="draft.name_ar" label="الاسم" required />
        <UiInput v-model="draft.name_en" label="الاسم بالإنجليزية" ltr />
        <UiInput v-model="draft.sku" label="الكود" ltr required />

        <label class="text-sm text-ink">
          <span class="mb-1 block font-medium">القسم</span>
          <select
            v-model="draft.category"
            class="w-full rounded-lg border border-line-strong bg-surface px-3 py-2.5 text-sm"
            required
          >
            <option value="" disabled>اختر القسم…</option>
            <option v-for="c in categories" :key="c.id" :value="c.id">{{ c.name_ar }}</option>
          </select>
        </label>

        <label class="text-sm text-ink">
          <span class="mb-1 block font-medium">محطة التحضير</span>
          <select
            v-model="draft.station"
            class="w-full rounded-lg border border-line-strong bg-surface px-3 py-2.5 text-sm"
          >
            <option value="">بدون — لا تُطبع تذكرة مطبخ</option>
            <option v-for="s in stations" :key="s.id" :value="s.id">{{ s.name_ar }}</option>
          </select>
          <span class="mt-1 block text-xs text-ink-faint">
            المحطة تحدد أين تُطبع التذكرة — منتج بمحطة خطأ قهوة تُطبع على الجريل.
          </span>
        </label>

        <UiInput v-model="draft.barcode" label="الباركود" ltr />

        <!--
          Price on CREATE only. Editing one is a separate audited action — see
          `changePrice` — and a field here would route it around the history.
        -->
        <UiInput
          v-if="!editing"
          v-model="draft.price"
          label="السعر"
          type="number"
          step="0.01"
          hint="سعر الحجم الأول. تغييره بعد ذلك يُسجَّل في تاريخ الأسعار."
          required
        />
        <UiInput v-model.number="draft.sort_order" label="الترتيب" type="number" />

        <label class="flex items-center gap-2 self-end pb-2 text-sm text-ink">
          <input v-model="draft.is_sellable" type="checkbox" class="h-4 w-4 rounded" />
          يظهر في نقطة البيع
        </label>
        <label class="flex items-center gap-2 self-end pb-2 text-sm text-ink">
          <input v-model="draft.track_inventory" type="checkbox" class="h-4 w-4 rounded" />
          يخصم من المخزون
        </label>
        <label class="flex items-center gap-2 self-end pb-2 text-sm text-ink">
          <input v-model="draft.is_tax_exempt" type="checkbox" class="h-4 w-4 rounded" />
          معفى من الضريبة
        </label>

        <div class="flex items-center gap-2 sm:col-span-2 lg:col-span-3">
          <UiButton type="submit" :loading="saving">
            {{ editing ? 'حفظ' : 'إضافة' }}
          </UiButton>
          <UiButton variant="ghost" @click="closeForm">إلغاء</UiButton>
        </div>
      </form>

      <p class="mt-3 text-xs text-ink-faint">
        التكلفة والهامش غير موجودين هنا لأنهما محسوبان من الوصفة — حقل تكلفة يُكتب يدوياً
        يعني هامشاً يخالف مكوّناته.
      </p>
    </UiCard>

    <UiSkeleton v-if="loading" :rows="8" />

    <UiCard v-else>
      <UiEmpty
        v-if="!filtered.length"
        icon="cup"
        title="لا توجد منتجات"
        :description="search ? 'جرّب بحثاً آخر.' : 'ابدأ بإضافة أقسام ثم منتجات من نظام الإدارة.'"
      />
      <UiTable v-else :columns="columns">
        <template v-for="product in filtered" :key="product.id">
          <tr
            v-for="(variant, index) in displayVariants(product)"
            :key="variant.id"
            class="hover:bg-surface-muted"
          >
            <td class="px-4 py-3">
              <div class="flex items-center gap-3">
                <!--
                  The thumbnail belongs to the PRODUCT, but the table has a row
                  per variant. Drawing it once and reserving the space on the
                  rest keeps the names on one left edge — repeating it would
                  read as three different products.
                -->
                <div v-if="index === 0" class="thumb-cell">
                  <img v-if="product.image" :src="product.image" alt="" class="thumb" />
                  <span v-else class="thumb thumb-empty"><UiIcon name="cup" size="1rem" /></span>
                  <label v-if="canEdit" class="thumb-edit" :title="product.image ? 'تغيير الصورة' : 'إضافة صورة'">
                    <UiIcon :name="uploading === product.id ? 'clock' : 'camera'" size="0.8rem" />
                    <input
                      type="file"
                      accept="image/*"
                      class="sr-only"
                      :disabled="uploading === product.id"
                      @change="pickImage(product, $event)"
                    />
                  </label>
                  <button
                    v-if="canEdit && product.image"
                    type="button"
                    class="thumb-clear"
                    title="حذف الصورة"
                    :disabled="uploading === product.id"
                    @click="clearImage(product)"
                  >
                    <UiIcon name="close" size="0.7rem" />
                  </button>
                </div>
                <div v-else class="thumb-cell" aria-hidden="true" />

                <div class="min-w-0">
                  <p class="font-medium text-ink">
                    {{ product.name_ar }}
                    <span v-if="variant.name_ar" class="text-ink-muted">— {{ variant.name_ar }}</span>
                  </p>
                  <p class="font-mono text-xs text-ink-faint" dir="ltr">{{ variant.sku }}</p>
                </div>
              </div>
            </td>
            <td class="px-4 py-3 text-ink-muted">{{ product.category_name }}</td>
            <td class="px-4 py-3 text-end tabular-nums">{{ money(variant.price) }}</td>
            <td class="px-4 py-3 text-end tabular-nums text-ink-muted">
              {{ Number(variant.cost) ? money(variant.cost) : '—' }}
            </td>
            <td class="px-4 py-3 text-end tabular-nums">
              <span v-if="Number(variant.cost)" class="font-medium">
                {{ percent(variant.margin_percent) }}
              </span>
              <span v-else class="text-ink-faint" title="لا توجد وصفة أو تكلفة مسجلة">—</span>
            </td>
            <td class="px-4 py-3">
              <UiBadge :tone="product.is_active ? 'success' : 'neutral'">
                {{ product.is_active ? 'مفعّل' : 'موقوف' }}
              </UiBadge>
              <UiBadge v-if="!variant.is_active" tone="warning" class="ms-1">حجم موقوف</UiBadge>
            </td>

            <!--
              Product actions on the FIRST row only; variant actions on every row.
              A table with a row per variant would otherwise repeat "تعديل المنتج"
              three times for one product, and the third one does the same as the
              first.
            -->
            <td class="px-4 py-3 text-end">
              <div class="flex flex-wrap items-center justify-end gap-1">
                <UiButton
                  v-if="canPrice && variant.is_active"
                  size="sm"
                  variant="ghost"
                  @click="changePrice(product, variant)"
                >
                  السعر
                </UiButton>
                <UiButton
                  v-if="canEdit && variant.is_active && displayVariants(product).length > 1"
                  size="sm"
                  variant="ghost"
                  @click="removeVariant(product, variant)"
                >
                  إيقاف الحجم
                </UiButton>

                <template v-if="index === 0 && canEdit">
                  <UiButton size="sm" variant="secondary" @click="edit(product)">تعديل</UiButton>
                  <UiButton size="sm" variant="ghost" @click="addVariant(product)">+ حجم</UiButton>
                  <UiButton size="sm" variant="ghost" @click="toggleProduct(product)">
                    {{ product.is_active ? 'إيقاف' : 'تفعيل' }}
                  </UiButton>
                </template>
              </div>
            </td>
          </tr>
        </template>
      </UiTable>
    </UiCard>
  </div>
</template>

<style scoped>
.thumb-cell {
  position: relative;
  flex: none;
  width: 2.75rem;
  height: 2.75rem;
}

.thumb {
  width: 100%;
  height: 100%;
  border-radius: 0.5rem;
  object-fit: cover;
  /* `--border`, not `--line`. `line` is the TAILWIND colour name (so that
     `border-line` reads as a colour rather than a width); the underlying custom
     property it points at is `--border`. Writing `var(--line)` here resolves to
     nothing, and an invalid var() makes the whole declaration `unset` — the
     hairline came back as a heavy ring in the text colour. */
  border: 1px solid var(--border);
  background: var(--surface-sunken);
}

.thumb-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--ink-faint);
  border-style: dashed;
}

.thumb-edit,
.thumb-clear {
  position: absolute;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 1.15rem;
  height: 1.15rem;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--ink-muted);
  cursor: pointer;
}
/* Anchored to physical corners, not logical ones: the thumbnail is a picture,
   not text, so its badges should not swap sides with the writing direction. */
.thumb-edit {
  bottom: -0.3rem;
  right: -0.3rem;
}
.thumb-clear {
  top: -0.3rem;
  right: -0.3rem;
}
.thumb-edit:hover,
.thumb-clear:hover {
  color: var(--ink);
  border-color: var(--border-strong);
}
.thumb-clear:disabled {
  opacity: 0.4;
  cursor: default;
}
</style>
