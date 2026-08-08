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
          meta: { permission: 'floor.view' },
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
          meta: { permission: 'kitchen.view' },
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
          meta: { permission: 'kids.view' },
        },
        {
          path: 'kids/tariffs',
          name: 'kids-tariffs',
          component: () => import('@/views/kids/KidsTariffView.vue'),
          meta: { permission: 'kids.view' },
        },
        {
          path: 'products',
          name: 'products',
          component: () => import('@/views/catalog/ProductListView.vue'),
          meta: { permission: 'catalog.view' },
        },
        {
          path: 'categories',
          name: 'categories',
          component: () => import('@/views/catalog/CategoryListView.vue'),
          meta: { permission: 'catalog.view' },
        },
        {
          path: 'recipes',
          name: 'recipes',
          component: () => import('@/views/catalog/RecipeView.vue'),
          meta: { permission: 'catalog.view' },
        },
        {
          path: 'stock',
          name: 'stock',
          component: () => import('@/views/inventory/StockLevelView.vue'),
          meta: { permission: 'inventory.view' },
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
          meta: { permission: 'inventory.view' },
        },
        {
          path: 'staff',
          name: 'staff',
          component: () => import('@/views/staff/StaffListView.vue'),
          meta: { permission: 'staff.view' },
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
          path: 'settings',
          name: 'settings',
          component: () => import('@/views/settings/SettingsView.vue'),
          meta: { permission: 'branch.view' },
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
    return auth.isAuthenticated && to.name === 'login' ? { name: 'dashboard' } : true
  }

  if (!auth.isAuthenticated) {
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
