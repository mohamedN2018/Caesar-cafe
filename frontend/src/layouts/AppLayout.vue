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

import logoSmall from '@/assets/brand/logo-64.png'
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
      { label: 'الحضور', to: '/hr/attendance', icon: 'clock', permission: 'hr.view' },
      { label: 'جدول الورديات', to: '/hr/roster', icon: 'clipboard', permission: 'hr.view' },
      { label: 'كشف الحضور', to: '/hr/timesheet', icon: 'chart', permission: 'hr.view' },
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
  <div class="min-h-screen bg-surface-muted">
    <!-- Mobile scrim -->
    <div
      v-if="sidebarOpen"
      class="fixed inset-0 z-20 bg-scrim lg:hidden"
      @click="sidebarOpen = false"
    />

    <!--
      Deep burgundy, not white.

      The POS chrome has been burgundy-and-gold since it was built; the admin
      sidebar being a cream panel was the one place the product stopped looking
      like itself. A dark rail also does the job a sidebar is for — it is
      permanent furniture, and giving it a different value from the page means
      the content area reads as the thing you are working on rather than as one
      more panel among several.

      A gradient rather than a flat fill: 16rem × full height is a large area of
      one value, and large flat areas are what make an interface look unfinished.
    -->
    <aside
      class="sidebar fixed inset-y-0 z-30 w-64 lg:translate-x-0 start-0"
      :class="sidebarOpen ? 'translate-x-0' : 'translate-x-full lg:translate-x-0'"
    >
      <div class="sidebar-brand flex h-16 items-center gap-3 px-5">
        <!--
          The cafe's own mark, at 64px from a 1.5MB source. The original is
          1536×1024 with baked-in padding, which would have been a megabyte and
          a half on every page load for a 36px slot — on the phone the owner
          actually uses this from.
        -->
        <img :src="logoSmall" alt="" class="h-9 w-auto" aria-hidden="true" />
        <div>
          <p class="sidebar-title text-sm font-bold leading-tight">القيصر</p>
          <p class="sidebar-subtitle text-xs">نظام الإدارة</p>
        </div>
      </div>

      <nav class="h-[calc(100vh-4rem)] space-y-6 overflow-y-auto px-3 py-5">
        <div v-for="group in visibleGroups" :key="group.label">
          <p
            v-if="group.label"
            class="sidebar-group mb-2 px-3 text-xs font-semibold uppercase tracking-wide"
          >
            {{ group.label }}
          </p>
          <ul class="space-y-1">
            <li v-for="item in group.items" :key="item.to">
              <!--
                Inactive rows are MUTED, not full ink. Every item at full
                strength means the list shouts in one voice and the active row
                has to win on colour alone; letting the rest recede is what
                makes "where am I" readable at a glance.

                The active row also carries a bar on its leading edge. In RTL
                that is the right-hand side, and `border-s` follows the writing
                direction rather than being pinned left — a hard-coded side is
                the classic RTL bug that puts the marker on the wrong edge.
              -->
              <RouterLink
                :to="item.to"
                class="nav-item flex items-center gap-3 rounded-lg border-s-[3px] px-3 py-2.5 text-sm"
                :class="isActive(item.to) ? 'nav-item--active font-semibold' : 'font-medium'"
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
        class="topbar sticky top-0 z-10 flex h-16 items-center justify-between gap-4
               border-b border-line px-4 sm:px-6"
      >
        <button
          class="rounded-lg p-2 text-ink-muted hover:bg-surface-sunken lg:hidden"
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
            <p class="text-sm font-medium leading-tight text-ink">
              {{ auth.me?.full_name_ar }}
            </p>
            <p class="text-xs text-ink-muted">{{ auth.me?.roles.join('، ') }}</p>
          </div>
          <!-- The gradient and the gold hairline, so the avatar matches the rail
               it sits beside rather than being a flat burgundy dot. -->
          <div class="avatar flex h-9 w-9 items-center justify-center rounded-full text-sm font-bold" aria-hidden="true">
            {{ initials }}
          </div>
          <button
            class="rounded-lg px-3 py-2 text-sm text-ink-muted hover:bg-surface-sunken"
            @click="signOut"
          >
            خروج
          </button>
        </div>
      </header>

      <main class="page p-4 sm:p-6">
        <RouterView />
      </main>
    </div>
  </div>
</template>

<style scoped>
/* ── the rail ──────────────────────────────────────────────────────────────
   Deep burgundy furniture. Everything on it takes its colour from the
   `--fg-on-brand-*` scale, because `--ink-muted` is a warm brown that
   disappears against brand-800. */

.sidebar {
  background-image: var(--brand-gradient);
  border-inline-end: 1px solid var(--on-brand-line);
  /* Cast onto the page, so the rail reads as in front of the content rather
     than as a differently-coloured region of the same plane. */
  box-shadow: var(--shadow-lg);
  transition: transform var(--duration-base) var(--ease-out);
}

.sidebar-brand {
  border-block-end: 1px solid var(--on-brand-line);
}
.sidebar-title {
  color: var(--fg-on-brand);
}
.sidebar-subtitle {
  color: var(--fg-on-brand-faint);
}
.sidebar-group {
  color: var(--fg-on-brand-faint);
  /* Wider than the default, because an uppercase Latin label at 12px on a dark
     ground closes up and reads as a smudge. */
  letter-spacing: 0.08em;
}

.nav-item {
  color: var(--fg-on-brand-muted);
  border-color: transparent;
  transition:
    background-color var(--duration-fast) var(--ease-out),
    color var(--duration-fast) var(--ease-out),
    border-color var(--duration-fast) var(--ease-out);
}
.nav-item:hover {
  background-color: var(--on-brand-hover);
  color: var(--fg-on-brand);
}

/* Gold marks the current page. It is the one accent the brand owns, and against
   burgundy it is unmistakable in a way a lighter red never is. The bar is on the
   LEADING edge via `border-s`, so RTL puts it on the right without a second
   rule — a hard-coded left is the classic RTL bug that marks the wrong side. */
.nav-item--active {
  background-color: var(--on-brand-active);
  color: var(--fg-on-brand);
  border-color: var(--gold-500);
}
.nav-item--active:hover {
  background-color: var(--on-brand-active);
}

/* ── the top bar ───────────────────────────────────────────────────────────
   Translucent with a blur, so content scrolling beneath it is faintly visible
   instead of vanishing under an opaque band. `--surface` at 82% rather than a
   Tailwind `/80` utility, because the opacity notation on a var()-backed colour
   depends on Tailwind rewriting it and fails silently for non-hex tokens. */
.topbar {
  background-color: rgba(255, 255, 255, 0.82);
  backdrop-filter: saturate(1.4) blur(10px);
  box-shadow: var(--shadow-xs);
}

.avatar {
  background-image: var(--brand-gradient);
  color: var(--fg-on-brand);
  box-shadow:
    0 0 0 1px var(--gold-500),
    var(--shadow-sm);
}

/* ── the page ──────────────────────────────────────────────────────────────
   Two very faint brand-tinted washes in opposite corners. Below the threshold
   where anybody would call it a gradient; above the one where a full-width
   layout reads as a single undifferentiated sheet of cream. */
.page {
  background-image: var(--page-wash);
  background-attachment: fixed;
  min-height: calc(100vh - 4rem);
}
</style>
