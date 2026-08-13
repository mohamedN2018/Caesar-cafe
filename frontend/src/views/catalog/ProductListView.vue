<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { ApiError, api } from '@/api/client'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiBadge from '@/components/ui/UiBadge.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiIcon from '@/components/ui/UiIcon.vue'
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
  name_ar: string
  category_name: string
  station_name: string | null
  image: string | null
  is_active: boolean
  is_sellable: boolean
  track_inventory: boolean
  variants: Variant[]
}

const auth = useAuthStore()

const products = ref<Product[]>([])
const loading = ref(true)
const search = ref('')
const uploading = ref('')
const uploadError = ref('')

/** Whoever may edit a product may put a face on it. Same permission, no new one. */
const canEdit = computed(() => auth.can('catalog.edit'))

const columns = [
  { key: 'name', label: 'المنتج' },
  { key: 'category', label: 'القسم' },
  { key: 'price', label: 'السعر', align: 'end' as const },
  { key: 'cost', label: 'التكلفة', align: 'end' as const },
  { key: 'margin', label: 'الهامش', align: 'end' as const },
  { key: 'status', label: 'الحالة' },
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
    products.value = await api.get<Product[]>('/catalog/products/')
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
      <input
        v-model="search"
        type="search"
        placeholder="بحث بالاسم أو الكود…"
        class="w-full rounded-lg border border-line-strong px-3 py-2.5 text-sm sm:w-64
               focus:border-brand-700 focus:outline-none focus:ring-2 focus:ring-brand-700/30"
      />
    </div>

    <UiAlert v-if="uploadError" tone="error">{{ uploadError }}</UiAlert>

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
