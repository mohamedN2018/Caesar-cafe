<script setup lang="ts">
/**
 * Staff, roles and PINs.
 *
 * The first screen an owner actually needs: until somebody creates the cashier,
 * there is nobody to stand at the till and no PIN for the Desktop to
 * authenticate against.
 *
 * Three things this screen deliberately cannot do:
 *
 *   * **Show a secret.** The API never returns `pin_hash` or `password`, so
 *     neither does this. What it shows instead is *whether* a PIN is set —
 *     which is the fact a manager needs, because a cashier without one cannot
 *     log in during an outage.
 *   * **Delete a person.** Their name is on last quarter's voids and shift
 *     closures. Deactivation is the only exit, and it is reversible.
 *   * **Remove somebody's last role.** An account that can log in and do nothing
 *     is a support call that looks like a broken system rather than a
 *     configuration mistake.
 *
 * The role editor renders the permission catalogue **served by the server**, not
 * a list written here. A hand-maintained copy would drift the first time a code
 * was added, and the drift would surface as a permission nobody can grant.
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
import { dateTime } from '@/lib/format'
import ActivityPanel from '@/modules/staff/ActivityPanel.vue'
import BadgeCard from '@/modules/staff/BadgeCard.vue'
import { useAuthStore } from '@/stores/auth'

interface Assignment {
  id: string
  role: string
  role_code: string
  role_name: string
  branch: string | null
  branch_name: string | null
}

interface Staff {
  id: string
  email: string
  phone: string
  full_name_ar: string
  full_name_en?: string
  is_active: boolean
  mfa_enabled: boolean
  has_pin: boolean
  pin_set_at: string | null
  last_login: string | null
  job_title: string
  assignments: Assignment[]
}

interface Role {
  id: string
  code: string
  name_ar: string
  description_ar: string
  is_system: boolean
  permissions: string[]
  assignment_count: number
}

interface PermissionDef {
  code: string
  group: string
  label_ar: string
  description_ar: string
  sensitive: boolean
}

const auth = useAuthStore()
const mayManage = computed(() => auth.can('staff.manage_users'))
const mayResetPin = computed(() => auth.can('staff.reset_pin'))
const mayManageRoles = computed(() => auth.can('staff.manage_roles'))

const tab = ref<'people' | 'roles'>('people')
const staff = ref<Staff[]>([])
const roles = ref<Role[]>([])
const permissions = ref<PermissionDef[]>([])
const loading = ref(true)
const error = ref('')
const notice = ref('')
const busy = ref('')

/** Readable exactly once, in the response that minted them. */
interface Credentials {
  name: string
  pin: string
  badge: string
}

const draft = ref({ email: '', full_name_ar: '', phone: '', password: '', role: 'CASHIER' })
const pinFor = ref<Staff | null>(null)
const newPin = ref('')
const editingRole = ref<Role | null>(null)
const card = ref<Credentials | null>(null)
const activityFor = ref<Staff | null>(null)

const active = computed(() => staff.value.filter((s) => s.is_active))
/** Cashiers who cannot log in at the terminal during an outage. */
const withoutPin = computed(() => active.value.filter((s) => !s.has_pin))

const grouped = computed(() => {
  const out: Record<string, PermissionDef[]> = {}
  for (const permission of permissions.value) {
    ;(out[permission.group] ??= []).push(permission)
  }
  return out
})

async function load() {
  loading.value = true
  try {
    const [staffRows, roleRows, permissionRows] = await Promise.all([
      api.get<Staff[]>('/staff/'),
      api.get<Role[]>('/roles/'),
      api.get<PermissionDef[]>('/permissions/'),
    ])
    staff.value = staffRows
    roles.value = roleRows
    permissions.value = permissionRows
    error.value = ''
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر تحميل بيانات الموظفين.'
  } finally {
    loading.value = false
  }
}

function fail(e: unknown, fallback: string) {
  if (e instanceof ApiError) {
    const fields = Object.values(e.fieldErrors).flat()
    error.value = fields.length ? `${e.message} — ${fields.join('، ')}` : e.message
  } else {
    error.value = fallback
  }
}

async function addStaff() {
  if (!draft.value.email || !draft.value.full_name_ar.trim()) return
  busy.value = 'create'
  try {
    // The password is OPTIONAL and left blank is the normal case: a cashier has
    // no account to log into, only a PIN and a badge. An empty string would be
    // a password of length zero rather than none, so it is dropped entirely.
    const body: Record<string, unknown> = {
      email: draft.value.email,
      full_name_ar: draft.value.full_name_ar.trim(),
      phone: draft.value.phone,
      role: draft.value.role,
    }
    if (draft.value.password) body.password = draft.value.password

    const created = await api.post<{ credentials: Credentials }>('/staff/', body)

    // Straight into the card. This response is the only moment the PIN and the
    // badge exist in readable form, so anything that could interrupt between
    // here and showing them loses a credential that cannot be recovered — only
    // reissued.
    card.value = created.credentials

    draft.value = { email: '', full_name_ar: '', phone: '', password: '', role: 'CASHIER' }
    error.value = ''
    await load()
  } catch (e) {
    fail(e, 'تعذّر إنشاء الحساب.')
  } finally {
    busy.value = ''
  }
}

async function reissueBadge(person: Staff) {
  if (!window.confirm(`إصدار بطاقة جديدة لـ${person.full_name_ar}؟ البطاقة القديمة ستتوقف فوراً.`))
    return

  busy.value = `badge:${person.id}`
  try {
    const issued = await api.post<{ badge: string; name: string }>(`/staff/${person.id}/badge/`)
    card.value = { badge: issued.badge, name: issued.name, pin: '' }
    error.value = ''
  } catch (e) {
    fail(e, 'تعذّر إصدار البطاقة.')
  } finally {
    busy.value = ''
  }
}

/**
 * Correcting a person's details.
 *
 * `PATCH /staff/{id}/` has existed the whole time and nothing called it: a name
 * misspelled on the first day, a phone number that changed, a job title after a
 * promotion — none of it could be fixed from the admin, only worked around by
 * deactivating the person and creating them again, which loses their history.
 *
 * Deliberately NOT here: the password, the PIN and the badge. They have their own
 * actions because each one is issued and shown exactly once, and a form that
 * saved a name and silently reissued a PIN would lock somebody out at a till.
 * Roles have their own controls too — assigning one is a different decision from
 * spelling a name.
 */
const editDraft = ref<{
  id: string
  full_name_ar: string
  full_name_en: string
  phone: string
  job_title: string
} | null>(null)

function startEdit(person: Staff) {
  editDraft.value = {
    id: person.id,
    full_name_ar: person.full_name_ar,
    full_name_en: person.full_name_en ?? '',
    phone: person.phone ?? '',
    job_title: person.job_title ?? '',
  }
}

async function saveEdit() {
  const current = editDraft.value
  if (!current) return
  const name = current.full_name_ar.trim()
  if (!name) {
    error.value = 'الاسم مطلوب.'
    return
  }

  busy.value = current.id
  try {
    await api.patch(`/staff/${current.id}/`, {
      full_name_ar: name,
      full_name_en: current.full_name_en.trim(),
      phone: current.phone.trim(),
    })
    editDraft.value = null
    error.value = ''
    await load()
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'تعذّر حفظ بيانات الموظف.'
  } finally {
    busy.value = ''
  }
}

async function resetPin() {
  if (!pinFor.value || !newPin.value) return
  busy.value = 'pin'
  try {
    await api.post(`/staff/${pinFor.value.id}/reset-pin/`, { pin: newPin.value })
    notice.value = `تم تعيين رمز دخول ${pinFor.value.full_name_ar}. أبلغه به شفهياً — لا يُخزَّن ولا يُعرض مرة أخرى.`
    error.value = ''
    pinFor.value = null
    newPin.value = ''
    await load()
  } catch (e) {
    fail(e, 'تعذّر تعيين رمز الدخول.')
  } finally {
    busy.value = ''
  }
}

async function setActive(person: Staff, isActive: boolean) {
  busy.value = person.id
  try {
    await api.post(`/staff/${person.id}/set-active/`, { is_active: isActive })
    error.value = ''
    await load()
  } catch (e) {
    fail(e, 'تعذّر تغيير حالة الحساب.')
  } finally {
    busy.value = ''
  }
}

async function revoke(person: Staff, assignment: Assignment) {
  busy.value = person.id
  try {
    await api.post(`/staff/${person.id}/revoke-role/${assignment.id}/`)
    error.value = ''
    await load()
  } catch (e) {
    fail(e, 'تعذّر إزالة الدور.')
  } finally {
    busy.value = ''
  }
}

// ── roles ───────────────────────────────────────────────────────────────────

function editRole(role: Role) {
  editingRole.value = { ...role, permissions: [...role.permissions] }
}

function toggle(code: string) {
  if (!editingRole.value) return
  const held = editingRole.value.permissions
  const index = held.indexOf(code)
  if (index === -1) held.push(code)
  else held.splice(index, 1)
}

async function saveRole() {
  if (!editingRole.value) return
  busy.value = 'role'
  try {
    await api.patch(`/roles/${editingRole.value.id}/`, {
      permissions: editingRole.value.permissions,
    })
    notice.value = 'حُفظت الصلاحيات وسرت فوراً على كل من يحمل هذا الدور.'
    error.value = ''
    editingRole.value = null
    await load()
  } catch (e) {
    fail(e, 'تعذّر حفظ الدور.')
  } finally {
    busy.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div>
      <h1 class="text-2xl font-bold text-ink">الموظفون والصلاحيات</h1>
      <p class="mt-1 text-sm text-ink-muted">
        رمز الدخول لا يُعرض أبداً بعد تعيينه — يُخزَّن مُجزّأً، تماماً ككلمة المرور.
      </p>
    </div>

    <UiAlert v-if="error" tone="error">{{ error }}</UiAlert>
    <UiAlert v-else-if="notice" tone="success">{{ notice }}</UiAlert>
    <UiAlert v-if="withoutPin.length" tone="warning">
      {{ withoutPin.length }} موظف بلا رمز دخول — لن يتمكنوا من تسجيل الدخول على جهاز الكاشير.
    </UiAlert>

    <div class="flex flex-wrap gap-2">
      <button
        v-for="option in [
          { key: 'people', label: `الموظفون (${active.length})` },
          { key: 'roles', label: `الأدوار (${roles.length})` },
        ]"
        :key="option.key"
        class="rounded-lg px-3 py-2 text-sm font-medium ring-1 ring-inset transition"
        :class="
          tab === option.key
            ? 'bg-brand-50 text-brand-800 ring-brand-200'
            : 'bg-surface text-ink ring hover:bg-surface-muted'
        "
        @click="tab = option.key as typeof tab"
      >
        {{ option.label }}
      </button>
    </div>

    <UiSkeleton v-if="loading" :rows="6" />

    <!-- ── people ─────────────────────────────────────────────────────────── -->

    <template v-else-if="tab === 'people'">
      <UiEmpty
        v-if="!staff.length"
        icon="users"
        title="لا يوجد موظفون"
        description="أنشئ حساباً لأول كاشير ليبدأ العمل."
      />

      <div v-else class="grid gap-3">
        <UiCard v-for="person in staff" :key="person.id" :class="person.is_active ? '' : 'opacity-60'">
          <div class="flex flex-wrap items-start justify-between gap-4">
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <span class="text-lg font-bold text-ink">{{ person.full_name_ar }}</span>
                <UiBadge v-if="!person.is_active" tone="warning">موقوف</UiBadge>
                <UiBadge :tone="person.has_pin ? 'success' : 'danger'">
                  {{ person.has_pin ? 'له رمز دخول' : 'بلا رمز دخول' }}
                </UiBadge>
                <UiBadge v-if="person.mfa_enabled" tone="info">تحقق بخطوتين</UiBadge>
              </div>

              <p class="mt-1 font-mono text-sm text-ink-muted" dir="ltr">{{ person.email }}</p>
              <p class="mt-0.5 text-sm text-ink-muted">
                آخر دخول {{ dateTime(person.last_login) }}
              </p>

              <div class="mt-2 flex flex-wrap items-center gap-2">
                <span
                  v-for="assignment in person.assignments"
                  :key="assignment.id"
                  class="inline-flex items-center gap-1 rounded-full bg-surface-sunken px-2.5 py-0.5 text-xs font-medium text-ink"
                >
                  {{ assignment.role_name }}
                  <span class="text-ink-faint">
                    {{ assignment.branch_name ?? 'كل الفروع' }}
                  </span>
                  <button
                    v-if="mayManageRoles && person.assignments.length > 1"
                    class="text-ink-faint hover:text-danger"
                    :title="`إزالة ${assignment.role_name}`"
                    @click="revoke(person, assignment)"
                  >
                    ×
                  </button>
                </span>
              </div>
            </div>

            <div class="flex flex-wrap gap-2">
              <!--
                Correcting details. `PATCH /staff/{id}/` existed the whole time
                and nothing called it, so a misspelled name could only be worked
                around by deactivating the person and creating them again — which
                loses their history.
              -->
              <UiButton
                v-if="mayManage"
                size="sm"
                variant="secondary"
                @click="startEdit(person)"
              >
                تعديل البيانات
              </UiButton>
              <UiButton
                v-if="mayResetPin"
                size="sm"
                variant="secondary"
                @click="((pinFor = person), (newPin = ''))"
              >
                {{ person.has_pin ? 'تغيير رمز الدخول' : 'تعيين رمز دخول' }}
              </UiButton>
              <UiButton
                v-if="mayResetPin"
                size="sm"
                variant="secondary"
                :loading="busy === `badge:${person.id}`"
                @click="reissueBadge(person)"
              >
                بطاقة جديدة
              </UiButton>
              <UiButton
                size="sm"
                variant="ghost"
                @click="activityFor = activityFor?.id === person.id ? null : person"
              >
                {{ activityFor?.id === person.id ? 'إخفاء النشاط' : 'النشاط' }}
              </UiButton>
              <UiButton
                v-if="mayManage"
                size="sm"
                variant="ghost"
                :loading="busy === person.id"
                @click="setActive(person, !person.is_active)"
              >
                {{ person.is_active ? 'إيقاف' : 'تفعيل' }}
              </UiButton>
            </div>

            <form
              v-if="editDraft?.id === person.id"
              class="mt-3 grid gap-3 border-t border-border pt-3 sm:grid-cols-2 lg:grid-cols-4"
              @submit.prevent="saveEdit"
            >
              <UiInput v-model="editDraft.full_name_ar" label="الاسم" required />
              <UiInput v-model="editDraft.full_name_en" label="الاسم بالإنجليزية" ltr />
              <UiInput v-model="editDraft.phone" label="الهاتف" ltr />
              <div class="flex items-end gap-2">
                <UiButton type="submit" :loading="busy === person.id">حفظ</UiButton>
                <UiButton variant="ghost" @click="editDraft = null">إلغاء</UiButton>
              </div>
              <p class="text-xs text-ink-faint sm:col-span-2 lg:col-span-4">
                البريد والرمز والبطاقة والأدوار ليست هنا — لكل منها زرّه، لأن كل واحدة تُصدَر
                وتُعرض مرة واحدة، ونموذج يحفظ اسماً ويعيد إصدار رمز بالصمت يقفل الباب على أحد
                أمام الكاشير.
              </p>
            </form>
          </div>

          <!--
            Inline rather than on its own page: "how many voids has this person
            had" is a question asked WHILE looking at the staff list, and a
            navigation away and back loses the row you were comparing against.
          -->
          <div v-if="activityFor?.id === person.id" class="mt-4 border-t border-line pt-4">
            <ActivityPanel :user-id="person.id" />
          </div>

          <form
            v-if="pinFor?.id === person.id"
            class="mt-3 flex flex-wrap items-end gap-3 rounded-lg bg-surface-muted px-4 py-3"
            @submit.prevent="resetPin"
          >
            <UiInput
              v-model="newPin"
              label="رمز الدخول الجديد"
              type="password"
              inputmode="numeric"
              class="w-40"
              ltr
            />
            <UiButton type="submit" :loading="busy === 'pin'" :disabled="!newPin">حفظ</UiButton>
            <UiButton variant="ghost" @click="pinFor = null">إلغاء</UiButton>
            <p class="w-full text-xs text-ink-muted">
              أبلغه به شفهياً. لن يظهر مرة أخرى، ولا يُسجَّل في سجل التدقيق — المسجَّل هو أنه
              تغيّر ومن غيّره.
            </p>
          </form>
        </UiCard>
      </div>

      <UiCard v-if="mayManage">
        <h2 class="text-sm font-semibold text-ink">إضافة موظف</h2>
        <form class="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3" @submit.prevent="addStaff">
          <UiInput v-model="draft.full_name_ar" label="الاسم" required />
          <UiInput v-model="draft.email" label="البريد الإلكتروني" type="email" ltr required />
          <UiInput v-model="draft.phone" label="الهاتف" ltr />
          <!--
            Optional, and blank is the normal case. A cashier has no account:
            they get a PIN and a badge and sign in at an enrolled terminal. A
            password is only for somebody who needs the admin screens in a
            browser — a manager, an accountant, the owner.
          -->
          <UiInput
            v-model="draft.password"
            label="كلمة مرور (اختياري)"
            type="password"
            hint="للأدمن فقط، للدخول من المتصفح. اتركها فارغة للكاشير — يدخل برمزه أو ببطاقته."
          />
          <label class="block">
            <span class="mb-1.5 block text-sm font-medium text-ink">الدور</span>
            <select
              v-model="draft.role"
              class="w-full min-h-[44px] rounded-lg border border-line-strong bg-surface px-3 text-[15px]
                     focus:border-brand-700 focus:outline-none focus:ring-2 focus:ring-brand-700/30"
            >
              <option v-for="role in roles" :key="role.id" :value="role.code">
                {{ role.name_ar }}
              </option>
            </select>
          </label>
          <div class="self-end">
            <UiButton type="submit" :loading="busy === 'create'">إضافة</UiButton>
          </div>
        </form>
        <p class="mt-3 text-xs text-ink-faint">
          الدور يُسنَد على هذا الفرع. رمز الدخول خطوة تالية منفصلة.
        </p>
      </UiCard>
    </template>

    <!-- ── roles ──────────────────────────────────────────────────────────── -->

    <template v-else>
      <div class="grid gap-3">
        <UiCard v-for="role in roles" :key="role.id">
          <div class="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div class="flex flex-wrap items-center gap-2">
                <span class="text-lg font-bold text-ink">{{ role.name_ar }}</span>
                <UiBadge tone="neutral"><span dir="ltr">{{ role.code }}</span></UiBadge>
                <UiBadge v-if="role.is_system" tone="info">دور نظام</UiBadge>
              </div>
              <p class="mt-1 text-sm text-ink-muted">
                {{ role.permissions.length }} صلاحية · {{ role.assignment_count }} موظف
              </p>
            </div>
            <UiButton v-if="mayManageRoles" size="sm" variant="secondary" @click="editRole(role)">
              تعديل الصلاحيات
            </UiButton>
          </div>
        </UiCard>
      </div>

      <UiCard v-if="editingRole">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 class="text-lg font-bold text-ink">
              صلاحيات {{ editingRole.name_ar }}
            </h2>
            <p class="mt-0.5 text-sm text-ink-muted">
              {{ editingRole.permissions.length }} صلاحية مختارة — تسري فوراً على كل من يحمل الدور.
            </p>
          </div>
          <div class="flex gap-2">
            <UiButton :loading="busy === 'role'" @click="saveRole">حفظ</UiButton>
            <UiButton variant="ghost" @click="editingRole = null">إلغاء</UiButton>
          </div>
        </div>

        <div class="mt-4 space-y-4">
          <div v-for="(defs, group) in grouped" :key="group">
            <h3 class="text-sm font-semibold text-ink">{{ group }}</h3>
            <div class="mt-2 grid gap-1.5 sm:grid-cols-2">
              <label
                v-for="def in defs"
                :key="def.code"
                class="flex items-start gap-2 rounded-lg px-2 py-1.5 text-sm"
                :class="def.sensitive ? 'bg-warning-bg' : 'hover:bg-surface-muted'"
              >
                <input
                  type="checkbox"
                  class="mt-0.5 h-4 w-4 rounded"
                  :checked="editingRole.permissions.includes(def.code)"
                  @change="toggle(def.code)"
                />
                <span>
                  <span class="text-ink">{{ def.label_ar }}</span>
                  <span v-if="def.sensitive" class="ms-1 text-xs font-medium text-warning">
                    حسّاسة
                  </span>
                  <span class="block font-mono text-[11px] text-ink-faint" dir="ltr">
                    {{ def.code }}
                  </span>
                </span>
              </label>
            </div>
          </div>
        </div>

        <p class="mt-4 text-xs text-ink-faint">
          القائمة تأتي من الخادم لا من المتصفح — نسخة مكتوبة يدوياً هنا كانت ستتأخر عن أي صلاحية
          جديدة، والنتيجة صلاحية لا يستطيع أحد منحها.
        </p>
      </UiCard>
    </template>

    <BadgeCard
      v-if="card"
      :name="card.name"
      :badge="card.badge"
      :pin="card.pin || undefined"
      @close="card = null"
    />
  </div>
</template>
