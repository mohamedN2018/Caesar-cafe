import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

/**
 * Every route declares the permission that gates it in `meta.permission`.
 *
 * This shapes navigation only. The server re-checks every request — a user who
 * types a URL they lack the permission for gets a 403 from the API even if the
 * guard were bypassed.
 *
 * A route the caller may not open sends them to the dashboard rather than to a
 * "forbidden" page, and the sidebar never listed it in the first place. The
 * principle across the whole product: **a user is never shown a refusal for
 * something they were never offered.** A page that exists only to say no is a
 * page that makes somebody feel watched for clicking a link the app drew.
 */
const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/auth/LoginView.vue'),
      meta: { public: true, layout: 'blank' },
    },
    {
      path: '/',
      component: () => import('@/layouts/AppLayout.vue'),
      children: [
        {
          path: '',
          name: 'dashboard',
          component: () => import('@/views/DashboardView.vue'),
        },
        {
          path: 'orders',
          name: 'orders',
          component: () => import('@/views/orders/OrderListView.vue'),
          meta: { permission: 'orders.view' },
        },
        {
          path: 'orders/:id',
          name: 'order-detail',
          component: () => import('@/views/orders/OrderDetailView.vue'),
          meta: { permission: 'orders.view' },
        },
        {
          path: 'shifts',
          name: 'shifts',
          component: () => import('@/views/shifts/ShiftListView.vue'),
          meta: { permission: 'shifts.view_all' },
        },
        {
          path: 'reports',
          name: 'reports',
          component: () => import('@/views/reports/ReportsView.vue'),
          meta: { permission: 'reports.sales' },
        },
        {
          path: 'floor',
          name: 'floor-plan',
          component: () => import('@/views/floor/FloorPlanView.vue'),
          meta: { permission: 'branch.manage_tables' },
        },
        {
          path: 'kitchen',
          name: 'kitchen-live',
          component: () => import('@/views/kitchen/KitchenLiveView.vue'),
          meta: { permission: 'kitchen.view' },
        },
        {
          path: 'kitchen/stations',
          name: 'kitchen-stations',
          component: () => import('@/views/kitchen/StationListView.vue'),
          meta: { permission: 'kitchen.manage_stations' },
        },
        {
          path: 'kids',
          name: 'kids-board',
          component: () => import('@/views/kids/KidsBoardView.vue'),
          meta: { permission: 'kids.view' },
        },
        {
          path: 'kids/guardians',
          name: 'kids-guardians',
          component: () => import('@/views/kids/KidsGuardianView.vue'),
          meta: { permission: 'kids.view' },
        },
        {
          path: 'kids/incidents',
          name: 'kids-incidents',
          component: () => import('@/views/kids/KidsIncidentView.vue'),
          meta: { permission: 'kids.view' },
        },
        {
          path: 'kids/sessions',
          name: 'kids-sessions',
          component: () => import('@/views/kids/KidsSessionListView.vue'),
          meta: { permission: 'kids.view_reports' },
        },
        {
          path: 'kids/tariffs',
          name: 'kids-tariffs',
          component: () => import('@/views/kids/KidsTariffView.vue'),
          meta: { permission: 'kids.manage_tariffs' },
        },
        {
          path: 'products',
          name: 'products',
          component: () => import('@/views/catalog/ProductListView.vue'),
          meta: { permission: 'catalog.edit' },
        },
        {
          path: 'categories',
          name: 'categories',
          component: () => import('@/views/catalog/CategoryListView.vue'),
          meta: { permission: 'catalog.edit' },
        },
        {
          path: 'recipes',
          name: 'recipes',
          component: () => import('@/views/catalog/RecipeView.vue'),
          meta: { permission: 'catalog.manage_recipes' },
        },
        {
          path: 'stock',
          name: 'stock',
          component: () => import('@/views/inventory/StockLevelView.vue'),
          meta: { permission: 'reports.inventory' },
        },
        {
          path: 'suppliers',
          name: 'suppliers',
          component: () => import('@/views/purchasing/SupplierListView.vue'),
          meta: { permission: 'purchasing.view' },
        },
        {
          path: 'purchasing',
          name: 'purchasing',
          component: () => import('@/views/purchasing/PurchaseOrderView.vue'),
          meta: { permission: 'purchasing.view' },
        },
        {
          path: 'stock/movements',
          name: 'stock-movements',
          component: () => import('@/views/inventory/StockMovementView.vue'),
          meta: { permission: 'reports.inventory' },
        },
        {
          path: 'staff',
          name: 'staff',
          component: () => import('@/views/staff/StaffListView.vue'),
          meta: { permission: 'staff.view' },
        },
        // `hr.view`, not `staff.view`. They answer different questions: staff is
        // "who works here and what may they do", hr is "when were they here". A
        // shift leader needs the second and has no business reading roles.
        {
          path: 'hr/attendance',
          name: 'hr-attendance',
          component: () => import('@/views/hr/HrAttendanceView.vue'),
          meta: { permission: 'hr.view' },
        },
        {
          path: 'hr/roster',
          name: 'hr-roster',
          component: () => import('@/views/hr/HrRosterView.vue'),
          meta: { permission: 'hr.view' },
        },
        {
          path: 'hr/timesheet',
          name: 'hr-timesheet',
          component: () => import('@/views/hr/HrTimesheetView.vue'),
          meta: { permission: 'hr.view' },
        },
        {
          path: 'licensing',
          name: 'licensing',
          component: () => import('@/views/licensing/LicenseListView.vue'),
          meta: { permission: 'licenses.view' },
        },
        {
          path: 'devices',
          name: 'devices',
          component: () => import('@/views/licensing/DeviceListView.vue'),
          meta: { permission: 'devices.view' },
        },
        {
          path: 'backups',
          name: 'backups',
          component: () => import('@/views/ops/BackupView.vue'),
          meta: { permission: 'backups.manage' },
        },
        {
          path: 'audit',
          name: 'audit',
          component: () => import('@/views/audit/AuditLogView.vue'),
          meta: { permission: 'audit.view' },
        },
        {
          path: 'sync',
          name: 'sync',
          component: () => import('@/views/sync/SyncStatusView.vue'),
          meta: { permission: 'sync.view' },
        },
        {
          // No permission: anybody signed in may ask to be told about their own
          // branch, and gating it would mean a manager added last week
          // wondering why their phone is silent.
          path: 'notifications',
          name: 'notifications',
          component: () => import('@/views/notifications/NotificationsView.vue'),
        },
        {
          path: 'settings',
          name: 'settings',
          component: () => import('@/views/settings/SettingsView.vue'),
          meta: { permission: 'branch.view' },
        },
        {
          path: 'printers',
          name: 'printers',
          component: () => import('@/views/settings/PrinterListView.vue'),
          meta: { permission: 'branch.manage_printers' },
        },
      ],
    },
    {
      /**
       * The till's own sign-in. PUBLIC, and that is the point.
       *
       * A cashier arrives with no session at all — they have a PIN and a badge
       * and a terminal the branch enrolled, which is the whole credential. The
       * page is reachable without a token because being reachable is what makes
       * it a till; what protects it is that `pos-login` refuses any PIN not
       * presented from an enrolled device.
       */
      path: '/pos/sign-in',
      name: 'pos-sign-in',
      component: () => import('@/views/pos/PosSignInView.vue'),
      meta: { public: true, layout: 'blank' },
    },
    {
      /**
       * The till, outside the admin shell on purpose.
       *
       * `PosLayout` takes the whole viewport and drops the sidebar, the
       * breadcrumb and the 14px type — none of which survive being used
       * standing up with a queue waiting. Nesting it under `AppLayout` would
       * have inherited all three.
       */
      path: '/pos',
      component: () => import('@/layouts/PosLayout.vue'),
      meta: { permission: 'orders.create' },
      children: [
        {
          path: '',
          name: 'pos',
          component: () => import('@/views/pos/PosBoardView.vue'),
        },
        {
          path: 'shift',
          name: 'pos-shift',
          component: () => import('@/views/pos/PosShiftView.vue'),
          // No `permission` of its own: the screen shows what the person may
          // do and says so plainly when they may not open a drawer. Gating the
          // route instead would send a waiter who can only READ their shift to
          // the dashboard, which is not an answer to anything.
        },
      ],
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: () => import('@/views/NotFoundView.vue'),
      meta: { public: true },
    },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()

  // Restore the session once per page load, before the first guard decision.
  if (!auth.ready) await auth.load()

  if (to.meta.public) {
    if (!auth.isAuthenticated) return true
    // Already signed in and standing on a sign-in screen: send them where that
    // screen was going to send them anyway.
    if (to.name === 'login') return { name: 'dashboard' }
    if (to.name === 'pos-sign-in') return { name: 'pos' }
    return true
  }

  if (!auth.isAuthenticated) {
    // **A cashier is never sent to the admin login.** They have no email and no
    // password — the whole design is that they do not have an account — so
    // landing them on a form asking for both is a dead end with no way out of
    // it. Anything under /pos goes to the till's own PIN screen.
    if (to.path.startsWith('/pos')) return { name: 'pos-sign-in' }
    return { name: 'login', query: { next: to.fullPath } }
  }

  const permission = to.meta.permission as string | undefined
  if (permission && !auth.can(permission)) {
    return { name: 'dashboard' }
  }

  return true
})

// The client fires this when a refresh token is rejected mid-session.
window.addEventListener('caesar:session-expired', () => {
  router.push({ name: 'login' })
})

export default router
