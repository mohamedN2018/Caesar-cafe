<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { api } from '@/api/client'
import UiBadge from '@/components/ui/UiBadge.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'

interface Category {
  id: string
  name_ar: string
  color: string
  sort_order: number
  is_active: boolean
  product_count: number
}

const categories = ref<Category[]>([])
const loading = ref(true)

onMounted(async () => {
  try {
    categories.value = await api.get<Category[]>('/catalog/categories/')
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-bold text-ink">الأقسام</h1>
      <p class="mt-1 text-sm text-ink-muted">ترتيب الأقسام هو ترتيب شبكة نقطة البيع.</p>
    </div>

    <UiSkeleton v-if="loading" :rows="5" />

    <UiCard v-else>
      <UiEmpty
        v-if="!categories.length"
        icon="folders"
        title="لا توجد أقسام"
        description="القسم هو أول شيء يراه الكاشير — ابدأ بقهوة ومشروبات باردة وحلويات."
      />
      <ul v-else class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <li
          v-for="category in categories"
          :key="category.id"
          class="flex items-center justify-between gap-3 rounded-lg border border px-4 py-3"
        >
          <div class="flex items-center gap-3">
            <span
              class="h-8 w-8 shrink-0 rounded-lg"
              :style="{ background: category.color || '#e2e8f0' }"
              aria-hidden="true"
            />
            <div>
              <p class="font-medium text-ink">{{ category.name_ar }}</p>
              <p class="text-xs text-ink-muted">{{ category.product_count }} منتج</p>
            </div>
          </div>
          <UiBadge :tone="category.is_active ? 'success' : 'neutral'">
            {{ category.is_active ? 'مفعّل' : 'موقوف' }}
          </UiBadge>
        </li>
      </ul>
    </UiCard>
  </div>
</template>
