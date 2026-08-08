import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

/**
 * Every route declares the permission that gates it in `meta.permission`.
 *
 * This shapes navigation only. The server re-checks every request — a user who
 * types a URL they lack the permission for gets a 403 from the API even if the
 * guard were bypassed.
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
          path: 'kids',
          name: 'kids-board',
          component: () => import('@/views/kids/KidsBoardView.vue'),
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
          path: 'stock',
          name: 'stock',
          component: () => import('@/views/inventory/StockLevelView.vue'),
          meta: { permission: 'inventory.view' },
        },
        {
          path: 'stock/movements',
          name: 'stock-movements',
          component: () => import('@/views/inventory/StockMovementView.vue'),
          meta: { permission: 'inventory.view' },
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
