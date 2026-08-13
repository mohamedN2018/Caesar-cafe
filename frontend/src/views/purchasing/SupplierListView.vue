<script setup lang="ts">
/**
 * Suppliers, and what we owe them.
 *
 * The balance shown here is a projection of the ledger, never a field somebody
 * types — the same discipline stock levels follow. So the statement is one tap
 * away and it carries its own reconciliation: the API replays every entry and
 * reports the drift against the stored balance. A non-zero drift is not a
 * supplier problem, it is a bug in a write path, and the person reading the
 * statement is the one who will notice first. Hiding it would mean nobody does.
 *
 * Paying is behind its own permission and its own button colour. Keeping a
 * supplier's phone number current and moving money out of the business are not
 * the same act, and one person usually does the first while only the owner
 * should do the second.
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
import { dateTime, money } from '@/lib/format'
import { useAuthStore } from '@/stores/auth'

interface Supplier {
  id: string
  name: string
  phone: string
  email: string
  address: string
  tax_number: string
  payment_terms_days: number
  notes: string
  current_balance: string
  is_active: boolean
}

interface LedgerEntry {
  id: string
  entry_type: string
  amount: string
  balance_after: string
  reference: string
  notes: string
  user_name: string | null
  occurred_at: string
}

interface Statement {
  supplier_id: string
  supplier_name: string
  current_balance: string
  drift: string
  entries: LedgerEntry[]
}

const ENTRY_LABELS: Record<string, { label: string; tone: 'danger' | 'success' | 'info' | 'neutral' }> = {
  INVOICE: { label: 'فاتورة', tone: 'danger' },
  PAYMENT: { label: 'سداد', tone: 'success' },
  RETURN: { label: 'مرتجع', tone: 'info' },
  ADJUSTMENT: { label: 'تسوية', tone: 'neutral' },
}

const auth = useAuthStore()
const mayEdit = computed(() => auth.can('purchasing.manage_suppliers'))
const mayPay = computed(() => auth.can('purchasing.pay_supplier'))

const suppliers = ref<Supplier[]>([])
const statement = ref<Statement | null>(null)
const loading = ref(true)
const error = ref('')
const saving = ref(false)

const draft = ref({ name: '', phone: '', tax_number: '', payment_terms_days: 0 })
const payment = ref({ amount: '', reference: '' })

const owed = computed(() =>
  suppliers.value.reduce((sum, s) => sum + Number(s.current_balance), 0),
)

const inDebt = computed(() =>
  suppliers.value
    .filter((s) => Number(s.current_balance) > 0)
    .sort((a, b) => Number(b.current_balance) - Number(a.current_balance)),
)

async function load() {
  loading.value = true
  try {
    suppliers.value = await api.get<Supplier[]>('/suppliers/')
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تحميل الموردين.'
  } finally {
    loading.value = false
  }
}

async function openStatement(supplier: Supplier) {
  statement.value = null
  payment.value = { amount: '', reference: '' }
  try {
    statement.value = await api.get<Statement>(`/suppliers/${supplier.id}/statement/`)
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تحميل كشف الحساب.'
  }
}

async function addSupplier() {
  if (!draft.value.name.trim()) return
  saving.value = true
  try {
    await api.post('/suppliers/', { ...draft.value, name: draft.value.name.trim() })
    draft.value = { name: '', phone: '', tax_number: '', payment_terms_days: 0 }
    await load()
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر إضافة المورد.'
  } finally {
    saving.value = false
  }
}

async function pay() {
  if (!statement.value || Number(payment.value.amount) <= 0) return
  saving.value = true
  try {
    await api.post(`/suppliers/${statement.value.supplier_id}/pay/`, {
      amount: payment.value.amount,
      reference: payment.value.reference,
    })
    const supplier = suppliers.value.find((s) => s.id === statement.value?.supplier_id)
    await load()
    if (supplier) await openStatement(supplier)
    payment.value = { amount: '', reference: '' }
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تسجيل السداد.'
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-bold text-ink">الموردون</h1>
      <p class="mt-1 text-sm text-ink-muted">
        الرصيد محسوب من كشف الحساب ولا يُكتب يدوياً — تماماً كأرصدة المخزون.
      </p>
    </div>

    <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>

    <UiSkeleton v-if="loading" :rows="5" />

    <template v-else>
      <div class="grid gap-3 sm:grid-cols-2">
        <UiCard>
          <p class="text-sm text-ink-muted">إجمالي المستحق للموردين</p>
          <p class="mt-1 text-2xl font-bold text-ink">{{ money(owed) }}</p>
        </UiCard>
        <UiCard>
          <p class="text-sm text-ink-muted">موردون لهم رصيد</p>
          <p class="mt-1 text-2xl font-bold text-ink">{{ inDebt.length }}</p>
        </UiCard>
      </div>

      <UiEmpty
        v-if="!suppliers.length"
        icon="truck"
        title="لا يوجد موردون"
        description="أضف مورداً لتتمكن من تسجيل أوامر الشراء والاستلام."
      />

      <div v-else class="grid gap-4 lg:grid-cols-2">
        <div class="space-y-2">
          <button
            v-for="supplier in suppliers"
            :key="supplier.id"
            class="w-full rounded-lg border px-4 py-3 text-start transition"
            :class="
              statement?.supplier_id === supplier.id
                ? 'border-brand-300 bg-brand-50'
                : 'border-line bg-surface hover:bg-surface-muted'
            "
            @click="openStatement(supplier)"
          >
            <div class="flex flex-wrap items-center justify-between gap-2">
              <span class="font-semibold text-ink">{{ supplier.name }}</span>
              <span
                class="font-mono text-sm font-semibold tabular-nums"
                :class="Number(supplier.current_balance) > 0 ? 'text-danger' : 'text-ink-muted'"
                dir="ltr"
              >
                {{ money(supplier.current_balance) }}
              </span>
            </div>
            <p class="mt-0.5 text-sm text-ink-muted">
              <span v-if="supplier.phone" class="font-mono" dir="ltr">{{ supplier.phone }}</span>
              <span v-if="supplier.payment_terms_days">
                · سداد خلال {{ supplier.payment_terms_days }} يوم
              </span>
            </p>
          </button>
        </div>

        <UiCard v-if="statement">
          <div class="flex flex-wrap items-start justify-between gap-2">
            <div>
              <h2 class="text-lg font-bold text-ink">{{ statement.supplier_name }}</h2>
              <p class="mt-0.5 text-sm text-ink-muted">كشف حساب</p>
            </div>
            <p class="font-mono text-xl font-bold tabular-nums text-ink" dir="ltr">
              {{ money(statement.current_balance) }}
            </p>
          </div>

          <UiAlert v-if="Number(statement.drift) !== 0" tone="error" class="mt-3">
            فرق {{ money(statement.drift) }} بين الرصيد المسجَّل ومجموع الحركات — بلّغ الدعم الفني.
            هذا خطأ برمجي وليس خطأ في حساب المورد.
          </UiAlert>

          <form
            v-if="mayPay"
            class="mt-4 flex flex-wrap items-end gap-3 rounded-lg bg-surface-muted px-4 py-3"
            @submit.prevent="pay"
          >
            <UiInput
              v-model="payment.amount"
              label="سداد"
              type="number"
              step="0.01"
              class="w-36"
              ltr
            />
            <UiInput v-model="payment.reference" label="المرجع" class="w-40" />
            <UiButton type="submit" :loading="saving" :disabled="Number(payment.amount) <= 0">
              تسجيل السداد
            </UiButton>
          </form>

          <UiEmpty
            v-if="!statement.entries.length"
            icon="receipt"
            title="لا توجد حركات"
            description="ستظهر الفواتير والسداد هنا."
          />

          <div v-else class="mt-4 overflow-x-auto">
            <table class="w-full text-sm">
              <thead class="text-xs text-ink-muted">
                <tr>
                  <th class="px-2 py-2 text-start">التاريخ</th>
                  <th class="px-2 py-2 text-start">النوع</th>
                  <th class="px-2 py-2 text-end">القيمة</th>
                  <th class="px-2 py-2 text-end">الرصيد</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-line">
                <tr v-for="entry in statement.entries" :key="entry.id">
                  <td class="px-2 py-2 text-ink-muted">{{ dateTime(entry.occurred_at) }}</td>
                  <td class="px-2 py-2">
                    <UiBadge :tone="ENTRY_LABELS[entry.entry_type]?.tone ?? 'neutral'">
                      {{ ENTRY_LABELS[entry.entry_type]?.label ?? entry.entry_type }}
                    </UiBadge>
                    <span v-if="entry.reference" class="ms-1 text-xs text-ink-faint" dir="ltr">
                      {{ entry.reference }}
                    </span>
                  </td>
                  <td
                    class="px-2 py-2 text-end font-mono tabular-nums"
                    :class="Number(entry.amount) > 0 ? 'text-danger' : 'text-success'"
                    dir="ltr"
                  >
                    {{ money(entry.amount) }}
                  </td>
                  <td class="px-2 py-2 text-end font-mono tabular-nums text-ink" dir="ltr">
                    {{ money(entry.balance_after) }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </UiCard>

        <UiCard v-else>
          <p class="text-sm text-ink-muted">اختر مورداً لعرض كشف حسابه.</p>
        </UiCard>
      </div>

      <UiCard v-if="mayEdit">
        <h2 class="text-sm font-semibold text-ink">إضافة مورد</h2>
        <form class="mt-3 grid gap-3 sm:grid-cols-4" @submit.prevent="addSupplier">
          <UiInput v-model="draft.name" label="الاسم" required />
          <UiInput v-model="draft.phone" label="الهاتف" ltr />
          <UiInput v-model="draft.tax_number" label="الرقم الضريبي" ltr />
          <UiInput
            v-model.number="draft.payment_terms_days"
            label="مهلة السداد (يوم)"
            type="number"
          />
          <div class="sm:col-span-4">
            <UiButton type="submit" :loading="saving" :disabled="!draft.name.trim()">
              إضافة
            </UiButton>
          </div>
        </form>
      </UiCard>
    </template>
  </div>
</template>
