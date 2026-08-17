<script setup lang="ts">
/**
 * Inventory items — the things a recipe is made of.
 *
 * `/inventory/items/` had full CRUD and **no screen at all**. Stock levels and the
 * movement ledger were both readable, so the admin could watch coffee beans run
 * down and had no way to add a new ingredient, correct a reorder level, or retire
 * something the café stopped buying. Twenty-five items existed because the seed
 * wrote them.
 *
 * Two numbers here drive the purchasing suggestions, which is the only reason
 * they are worth typing carefully:
 *
 *   * `reorder_level` is when the item appears on the reorder list. Set it below
 *     what a busy weekend consumes and the list warns after the café has already
 *     run out.
 *   * `reorder_quantity` is what the suggestion asks for.
 *
 * `quantity_on_hand` and `weighted_avg_cost` are NOT editable and not on the form.
 * They are the ledger's, moved by receipts, waste, counts and sales — a field that
 * let somebody type a quantity would be stock that no movement explains, and the
 * variance report exists precisely to find that.
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
import UiTable from '@/components/ui/UiTable.vue'
import { useAuthStore } from '@/stores/auth'

interface Item {
  id: string
  code: string
  name_ar: string
  name_en: string
  item_type: string
  base_unit: string
  base_unit_code: string
  default_supplier: string | null
  minimum_stock: string
  reorder_level: string
  reorder_quantity: string
  costing_method: string
  is_active: boolean
  quantity_on_hand: string | null
  weighted_avg_cost: string | null
}

interface Unit {
  id: string
  code: string
  name_ar: string
}

interface Supplier {
  id: string
  name: string
  is_active?: boolean
}

type Draft = {
  id?: string
  code: string
  name_ar: string
  name_en: string
  item_type: string
  base_unit: string
  default_supplier: string
  minimum_stock: string
  reorder_level: string
  reorder_quantity: string
  costing_method: string
}

const TYPES = [
  { value: 'RAW', label: 'خام — يدخل في الوصفات' },
  { value: 'CONSUMABLE', label: 'مستهلك — أكواب، مناديل' },
  { value: 'PACKAGING', label: 'تعبئة' },
  { value: 'FINISHED', label: 'جاهز — يُشترى ويُباع كما هو' },
]

const COSTING = [
  { value: 'WEIGHTED_AVG', label: 'متوسط مرجّح' },
  { value: 'FIFO', label: 'الأول أولاً (FIFO)' },
]

const EMPTY: Draft = {
  code: '',
  name_ar: '',
  name_en: '',
  item_type: 'RAW',
  base_unit: '',
  default_supplier: '',
  minimum_stock: '0',
  reorder_level: '0',
  reorder_quantity: '0',
  costing_method: 'WEIGHTED_AVG',
}

const auth = useAuthStore()
const mayEdit = computed(() => auth.can('inventory.adjust'))

const items = ref<Item[]>([])
const units = ref<Unit[]>([])
const suppliers = ref<Supplier[]>([])
const loading = ref(true)
const error = ref('')
const notice = ref('')
const saving = ref(false)
const draft = ref<Draft>({ ...EMPTY })
const editing = ref(false)
const formOpen = ref(false)
const search = ref('')
const typeFilter = ref('')

const columns = [
  { key: 'name', label: 'الصنف' },
  { key: 'type', label: 'النوع' },
  { key: 'onhand', label: 'الرصيد', align: 'end' as const },
  { key: 'reorder', label: 'حد الطلب', align: 'end' as const },
  { key: 'status', label: 'الحالة' },
  { key: 'actions', label: '', align: 'end' as const },
]

const shown = computed(() => {
  const term = search.value.trim().toLowerCase()
  return items.value
    .filter((i) => !typeFilter.value || i.item_type === typeFilter.value)
    .filter(
      (i) =>
        !term ||
        i.name_ar.includes(term) ||
        i.code.toLowerCase().includes(term) ||
        (i.name_en ?? '').toLowerCase().includes(term),
    )
    .slice()
    .sort(
      (a, b) =>
        Number(b.is_active) - Number(a.is_active) || a.name_ar.localeCompare(b.name_ar, 'ar'),
    )
})

/** Below the reorder level and still active — the row worth colouring. */
function isLow(item: Item): boolean {
  const onHand = Number(item.quantity_on_hand ?? 0)
  const level = Number(item.reorder_level ?? 0)
  return item.is_active && level > 0 && onHand <= level
}

function typeLabel(value: string): string {
  return TYPES.find((t) => t.value === value)?.label.split(' — ')[0] ?? value
}

function flash(message: string) {
  notice.value = message
  setTimeout(() => (notice.value = ''), 4000)
}

async function load() {
  loading.value = true
  try {
    const [rows, us, sups] = await Promise.all([
      api.get<Item[]>('/inventory/items/'),
      api.get<Unit[]>('/inventory/units/'),
      api.optional<Supplier[]>('/suppliers/'),
    ])
    items.value = rows
    units.value = us
    suppliers.value = (sups ?? []).filter((s) => s.is_active !== false)
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تحميل أصناف المخزون.'
  } finally {
    loading.value = false
  }
}

function newItem() {
  draft.value = { ...EMPTY, base_unit: units.value[0]?.id ?? '' }
  editing.value = false
  formOpen.value = true
}

function edit(item: Item) {
  draft.value = {
    id: item.id,
    code: item.code,
    name_ar: item.name_ar,
    name_en: item.name_en ?? '',
    item_type: item.item_type,
    base_unit: item.base_unit,
    default_supplier: item.default_supplier ?? '',
    minimum_stock: item.minimum_stock,
    reorder_level: item.reorder_level,
    reorder_quantity: item.reorder_quantity,
    costing_method: item.costing_method,
  }
  editing.value = true
  formOpen.value = true
}

function closeForm() {
  draft.value = { ...EMPTY }
  editing.value = false
  formOpen.value = false
}

async function save() {
  const name = draft.value.name_ar.trim()
  const code = draft.value.code.trim()
  if (!name || !code) {
    error.value = 'الاسم والكود مطلوبان.'
    return
  }
  if (!draft.value.base_unit) {
    error.value = 'وحدة القياس مطلوبة — بدونها لا يمكن حساب تكلفة أي وصفة تستخدم الصنف.'
    return
  }

  saving.value = true
  try {
    const body = {
      code,
      name_ar: name,
      name_en: draft.value.name_en.trim(),
      item_type: draft.value.item_type,
      base_unit: draft.value.base_unit,
      default_supplier: draft.value.default_supplier || null,
      minimum_stock: draft.value.minimum_stock || '0',
      reorder_level: draft.value.reorder_level || '0',
      reorder_quantity: draft.value.reorder_quantity || '0',
      costing_method: draft.value.costing_method,
    }
    if (draft.value.id) {
      await api.patch(`/inventory/items/${draft.value.id}/`, body)
      flash(`تم حفظ «${name}».`)
    } else {
      await api.post('/inventory/items/', body)
      flash(`تمت إضافة «${name}».`)
    }
    closeForm()
    await load()
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر حفظ الصنف.'
  } finally {
    saving.value = false
  }
}

/**
 * Retire an item, or bring it back.
 *
 * The warning names the balance, because retiring an item that still has stock
 * takes real value off the reports without a movement explaining where it went —
 * the honest order is to write it off first, then retire it.
 */
async function toggleActive(item: Item) {
  if (item.is_active) {
    const onHand = Number(item.quantity_on_hand ?? 0)
    const warning = onHand
      ? `\n\nالرصيد الحالي ${onHand} ${item.base_unit_code} — الأفضل تسجيل هالك أو جرد أولاً، وإلا اختفت قيمة من التقارير بلا حركة تفسّرها.`
      : ''
    if (!window.confirm(`إيقاف «${item.name_ar}»؟${warning}`)) return
  }

  try {
    if (item.is_active) {
      await api.delete(`/inventory/items/${item.id}/`)
      flash(`تم إيقاف «${item.name_ar}» — يمكن استرجاعه من «المحذوفات».`)
    } else {
      await api.patch(`/inventory/items/${item.id}/`, { is_active: true })
      flash(`تم تفعيل «${item.name_ar}».`)
    }
    await load()
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تغيير حالة الصنف.'
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 class="text-2xl font-bold text-ink">أصناف المخزون</h1>
        <p class="mt-1 text-sm text-ink-muted">
          مكوّنات الوصفات والمستهلكات. الرصيد والتكلفة يتحرّكان بالحركات — لا يُكتبان هنا.
        </p>
      </div>
      <div class="flex w-full flex-wrap items-center gap-2 sm:w-auto">
        <select
          v-model="typeFilter"
          class="rounded-lg border border-line-strong bg-surface px-3 py-2.5 text-sm"
        >
          <option value="">كل الأنواع</option>
          <option v-for="t in TYPES" :key="t.value" :value="t.value">
            {{ t.label.split(' — ')[0] }}
          </option>
        </select>
        <input
          v-model="search"
          type="search"
          placeholder="بحث بالاسم أو الكود…"
          class="w-full rounded-lg border border-line-strong px-3 py-2.5 text-sm sm:w-56
                 focus:border-brand-700 focus:outline-none focus:ring-2 focus:ring-brand-700/30"
        />
        <UiButton v-if="mayEdit" @click="newItem">صنف جديد</UiButton>
      </div>
    </div>

    <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>
    <UiAlert v-if="notice" tone="success">{{ notice }}</UiAlert>

    <UiCard v-if="formOpen">
      <h2 class="text-sm font-semibold text-ink">
        {{ editing ? 'تعديل صنف' : 'صنف جديد' }}
      </h2>

      <form class="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3" @submit.prevent="save">
        <UiInput v-model="draft.name_ar" label="الاسم" required />
        <UiInput v-model="draft.name_en" label="الاسم بالإنجليزية" ltr />
        <UiInput v-model="draft.code" label="الكود" ltr required />

        <label class="text-sm text-ink">
          <span class="mb-1 block font-medium">النوع</span>
          <select
            v-model="draft.item_type"
            class="w-full rounded-lg border border-line-strong bg-surface px-3 py-2.5 text-sm"
          >
            <option v-for="t in TYPES" :key="t.value" :value="t.value">{{ t.label }}</option>
          </select>
        </label>

        <label class="text-sm text-ink">
          <span class="mb-1 block font-medium">وحدة القياس</span>
          <select
            v-model="draft.base_unit"
            class="w-full rounded-lg border border-line-strong bg-surface px-3 py-2.5 text-sm"
            required
          >
            <option value="" disabled>اختر الوحدة…</option>
            <option v-for="u in units" :key="u.id" :value="u.id">
              {{ u.name_ar }} ({{ u.code }})
            </option>
          </select>
          <span class="mt-1 block text-xs text-ink-faint">
            لا تُغيَّر بعد تسجيل حركات — الأرصدة القديمة مسجّلة بالوحدة القديمة.
          </span>
        </label>

        <label class="text-sm text-ink">
          <span class="mb-1 block font-medium">المورد الافتراضي</span>
          <select
            v-model="draft.default_supplier"
            class="w-full rounded-lg border border-line-strong bg-surface px-3 py-2.5 text-sm"
          >
            <option value="">بدون</option>
            <option v-for="s in suppliers" :key="s.id" :value="s.id">{{ s.name }}</option>
          </select>
        </label>

        <UiInput
          v-model="draft.reorder_level"
          label="حد الطلب"
          type="number"
          step="0.001"
          hint="عند هذا الرصيد يظهر الصنف في قائمة إعادة الطلب."
        />
        <UiInput
          v-model="draft.reorder_quantity"
          label="كمية الطلب"
          type="number"
          step="0.001"
          hint="الكمية التي يقترحها النظام عند الطلب."
        />
        <UiInput v-model="draft.minimum_stock" label="أقل رصيد" type="number" step="0.001" />

        <label class="text-sm text-ink">
          <span class="mb-1 block font-medium">طريقة التكلفة</span>
          <select
            v-model="draft.costing_method"
            class="w-full rounded-lg border border-line-strong bg-surface px-3 py-2.5 text-sm"
          >
            <option v-for="c in COSTING" :key="c.value" :value="c.value">{{ c.label }}</option>
          </select>
        </label>

        <div class="flex items-center gap-2 sm:col-span-2 lg:col-span-3">
          <UiButton type="submit" :loading="saving">{{ editing ? 'حفظ' : 'إضافة' }}</UiButton>
          <UiButton variant="ghost" @click="closeForm">إلغاء</UiButton>
        </div>
      </form>

      <p class="mt-3 text-xs text-ink-faint">
        الرصيد والتكلفة ليسا هنا لأنهما ملك دفتر الحركات — يتحرّكان بالاستلام والهالك والجرد
        والبيع. حقل يكتب رصيداً يدوياً يعني مخزوناً لا تفسّره أي حركة.
      </p>
    </UiCard>

    <UiSkeleton v-if="loading" :rows="8" />

    <UiCard v-else>
      <UiEmpty
        v-if="!items.length"
        icon="box"
        title="لا توجد أصناف مخزون"
        description="ابدأ بمكوّنات الوصفات — حبوب القهوة، الحليب، السكر."
      />
      <UiEmpty
        v-else-if="!shown.length"
        icon="search"
        title="لا نتائج"
        description="جرّب بحثاً آخر أو غيّر النوع."
      />
      <UiTable v-else :columns="columns">
        <tr
          v-for="item in shown"
          :key="item.id"
          class="hover:bg-surface-muted"
          :class="item.is_active ? '' : 'opacity-60'"
        >
          <td class="px-4 py-3">
            <p class="font-medium text-ink">{{ item.name_ar }}</p>
            <p class="font-mono text-xs text-ink-faint" dir="ltr">{{ item.code }}</p>
          </td>
          <td class="px-4 py-3 text-sm text-ink-muted">{{ typeLabel(item.item_type) }}</td>
          <td class="px-4 py-3 text-end tabular-nums">
            <span :class="isLow(item) ? 'font-semibold text-warning' : ''">
              {{ Number(item.quantity_on_hand ?? 0).toLocaleString('en') }}
            </span>
            <span class="text-xs text-ink-faint"> {{ item.base_unit_code }}</span>
          </td>
          <td class="px-4 py-3 text-end tabular-nums text-ink-muted">
            {{ Number(item.reorder_level ?? 0) || '—' }}
          </td>
          <td class="px-4 py-3">
            <UiBadge :tone="item.is_active ? 'success' : 'neutral'">
              {{ item.is_active ? 'مفعّل' : 'موقوف' }}
            </UiBadge>
            <UiBadge v-if="isLow(item)" tone="warning" class="ms-1">تحت الحد</UiBadge>
          </td>
          <td class="px-4 py-3 text-end">
            <div v-if="mayEdit" class="flex items-center justify-end gap-1">
              <UiButton size="sm" variant="secondary" @click="edit(item)">تعديل</UiButton>
              <UiButton size="sm" variant="ghost" @click="toggleActive(item)">
                {{ item.is_active ? 'إيقاف' : 'تفعيل' }}
              </UiButton>
            </div>
          </td>
        </tr>
      </UiTable>
    </UiCard>
  </div>
</template>
