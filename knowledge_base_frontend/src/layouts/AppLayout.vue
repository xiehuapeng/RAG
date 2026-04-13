<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  ChatLineRound,
  Clock,
  DataAnalysis,
  Document,
  HomeFilled,
  Monitor,
  Setting,
  SwitchButton,
} from '@element-plus/icons-vue'
import { authApi } from '../api/service'
import { useSessionStore } from '../store/session'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const nowText = ref('')

let clockTimer = null

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

const topbarKicker = computed(() => {
  const map = {
    '/home': '知识海洋',
    '/documents': '知识资产',
    '/chat': '问答航道',
    '/dashboard': '运营脉冲',
    '/config': '模型控制',
  }
  return map[activeMenu.value] || '知识控制台'
})

function updateNowText() {
  const formatter = new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
  nowText.value = formatter.format(new Date()).replace(',', '')
}

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

onMounted(() => {
  updateNowText()
  clockTimer = window.setInterval(updateNowText, 30000)
})

onBeforeUnmount(() => {
  if (clockTimer) {
    window.clearInterval(clockTimer)
    clockTimer = null
  }
})
</script>

<template>
  <div class="page-shell app-shell">
    <div class="page-noise"></div>
    <div class="page-grid-lines"></div>

    <el-container class="app-container">
      <el-aside class="app-sidebar" width="292px">
        <div class="sidebar-surface">
          <div class="brand-block">
            <div class="sidebar-tagline">
              <span class="sidebar-tag-dot"></span>
              知识海洋中枢
            </div>
            <h2 class="brand-title">数智化运营知识库</h2>
            <p class="brand-copy">文档治理、混合检索、问答生成与运营分析的一体化后台。</p>

            <div class="brand-signal-row">
              <div class="brand-signal-pill">
                <el-icon><Monitor /></el-icon>
                <span>检索在线</span>
              </div>
              <div class="brand-signal-pill">
                <el-icon><Clock /></el-icon>
                <span>{{ nowText }}</span>
              </div>
            </div>
          </div>

          <el-menu
            :default-active="activeMenu"
            class="sidebar-menu"
            router
            background-color="transparent"
            text-color="#334155"
            active-text-color="#1d4ed8"
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
            <div class="sidebar-poster-card">
              <div class="sidebar-poster-glow"></div>
              <p class="sidebar-poster-kicker">今日信号</p>
              <h3>把文档、问答与运营数据编排成一片可交互的知识海洋。</h3>
              <div class="sidebar-poster-metrics">
                <span>检索</span>
                <span>推理</span>
                <span>洞察</span>
              </div>
            </div>

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
          <div class="topbar-copy-block">
            <div class="topbar-kicker">{{ topbarKicker }}</div>
            <h1 class="topbar-title topbar-title-with-icon">
              <el-icon class="topbar-section-icon">
                <component :is="currentSectionIcon" />
              </el-icon>
              <span>{{ currentSection }}</span>
            </h1>
            <p class="topbar-subcopy">更像一块实时作战屏，而不是传统后台表单页。</p>
          </div>

          <div class="topbar-signal-board">
            <div class="topbar-status">
              <span class="status-dot"></span>
              检索与问答在线
            </div>
            <div class="topbar-micro-card">
              <span>语义路由</span>
              <strong>已启用</strong>
            </div>
          </div>
        </el-header>

        <el-main class="app-main">
          <router-view />
        </el-main>
      </el-container>
    </el-container>
  </div>
</template>
