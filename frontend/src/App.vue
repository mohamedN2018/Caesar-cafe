<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { api, ApiError } from '@/api/client'

const { t } = useI18n()

interface Health {
  status: string
  version: string
  checks: Record<string, boolean>
}

const health = ref<Health | null>(null)
const error = ref<string | null>(null)

onMounted(async () => {
  try {
    health.value = await api.get<Health>('/system/health/')
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : String(e)
  }
})
</script>

<template>
  <main class="min-h-screen bg-slate-50 p-8 dark:bg-slate-900">
    <div class="mx-auto max-w-2xl">
      <header class="mb-8">
        <h1 class="text-3xl font-bold text-slate-900 dark:text-slate-50">
          ☕ {{ t('app.title') }}
        </h1>
        <p class="mt-1 text-slate-600 dark:text-slate-400">{{ t('app.subtitle') }}</p>
      </header>

      <section
        class="rounded-xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-700 dark:bg-slate-800"
      >
        <h2 class="mb-4 text-lg font-semibold text-slate-900 dark:text-slate-100">
          {{ t('health.title') }}
        </h2>

        <p v-if="error" class="text-red-600 dark:text-red-400">⚠ {{ error }}</p>

        <dl v-else-if="health" class="space-y-2 text-slate-700 dark:text-slate-300">
          <div class="flex justify-between">
            <dt>{{ t('health.status') }}</dt>
            <dd class="font-medium text-emerald-600 dark:text-emerald-400">
              🟢 {{ health.status }}
            </dd>
          </div>
          <div class="flex justify-between">
            <dt>{{ t('health.version') }}</dt>
            <dd class="font-mono">{{ health.version }}</dd>
          </div>
          <div class="flex justify-between">
            <dt>{{ t('health.database') }}</dt>
            <dd>{{ health.checks.database ? '✅' : '❌' }}</dd>
          </div>
        </dl>

        <p v-else class="text-slate-500">{{ t('common.loading') }}</p>
      </section>

      <p class="mt-6 text-sm text-slate-500">{{ t('app.phase') }}</p>
    </div>
  </main>
</template>
