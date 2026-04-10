<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ChatLineRound, DataAnalysis, Upload } from '@element-plus/icons-vue'
import { authApi } from '../api/service'
import { useSessionStore } from '../store/session'

const router = useRouter()
const session = useSessionStore()
const loading = ref(false)
const form = reactive({
  username: 'admin',
  password: 'admin123',
  remember: true,
})

async function handleLogin() {
  loading.value = true
  try {
    const data = await authApi.login({
      username: form.username,
      password: form.password,
    })
    session.setSession({
      token: data.token,
      user: {
        user_id: data.user_id,
        username: data.username,
        role: data.role,
      },
    })
    ElMessage.success('登录成功')
    router.push('/home')
  } catch {
    ElMessage.error('登录失败，请检查账号、密码或后端服务状态')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-shell">
    <section class="login-brand-panel">
      <div class="login-brand-content">
        <h1 class="login-title">数智化运营知识库</h1>
        <p class="login-copy">
          面向文档治理、混合检索、智能问答和运营分析的一体化后台。
        </p>
        <div class="login-highlight-grid">
          <div class="metric-card">
            <div class="metric-label metric-label-with-icon">
              <el-icon><Upload /></el-icon>
              <span>文档治理</span>
            </div>
            <div class="metric-value" style="font-size: 22px">Upload</div>
          </div>
          <div class="metric-card">
            <div class="metric-label metric-label-with-icon">
              <el-icon><ChatLineRound /></el-icon>
              <span>混合检索</span>
            </div>
            <div class="metric-value" style="font-size: 22px">Search</div>
          </div>
          <div class="metric-card">
            <div class="metric-label metric-label-with-icon">
              <el-icon><DataAnalysis /></el-icon>
              <span>运营分析</span>
            </div>
            <div class="metric-value" style="font-size: 22px">Insight</div>
          </div>
        </div>
      </div>
    </section>

    <section class="login-form-panel">
      <el-card class="panel-card login-card">
        <div class="login-card-inner">
          <div class="page-title" style="font-size: 28px; margin-bottom: 8px">登录系统</div>
          <p class="page-subtitle" style="margin-bottom: 26px">登录后可管理文档、问答和运营看板。</p>
          <el-form label-position="top" @submit.prevent="handleLogin">
            <el-form-item label="用户名">
              <el-input v-model="form.username" size="large" placeholder="请输入用户名" />
            </el-form-item>
            <el-form-item label="密码">
              <el-input v-model="form.password" size="large" type="password" show-password placeholder="请输入密码" />
            </el-form-item>
            <div class="login-row-hint">
              <el-checkbox v-model="form.remember">记住登录</el-checkbox>
              <span class="placeholder-note">默认管理员：admin / admin123</span>
            </div>
            <el-button
              type="primary"
              size="large"
              :loading="loading"
              style="width: 100%; border-radius: 16px; height: 48px"
              @click="handleLogin"
            >
              登录系统
            </el-button>
          </el-form>
        </div>
      </el-card>
    </section>
  </div>
</template>
