<script setup lang="ts">
/**
 * The application shell.
 *
 * Sidebar entries are filtered by permission: a user without `inventory.view`
 * does not see a greyed-out Inventory section, they see no Inventory section at
 * all. Hiding what someone cannot use keeps the interface honest about what it
 * is for — and it is never the security boundary, which lives on the server.
 */
import { computed, ref } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'

import UiIcon from '@/components/ui/UiIcon.vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const sidebarOpen = ref(false)

interface NavItem {
  label: string
  to: string
  icon: string
  permission?: string
}

interface NavGroup {
  label: string
  items: NavItem[]
}

const groups: NavGroup[] = [
  {
    label: '',
    items: [{ label: 'لوحة التحكم', to: '/', icon: 'dashboard' }],
  },
  {
    label: 'المبيعات',
    items: [
      // First in the group, because for a cashier it is the only entry that
      // matters and the sidebar is read top-down.
      { label: 'نقطة البيع', to: '/pos', icon: 'pos', permission: 'orders.create' },
      { label: 'الطلبات', to: '/orders', icon: 'receipt', permission: 'orders.view' },
      { label: 'الورديات', to: '/shifts', icon: 'cash', permission: 'shifts.view_all' },
      { label: 'التقارير', to: '/reports', icon: 'chart', permission: 'reports.sales' },
    ],
  },
  {
    label: 'الصالة والمطبخ',
    items: [
      { label: 'مخطط الصالة', to: '/floor', icon: 'table', permission: 'floor.view' },
      { label: 'المطبخ الآن', to: '/kitchen', icon: 'kitchen', permission: 'kitchen.view' },
      { label: 'المحطات', to: '/kitchen/stations', icon: 'station', permission: 'kitchen.view' },
    ],
  },
  {
    label: 'صالة الأطفال',
    items: [
      { label: 'اللوحة المباشرة', to: '/kids', icon: 'kids', permission: 'kids.view' },
      { label: 'الجلسات', to: '/kids/sessions', icon: 'clock', permission: 'kids.view' },
      { label: 'أولياء الأمور', to: '/kids/guardians', icon: 'guardians', permission: 'kids.view' },
      { label: 'سجل الوقائع', to: '/kids/incidents', icon: 'clipboard', permission: 'kids.view' },
      { label: 'التعريفات', to: '/kids/tariffs', icon: 'ticket', permission: 'kids.view' },
    ],
  },
  {
    label: 'المنتجات والمخزون',
    items: [
      { label: 'المنتجات', to: '/products', icon: 'cup', permission: 'catalog.view' },
      { label: 'الأقسام', to: '/categories', icon: 'folders', permission: 'catalog.view' },
      { label: 'الوصفات والتكلفة', to: '/recipes', icon: 'receipt', permission: 'catalog.view' },
      { label: 'أرصدة المخزون', to: '/stock', icon: 'box', permission: 'inventory.view' },
      { label: 'حركة المخزون', to: '/stock/movements', icon: 'history', permission: 'inventory.view' },
    ],
  },
  {
    label: 'الشراء',
    items: [
      { label: 'الموردون', to: '/suppliers', icon: 'truck', permission: 'purchasing.view' },
      { label: 'الشراء والاستلام', to: '/purchasing', icon: 'inbox', permission: 'purchasing.view' },
    ],
  },
  {
    label: 'النظام',
    items: [
      { label: 'الإشعارات', to: '/notifications', icon: 'bell' },
      { label: 'الموظفون', to: '/staff', icon: 'users', permission: 'staff.view' },
      { label: 'التراخيص', to: '/licensing', icon: 'key', permission: 'licenses.view' },
      { label: 'الأجهزة', to: '/devices', icon: 'monitor', permission: 'devices.view' },
      { label: 'المزامنة', to: '/sync', icon: 'sync', permission: 'sync.view' },
      { label: 'سجل التدقيق', to: '/audit', icon: 'clipboard', permission: 'audit.view' },
      { label: 'النسخ الاحتياطي', to: '/backups', icon: 'save', permission: 'backups.manage' },
      { label: 'الطابعات', to: '/printers', icon: 'printer', permission: 'branch.manage_printers' },
      { label: 'الإعدادات', to: '/settings', icon: 'settings', permission: 'branch.view' },
    ],
  },
]

const visibleGroups = computed(() =>
  groups
    .map((group) => ({
      ...group,
      items: group.items.filter((item) => !item.permission || auth.can(item.permission)),
    }))
    .filter((group) => group.items.length > 0),
)

const initials = computed(() => (auth.me?.full_name_ar ?? '؟').trim().charAt(0))

function isActive(to: string): boolean {
  return to === '/' ? route.path === '/' : route.path.startsWith(to)
}

async function signOut() {
  await auth.logout()
  router.push('/login')
}
</script>

<template>
  <div class="min-h-screen bg-slate-50">
    <!-- Mobile scrim -->
    <div
      v-if="sidebarOpen"
      class="fixed inset-0 z-20 bg-slate-900/40 lg:hidden"
      @click="sidebarOpen = false"
    />

    <aside
      class="fixed inset-y-0 z-30 w-64 border-slate-200 bg-white transition-transform lg:translate-x-0
             start-0 border-e"
      :class="sidebarOpen ? 'translate-x-0' : 'translate-x-full lg:translate-x-0'"
    >
      <div class="flex h-16 items-center gap-3 border-b border-slate-100 px-5">
        <!--
          A monogram, not a picture of a coffee cup. Every cafe system on earth
          uses the cup; the letter is the one mark that is actually this cafe's.
        -->
        <span
          class="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-700 text-base font-bold text-gold-300"
          aria-hidden="true"
        >
          ق
        </span>
        <div>
          <p class="text-sm font-bold leading-tight text-slate-900">القيصر</p>
          <p class="text-xs text-slate-500">نظام الإدارة</p>
        </div>
      </div>

      <nav class="h-[calc(100vh-4rem)] space-y-6 overflow-y-auto px-3 py-5">
        <div v-for="group in visibleGroups" :key="group.label">
          <p
            v-if="group.label"
            class="mb-2 px-3 text-xs font-semibold uppercase tracking-wide text-slate-400"
          >
            {{ group.label }}
          </p>
          <ul class="space-y-1">
            <li v-for="item in group.items" :key="item.to">
              <RouterLink
                :to="item.to"
                class="flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition"
                :class="
                  isActive(item.to)
                    ? 'bg-brand-50 text-brand-800'
                    : 'text-slate-700 hover:bg-slate-100'
                "
                @click="sidebarOpen = false"
              >
                <UiIcon :name="item.icon" size="1.15rem" />
                {{ item.label }}
              </RouterLink>
            </li>
          </ul>
        </div>
      </nav>
    </aside>

    <div class="lg:ps-64">
      <header
        class="sticky top-0 z-10 flex h-16 items-center justify-between gap-4
               border-b border-slate-200 bg-white/90 px-4 backdrop-blur sm:px-6"
      >
        <button
          class="rounded-lg p-2 text-slate-600 hover:bg-slate-100 lg:hidden"
          aria-label="القائمة"
          @click="sidebarOpen = !sidebarOpen"
        >
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.6"
            stroke-linecap="round"
            aria-hidden="true"
          >
            <path d="M4 7h16M4 12h16M4 17h16" />
          </svg>
        </button>

        <div class="flex-1" />

        <div class="flex items-center gap-3">
          <div class="hidden text-end sm:block">
            <p class="text-sm font-medium leading-tight text-slate-900">
              {{ auth.me?.full_name_ar }}
            </p>
            <p class="text-xs text-slate-500">{{ auth.me?.roles.join('، ') }}</p>
          </div>
          <div
            class="flex h-9 w-9 items-center justify-center rounded-full bg-brand-700 text-sm font-bold text-white"
            aria-hidden="true"
          >
            {{ initials }}
          </div>
          <button
            class="rounded-lg px-3 py-2 text-sm text-slate-600 hover:bg-slate-100"
            @click="signOut"
          >
            خروج
          </button>
        </div>
      </header>

      <main class="p-4 sm:p-6">
        <RouterView />
      </main>
    </div>
  </div>
</template>
