import { createRouter, createWebHistory } from 'vue-router'

/**
 * Routes are added per phase. Each will declare the permission code that gates
 * it in `meta.permission`, checked by a navigation guard in Phase 2.
 */
const router = createRouter({
  history: createWebHistory(),
  routes: [{ path: '/', name: 'dashboard', component: () => import('@/App.vue') }],
})

export default router
