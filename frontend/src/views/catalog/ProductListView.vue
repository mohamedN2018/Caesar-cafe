<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { api } from '@/api/client'
import UiBadge from '@/components/ui/UiBadge.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import UiTable from '@/components/ui/UiTable.vue'
import { money, percent } from '@/lib/format'

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
  is_active: boolean
  is_sellable: boolean
  track_inventory: boolean
  variants: Variant[]
}

const products = ref<Product[]>([])
const loading = ref(true)
const search = ref('')

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
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 class="text-2xl font-bold text-slate-900">المنتجات</h1>
        <p class="mt-1 text-sm text-slate-500">
          التكلفة والهامش محسوبان من الوصفة — لا يُدخلان يدوياً.
        </p>
      </div>
      <input
        v-model="search"
        type="search"
        placeholder="بحث بالاسم أو الكود…"
        class="w-full rounded-lg border border-slate-300 px-3 py-2.5 text-sm sm:w-64
               focus:border-brand-700 focus:outline-none focus:ring-2 focus:ring-brand-700/30"
      />
    </div>

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
            v-for="variant in displayVariants(product)"
            :key="variant.id"
            class="hover:bg-slate-50"
          >
            <td class="px-4 py-3">
              <p class="font-medium text-slate-900">
                {{ product.name_ar }}
                <span v-if="variant.name_ar" class="text-slate-500">— {{ variant.name_ar }}</span>
              </p>
              <p class="font-mono text-xs text-slate-400" dir="ltr">{{ variant.sku }}</p>
            </td>
            <td class="px-4 py-3 text-slate-600">{{ product.category_name }}</td>
            <td class="px-4 py-3 text-end tabular-nums">{{ money(variant.price) }}</td>
            <td class="px-4 py-3 text-end tabular-nums text-slate-600">
              {{ Number(variant.cost) ? money(variant.cost) : '—' }}
            </td>
            <td class="px-4 py-3 text-end tabular-nums">
              <span v-if="Number(variant.cost)" class="font-medium">
                {{ percent(variant.margin_percent) }}
              </span>
              <span v-else class="text-slate-400" title="لا توجد وصفة أو تكلفة مسجلة">—</span>
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
