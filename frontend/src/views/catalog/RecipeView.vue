<script setup lang="ts">
/**
 * Recipes — what a sold item takes off the shelf, and what that costs.
 *
 * This screen is the bridge between the catalog and the ledger. Without a
 * recipe, selling a cappuccino deducts nothing, inventory becomes a list
 * somebody updates by hand, and then stops trusting.
 *
 * The costing panel is the reason it earns a screen rather than a fixture:
 *
 *   * **The cost is computed, never typed.** It comes from today's
 *     weighted-average ingredient costs, and a goods receipt re-costs every
 *     recipe using that ingredient automatically. A margin entered by hand is a
 *     margin that is wrong by the end of the month.
 *   * **Ingredients with no cost are named, not averaged away.** An item that
 *     has never been received contributes zero, and a margin that looks
 *     excellent for that reason is worse than no margin at all — so the panel
 *     says which ones, in the words "the figure below is understated".
 *   * **Waste percent is a real number, not padding.** Beans lost in grinding,
 *     milk left in the pitcher. Leaving it at zero is what makes theoretical
 *     stock drift from counted stock until staff stop believing either.
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
import { money, percent, quantity as fmtQuantity } from '@/lib/format'
import { useAuthStore } from '@/stores/auth'

interface RecipeLine {
  id?: string
  item: string
  item_name?: string
  unit: string
  unit_code?: string
  quantity: string
  waste_percent: string
  effective_quantity?: string
  is_optional: boolean
}

interface Recipe {
  id: string
  variant: string
  variant_name: string
  yield_quantity: string
  notes: string
  is_active: boolean
  lines: RecipeLine[]
}

interface CostLine {
  item_code: string
  item_name: string
  quantity: string
  unit_code: string
  unit_cost: string
  line_cost: string
}

interface Cost {
  total: string
  lines: CostLine[]
  missing_costs: string[]
  price: string | null
  margin: string | null
  margin_percent: string | null
}

interface Item {
  id: string
  code: string
  name_ar: string
  base_unit: string
  base_unit_code: string
}

const auth = useAuthStore()
const mayEdit = computed(() => auth.can('catalog.manage_recipes'))

const recipes = ref<Recipe[]>([])
const items = ref<Item[]>([])
const selected = ref<Recipe | null>(null)
const cost = ref<Cost | null>(null)
const loading = ref(true)
const error = ref('')
const saving = ref(false)

/** Recipes whose cost the server could not fully compute. */
const thin = computed(() => recipes.value.filter((r) => !r.lines.length))

async function load() {
  loading.value = true
  try {
    const [recipeRows, itemRows] = await Promise.all([
      api.get<Recipe[]>('/recipes/'),
      api.get<Item[]>('/inventory/items/'),
    ])
    recipes.value = recipeRows
    items.value = itemRows
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تحميل الوصفات.'
  } finally {
    loading.value = false
  }
}

async function pick(recipe: Recipe) {
  // A copy: editing the list in place would leave a half-typed quantity showing
  // in the sidebar as though it had been saved.
  selected.value = JSON.parse(JSON.stringify(recipe))
  cost.value = null
  try {
    cost.value = await api.get<Cost>(`/recipes/${recipe.id}/cost/`)
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر حساب التكلفة.'
  }
}

function addLine() {
  if (!selected.value || !items.value.length) return
  const item = items.value[0]
  selected.value.lines.push({
    item: item.id,
    unit: item.base_unit,
    quantity: '',
    waste_percent: '0',
    is_optional: false,
  })
}

function removeLine(index: number) {
  selected.value?.lines.splice(index, 1)
}

async function save() {
  if (!selected.value) return
  saving.value = true
  try {
    await api.patch(`/recipes/${selected.value.id}/`, {
      yield_quantity: selected.value.yield_quantity,
      notes: selected.value.notes,
      is_active: selected.value.is_active,
      lines: selected.value.lines.map((line) => ({
        item: line.item,
        unit: line.unit,
        quantity: line.quantity,
        waste_percent: line.waste_percent,
        is_optional: line.is_optional,
      })),
    })
    await load()
    const refreshed = recipes.value.find((r) => r.id === selected.value?.id)
    if (refreshed) await pick(refreshed)
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر حفظ الوصفة.'
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-bold text-ink">الوصفات والتكلفة</h1>
      <p class="mt-1 text-sm text-ink-muted">
        الوصفة هي ما يخصم من المخزون عند البيع، وهي مصدر التكلفة — التكلفة تُحسب ولا تُكتب.
      </p>
    </div>

    <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>
    <UiAlert v-if="thin.length" tone="warning">
      {{ thin.length }} وصفة بلا مكوّنات — بيع أصنافها لا يخصم شيئاً من المخزون.
    </UiAlert>

    <UiSkeleton v-if="loading" :rows="6" />

    <template v-else>
      <UiEmpty
        v-if="!recipes.length"
        icon="receipt"
        title="لا توجد وصفات"
        description="بدون وصفة، يبقى المخزون قائمة تُحدَّث يدوياً."
      />

      <div v-else class="grid gap-4 lg:grid-cols-3">
        <div class="space-y-2">
          <button
            v-for="recipe in recipes"
            :key="recipe.id"
            class="w-full rounded-lg border px-4 py-3 text-start transition"
            :class="
              selected?.id === recipe.id
                ? 'border-brand-300 bg-brand-50'
                : 'border-line bg-surface hover:bg-surface-muted'
            "
            @click="pick(recipe)"
          >
            <div class="flex flex-wrap items-center gap-2">
              <span class="font-semibold text-ink">{{ recipe.variant_name }}</span>
              <UiBadge v-if="!recipe.is_active" tone="warning">موقوفة</UiBadge>
              <UiBadge v-if="!recipe.lines.length" tone="danger">بلا مكوّنات</UiBadge>
            </div>
            <p class="mt-0.5 text-sm text-ink-muted">{{ recipe.lines.length }} مكوّن</p>
          </button>
        </div>

        <div class="space-y-4 lg:col-span-2">
          <UiCard v-if="selected">
            <div class="flex flex-wrap items-start justify-between gap-3">
              <h2 class="text-lg font-bold text-ink">{{ selected.variant_name }}</h2>
              <UiButton v-if="mayEdit" size="sm" :loading="saving" @click="save">حفظ</UiButton>
            </div>

            <div class="mt-3 grid gap-3 sm:grid-cols-2">
              <UiInput
                v-model="selected.yield_quantity"
                label="عدد الحصص"
                type="number"
                step="0.001"
                hint="دفعة من ١٠ تقسم مكوّناتها على ١٠ لكل بيعة."
                :disabled="!mayEdit"
                ltr
              />
              <UiInput v-model="selected.notes" label="ملاحظات" :disabled="!mayEdit" />
            </div>

            <div class="mt-4 space-y-2">
              <div
                v-for="(line, index) in selected.lines"
                :key="index"
                class="grid gap-3 rounded-lg bg-surface-muted px-3 py-2 sm:grid-cols-4"
              >
                <label class="block sm:col-span-2">
                  <span class="mb-1.5 block text-xs font-medium text-ink-muted">المكوّن</span>
                  <select
                    v-model="line.item"
                    :disabled="!mayEdit"
                    class="w-full min-h-[44px] rounded-lg border border-line-strong bg-surface px-3 text-[15px]"
                  >
                    <option v-for="item in items" :key="item.id" :value="item.id">
                      {{ item.name_ar }} ({{ item.code }})
                    </option>
                  </select>
                </label>
                <UiInput
                  v-model="line.quantity"
                  label="الكمية"
                  type="number"
                  step="0.001"
                  :disabled="!mayEdit"
                  ltr
                />
                <UiInput
                  v-model="line.waste_percent"
                  label="الفاقد %"
                  type="number"
                  step="0.01"
                  :disabled="!mayEdit"
                  ltr
                />
                <div v-if="mayEdit" class="sm:col-span-4">
                  <UiButton size="sm" variant="ghost" @click="removeLine(index)">حذف</UiButton>
                </div>
              </div>

              <UiButton v-if="mayEdit" size="sm" variant="secondary" @click="addLine">
                إضافة مكوّن
              </UiButton>
            </div>

            <p class="mt-3 text-xs text-ink-faint">
              الفاقد رقم حقيقي: بن يضيع في الطحن، لبن يبقى في الإبريق. تركه صفراً يجعل المخزون
              النظري يبتعد عن المعدود حتى يفقد الجميع الثقة في الاثنين.
            </p>
          </UiCard>

          <UiCard v-if="cost">
            <h3 class="text-sm font-semibold text-ink">التكلفة اليوم</h3>

            <UiAlert v-if="cost.missing_costs.length" tone="warning" class="mt-2">
              أصناف بلا تكلفة مسجَّلة ({{ cost.missing_costs.join('، ') }}) — الرقم أدناه أقل من
              الحقيقة.
            </UiAlert>

            <div class="mt-3 grid gap-3 sm:grid-cols-3">
              <div class="rounded-lg bg-surface-muted px-4 py-3">
                <p class="text-xs text-ink-muted">التكلفة</p>
                <p class="mt-1 font-mono text-xl font-bold tabular-nums text-ink" dir="ltr">
                  {{ money(cost.total) }}
                </p>
              </div>
              <div class="rounded-lg bg-surface-muted px-4 py-3">
                <p class="text-xs text-ink-muted">سعر البيع</p>
                <p class="mt-1 font-mono text-xl font-bold tabular-nums text-ink" dir="ltr">
                  {{ money(cost.price) }}
                </p>
              </div>
              <div class="rounded-lg bg-surface-muted px-4 py-3">
                <p class="text-xs text-ink-muted">هامش الربح</p>
                <p
                  class="mt-1 font-mono text-xl font-bold tabular-nums"
                  :class="Number(cost.margin) > 0 ? 'text-success' : 'text-danger'"
                  dir="ltr"
                >
                  {{ money(cost.margin) }}
                </p>
                <p v-if="cost.margin_percent" class="mt-0.5 text-xs text-ink-muted">
                  {{ percent(cost.margin_percent) }}
                </p>
              </div>
            </div>

            <div v-if="cost.lines.length" class="mt-4 overflow-x-auto">
              <table class="w-full text-sm">
                <thead class="text-xs text-ink-muted">
                  <tr>
                    <th class="px-2 py-2 text-start">المكوّن</th>
                    <th class="px-2 py-2 text-end">الكمية</th>
                    <th class="px-2 py-2 text-end">تكلفة الوحدة</th>
                    <th class="px-2 py-2 text-end">التكلفة</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-line">
                  <tr v-for="line in cost.lines" :key="line.item_code">
                    <td class="px-2 py-2 text-ink">{{ line.item_name }}</td>
                    <td class="px-2 py-2 text-end font-mono tabular-nums text-ink-muted" dir="ltr">
                      {{ fmtQuantity(line.quantity, line.unit_code) }}
                    </td>
                    <td class="px-2 py-2 text-end font-mono tabular-nums text-ink-muted" dir="ltr">
                      {{ line.unit_cost }}
                    </td>
                    <td class="px-2 py-2 text-end font-mono tabular-nums text-ink" dir="ltr">
                      {{ line.line_cost }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <p class="mt-3 text-xs text-ink-faint">
              تُحدَّث تلقائياً مع كل سند استلام — سعر بن جديد يغيّر هامش كل مشروب يحتوي على بن.
            </p>
          </UiCard>

          <UiCard v-if="!selected">
            <p class="text-sm text-ink-muted">اختر وصفة لعرض مكوّناتها وتكلفتها.</p>
          </UiCard>
        </div>
      </div>
    </template>
  </div>
</template>
