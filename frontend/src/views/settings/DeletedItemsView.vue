<script setup lang="ts">
/**
 * The recycle bin.
 *
 * Deleting in this product means deactivating — `is_active = False` — because a
 * product that has ever been sold must not be removable: deleting it would
 * orphan historical line items and silently rewrite last quarter's reports.
 *
 * That rule is right, and it left a hole this screen closes. **Deactivated rows
 * became invisible.** A category switched off by accident disappeared from every
 * screen, still sitting in the database, with no way back that did not involve a
 * shell. "Deleted" behaved like deleted while promising it did not.
 *
 * So nothing here is new storage — it is a view of what was already there,
 * across fourteen models, each row restorable.
 */
import { computed, onMounted, ref } from 'vue'

import { ApiError, api } from '@/api/client'
import UiAlert from '@/components/ui/UiAlert.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiCard from '@/components/ui/UiCard.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiSkeleton from '@/components/ui/UiSkeleton.vue'
import UiTable from '@/components/ui/UiTable.vue'
import { dateTime } from '@/lib/format'

interface DeletedItem {
  id: string
  kind: string
  kind_label: string
  title: string
  deactivated_at: string | null
}

const items = ref<DeletedItem[]>([])
const loading = ref(true)
const error = ref('')
const notice = ref('')
const kind = ref('')
/** Which row is mid-restore, so only its own button shows the state. */
const restoring = ref('')

const columns = [
  { key: 'kind', label: 'النوع' },
  { key: 'title', label: 'العنصر' },
  { key: 'when', label: 'تاريخ الحذف' },
  { key: 'action', label: '', align: 'end' as const },
]

/** The kinds actually present, so the filter never offers an empty category. */
const kinds = computed(() => {
  const seen = new Map<string, string>()
  for (const item of items.value) seen.set(item.kind, item.kind_label)
  return [...seen.entries()].map(([value, label]) => ({ value, label }))
})

const visible = computed(() =>
  kind.value ? items.value.filter((item) => item.kind === kind.value) : items.value,
)

async function load() {
  loading.value = true
  try {
    const payload = await api.get<{ items: DeletedItem[] }>('/system/deleted/')
    items.value = payload.items ?? []
    error.value = ''
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : 'تعذّر تحميل المحذوفات.'
  } finally {
    loading.value = false
  }
}

/**
 * Put one row back.
 *
 * No confirmation. Restoring is the *undo*: it is what somebody came here to do,
 * and asking "are you sure you want to undo?" is a dialog that only ever gets
 * dismissed. It is also itself undoable — the delete that put the row here is
 * still available on its own screen.
 */
async function restore(item: DeletedItem) {
  restoring.value = item.id
  error.value = ''
  try {
    await api.post('/system/deleted/restore/', { kind: item.kind, id: item.id })
    items.value = items.value.filter((row) => row.id !== item.id)
    notice.value = `تم استرجاع «${item.title}».`
    setTimeout(() => (notice.value = ''), 4000)
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : 'تعذّر الاسترجاع.'
  } finally {
    restoring.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-bold text-ink">المحذوفات</h1>
      <p class="mt-1 text-sm text-ink-muted">
        الحذف في هذا النظام تعطيل وليس إزالة — لأن صنفاً بيع مرة لا يجوز أن يختفي ويترك
        فواتير قديمة بلا اسم. كل ما هنا يمكن استرجاعه.
      </p>
    </div>

    <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>
    <UiAlert v-if="notice" tone="success">{{ notice }}</UiAlert>

    <!-- Only worth showing when there is more than one kind to choose between. -->
    <nav v-if="kinds.length > 1" class="flex flex-wrap gap-2">
      <button
        type="button"
        class="chip"
        :class="{ 'chip-on': kind === '' }"
        @click="kind = ''"
      >
        الكل ({{ items.length }})
      </button>
      <button
        v-for="entry in kinds"
        :key="entry.value"
        type="button"
        class="chip"
        :class="{ 'chip-on': kind === entry.value }"
        @click="kind = entry.value"
      >
        {{ entry.label }}
      </button>
    </nav>

    <UiCard>
      <div v-if="loading" class="space-y-3 p-4">
        <UiSkeleton v-for="n in 4" :key="n" class="h-10" />
      </div>

      <UiEmpty
        v-else-if="!items.length"
        title="لا يوجد شيء محذوف"
        description="كل ما حُذف من النظام يظهر هنا مع إمكانية استرجاعه."
      />

      <UiTable v-else :columns="columns">
        <tr v-for="item in visible" :key="item.id" class="hover:bg-surface-muted">
          <td class="px-4 py-3 text-sm text-ink-muted">{{ item.kind_label }}</td>
          <td class="px-4 py-3 font-medium text-ink">{{ item.title }}</td>
          <td class="whitespace-nowrap px-4 py-3 text-sm text-ink-muted">
            <!--
              Rows deactivated before the timestamp existed have none. A dash is
              honest; a fabricated date on a screen about recovering things would
              not be.
            -->
            {{ item.deactivated_at ? dateTime(item.deactivated_at) : '—' }}
          </td>
          <td class="px-4 py-3 text-end">
            <UiButton
              size="sm"
              variant="ghost"
              :loading="restoring === item.id"
              @click="restore(item)"
            >
              استرجاع
            </UiButton>
          </td>
        </tr>
      </UiTable>
    </UiCard>
  </div>
</template>

<style scoped>
.chip {
  padding: 0.35rem 0.85rem;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
  font-size: 0.85rem;
  color: var(--ink-muted);
}
.chip-on {
  background: var(--brand-700);
  border-color: var(--brand-700);
  color: #fff;
}
</style>
