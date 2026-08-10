<script setup lang="ts">
import { RouterLink } from 'vue-router'

import UiIcon from '@/components/ui/UiIcon.vue'
</script>

<template>
  <div class="flex min-h-screen flex-col items-center justify-center gap-4 bg-surface-muted px-4 text-center">
    <div
      class="flex h-14 w-14 items-center justify-center rounded-full bg-surface-sunken text-ink-faint"
    >
      <UiIcon name="lost" size="1.75rem" />
    </div>
    <h1 class="text-2xl font-bold text-ink">الصفحة غير موجودة</h1>
    <p class="text-sm text-ink-muted">الرابط الذي فتحته غير صحيح أو تم نقله.</p>
    <RouterLink to="/" class="rounded-lg bg-brand-700 px-5 py-2.5 font-semibold text-white">
      العودة للوحة التحكم
    </RouterLink>
  </div>
</template>
