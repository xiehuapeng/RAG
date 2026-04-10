import { createRouter, createWebHistory } from 'vue-router'
import { useSessionStore } from '../store/session'

const routes = [
  {
    path: '/login',
    name: 'login',
    component: () => import('../pages/LoginPage.vue'),
    meta: { public: true },
  },
  {
    path: '/',
    component: () => import('../layouts/AppLayout.vue'),
    children: [
      { path: '', redirect: '/home' },
      { path: 'home', name: 'home', component: () => import('../pages/HomePage.vue') },
      { path: 'documents', name: 'documents', component: () => import('../pages/DocumentsPage.vue') },
      { path: 'documents/:id', name: 'document-detail', component: () => import('../pages/DocumentDetailPage.vue') },
      { path: 'chat', name: 'chat', component: () => import('../pages/ChatPage.vue') },
      { path: 'dashboard', name: 'dashboard', component: () => import('../pages/DashboardPage.vue') },
      { path: 'config', name: 'config', component: () => import('../pages/ConfigPage.vue') },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  const session = useSessionStore()
  session.restore()

  if (!to.meta.public && !session.token) {
    return '/login'
  }
  if (to.path === '/login' && session.token) {
    return '/home'
  }
  return true
})

export default router
