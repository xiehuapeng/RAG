<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ChatLineRound, DataAnalysis, Document, InfoFilled, Setting } from '@element-plus/icons-vue'
import { dashboardApi } from '../api/service'
import { formatDateTime } from '../utils/datetime'

const router = useRouter()
const loading = ref(false)
const stats = ref({
  document_count: 0,
  chunk_count: 0,
  retrieval_count: 0,
  feedback_positive_rate: 0,
})
const topQuestions = ref([])
const recentFeedback = ref([])

async function loadData() {
  loading.value = true
  try {
    const [statsData, topData, feedbackData] = await Promise.all([
      dashboardApi.stats(),
      dashboardApi.topQuestions(),
      dashboardApi.recentFeedback(),
    ])
    stats.value = statsData
    topQuestions.value = topData.slice(0, 5)
    recentFeedback.value = feedbackData.slice(0, 5)
  } finally {
    loading.value = false
  }
}

function go(path) {
  router.push(path)
}

onMounted(loadData)
</script>

<template>
  <section class="section-stack" v-loading="loading">
    <header class="chat-hero">
      <div>
        <p class="chat-hero-copy">
          统一查看知识库状态、核心入口和近期反馈。这里是进入问答、文档治理和运营分析的总入口。
        </p>
      </div>
      <div class="chat-hero-actions">
        <div class="chat-hero-chip">
          <span class="status-dot"></span>
          系统在线
        </div>
        <el-button type="primary" @click="go('/chat')">立即问答</el-button>
      </div>
    </header>

    <div class="metric-grid">
      <div class="metric-card">
        <div class="metric-label metric-label-with-icon">
          <el-icon><Document /></el-icon>
          <span>文档总数</span>
        </div>
        <div class="metric-value">{{ stats.document_count }}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label metric-label-with-icon">
          <el-icon><InfoFilled /></el-icon>
          <span>切片总数</span>
        </div>
        <div class="metric-value">{{ stats.chunk_count }}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label metric-label-with-icon">
          <el-icon><ChatLineRound /></el-icon>
          <span>检索次数</span>
        </div>
        <div class="metric-value">{{ stats.retrieval_count }}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label metric-label-with-icon">
          <el-icon><DataAnalysis /></el-icon>
          <span>有效反馈率</span>
        </div>
        <div class="metric-value">{{ Math.round((stats.feedback_positive_rate || 0) * 100) }}%</div>
      </div>
    </div>

    <div class="two-col">
      <el-card class="panel-card">
        <template #header>快捷入口</template>
        <div class="quick-question-grid">
          <el-button type="primary" round class="quick-question" :icon="Document" @click="go('/documents')">文档管理</el-button>
          <el-button round class="quick-question" :icon="ChatLineRound" @click="go('/chat')">智能问答</el-button>
          <el-button round class="quick-question" :icon="DataAnalysis" @click="go('/dashboard')">运营看板</el-button>
          <el-button round class="quick-question" :icon="Setting" @click="go('/config')">模型配置</el-button>
        </div>
      </el-card>

      <el-card class="panel-card">
        <template #header>高频问题</template>
        <el-empty v-if="!topQuestions.length" description="暂无提问数据" />
        <el-table v-else :data="topQuestions" size="small">
          <el-table-column type="index" width="60" />
          <el-table-column prop="query" label="问题" min-width="240" show-overflow-tooltip />
          <el-table-column prop="times" label="次数" width="90" />
        </el-table>
      </el-card>
    </div>

    <el-card class="panel-card">
      <template #header>最近反馈</template>
      <el-empty v-if="!recentFeedback.length" description="暂无反馈数据" />
      <el-table v-else :data="recentFeedback" size="small">
        <el-table-column prop="question" label="问题" min-width="260" show-overflow-tooltip />
        <el-table-column prop="feedback" label="反馈" width="100" />
        <el-table-column prop="comment" label="补充说明" min-width="220" show-overflow-tooltip />
        <el-table-column label="时间" width="180">
          <template #default="{ row }">
            {{ formatDateTime(row.created_at) }}
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </section>
</template>
