<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  ChatLineRound,
  DataAnalysis,
  Document,
  HomeFilled,
  Setting,
  SwitchButton,
} from '@element-plus/icons-vue'
import { authApi } from '../api/service'
import { useSessionStore } from '../store/session'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
session.restore()

const menuItems = [
  { index: '/home', label: '首页', icon: HomeFilled },
  { index: '/documents', label: '文档管理', icon: Document },
  { index: '/chat', label: '智能问答', icon: ChatLineRound },
  { index: '/dashboard', label: '运营看板', icon: DataAnalysis },
  { index: '/config', label: '模型配置', icon: Setting },
]

const activeMenu = computed(() => {
  if (route.path.startsWith('/documents')) {
    return '/documents'
  }
  return route.path
})

const currentSection = computed(() => {
  const active = menuItems.find((item) => item.index === activeMenu.value)
  return active?.label || '知识工作台'
})

const currentSectionIcon = computed(() => {
  const active = menuItems.find((item) => item.index === activeMenu.value)
  return active?.icon || HomeFilled
})

async function handleLogout() {
  try {
    await authApi.logout()
  } catch {
    // 服务端登出失败时，前端仍清理本地会话，避免 token 残留。
  }
  session.clearSession()
  ElMessage.success('已退出登录')
  router.push('/login')
}
</script>

<template>
  <div class="page-shell app-shell">
    <el-container class="app-container">
      <el-aside class="app-sidebar" width="292px">
        <div class="sidebar-surface">
          <div class="brand-block">
            <h2 class="brand-title">数智化运营知识库</h2>
            <p class="brand-copy">文档治理、混合检索、问答生成与运营分析的一体化后台</p>
          </div>

          <el-menu
            :default-active="activeMenu"
            class="sidebar-menu"
            router
            background-color="transparent"
            text-color="rgba(226,232,240,0.78)"
            active-text-color="#ffffff"
          >
            <el-menu-item
              v-for="item in menuItems"
              :key="item.index"
              :index="item.index"
              class="sidebar-menu-item"
            >
              <el-icon class="sidebar-menu-icon">
                <component :is="item.icon" />
              </el-icon>
              <span>{{ item.label }}</span>
            </el-menu-item>
          </el-menu>

          <div class="sidebar-footer">
            <div class="sidebar-user-card">
              <div class="sidebar-user-label">当前登录</div>
              <div class="sidebar-user-name">
                {{ session.user?.username || '管理员' }}
              </div>
              <div class="sidebar-user-role">
                {{ session.user?.role || 'admin' }}
              </div>
            </div>

            <el-button :icon="SwitchButton" class="sidebar-logout" plain @click="handleLogout">
              退出登录
            </el-button>
          </div>
        </div>
      </el-aside>

      <el-container class="app-content-shell">
        <el-header class="app-topbar">
          <div>
            <h1 class="topbar-title topbar-title-with-icon">
              <el-icon class="topbar-section-icon">
                <component :is="currentSectionIcon" />
              </el-icon>
              <span>{{ currentSection }}</span>
            </h1>
          </div>
          <div class="topbar-status">
            <span class="status-dot"></span>
            检索与问答在线
          </div>
        </el-header>

        <el-main class="app-main">
          <router-view />
        </el-main>
      </el-container>
    </el-container>
  </div>
</template>
