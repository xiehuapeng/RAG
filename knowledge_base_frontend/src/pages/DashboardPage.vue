<script setup>
import { onMounted, ref } from 'vue'
import { ChatDotRound, DataAnalysis, Document, Histogram, QuestionFilled, TrendCharts } from '@element-plus/icons-vue'
import { dashboardApi } from '../api/service'
import { formatDateTime } from '../utils/datetime'

const loading = ref(false)
const days = ref(7)
const stats = ref({
  retrieval_count: 0,
  upload_count: 0,
  parse_success_rate: 0,
  feedback_positive_rate: 0,
})
const feedbackStats = ref({
  positive: 0,
  negative: 0,
  total: 0,
  positive_rate: 0,
})
const topQuestions = ref([])
const noAnswer = ref([])
const recentFeedback = ref([])
const askTrend = ref([])
const uploadTrend = ref([])

async function loadData() {
  loading.value = true
  try {
    const [statsData, topData, noAnswerData, recentFeedbackData, askTrendData, uploadTrendData, feedbackStatsData] = await Promise.all([
      dashboardApi.stats(),
      dashboardApi.topQuestions(),
      dashboardApi.noAnswer(),
      dashboardApi.recentFeedback(),
      dashboardApi.askTrend(days.value),
      dashboardApi.uploadTrend(days.value),
      dashboardApi.feedback(),
    ])
    stats.value = statsData
    topQuestions.value = topData
    noAnswer.value = noAnswerData
    recentFeedback.value = recentFeedbackData
    askTrend.value = askTrendData
    uploadTrend.value = uploadTrendData
    feedbackStats.value = feedbackStatsData
  } finally {
    loading.value = false
  }
}

function formatTrendDate(value) {
  return formatDateTime(value ? `${value} 00:00:00` : value)
}

onMounted(loadData)
</script>

<template>
  <section class="section-stack" v-loading="loading">
    <header class="chat-hero">
      <div>
        <p class="chat-hero-copy">
          观察提问趋势、上传趋势、无答案问题和反馈质量，帮助定位知识库内容缺口。
        </p>
      </div>
      <div class="chat-hero-actions">
        <el-select v-model="days" style="width: 120px" @change="loadData">
          <el-option :value="7" label="近 7 天" />
          <el-option :value="15" label="近 15 天" />
          <el-option :value="30" label="近 30 天" />
        </el-select>
      </div>
    </header>

    <div class="metric-grid">
      <div class="metric-card">
        <div class="metric-label metric-label-with-icon">
          <el-icon><ChatDotRound /></el-icon>
          <span>提问总量</span>
        </div>
        <div class="metric-value">{{ stats.retrieval_count }}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label metric-label-with-icon">
          <el-icon><Document /></el-icon>
          <span>文档上传量</span>
        </div>
        <div class="metric-value">{{ stats.upload_count }}</div>
      </div>
      <div class="metric-card">
        <div class="metric-label metric-label-with-icon">
          <el-icon><TrendCharts /></el-icon>
          <span>解析成功率</span>
        </div>
        <div class="metric-value">{{ Math.round((stats.parse_success_rate || 0) * 100) }}%</div>
      </div>
      <div class="metric-card">
        <div class="metric-label metric-label-with-icon">
          <el-icon><Histogram /></el-icon>
          <span>正向反馈率</span>
        </div>
        <div class="metric-value">{{ Math.round((feedbackStats.positive_rate || stats.feedback_positive_rate || 0) * 100) }}%</div>
      </div>
    </div>

    <div class="two-col">
      <el-card class="panel-card">
        <template #header>
          <span class="panel-title-with-icon">
            <el-icon><TrendCharts /></el-icon>
            <span>提问趋势</span>
          </span>
        </template>
        <el-empty v-if="!askTrend.length" description="暂无提问趋势数据" />
        <el-table v-else :data="askTrend" size="small">
          <el-table-column label="日期" width="180">
            <template #default="{ row }">
              {{ formatTrendDate(row.date) }}
            </template>
          </el-table-column>
          <el-table-column prop="count" label="提问次数" />
        </el-table>
      </el-card>

      <el-card class="panel-card">
        <template #header>
          <span class="panel-title-with-icon">
            <el-icon><Document /></el-icon>
            <span>上传趋势</span>
          </span>
        </template>
        <el-empty v-if="!uploadTrend.length" description="暂无上传趋势数据" />
        <el-table v-else :data="uploadTrend" size="small">
          <el-table-column label="日期" width="180">
            <template #default="{ row }">
              {{ formatTrendDate(row.date) }}
            </template>
          </el-table-column>
          <el-table-column prop="count" label="上传数量" />
        </el-table>
      </el-card>
    </div>

    <div class="two-col">
      <el-card class="panel-card">
        <template #header>
          <span class="panel-title-with-icon">
            <el-icon><QuestionFilled /></el-icon>
            <span>高频问题 Top N</span>
          </span>
        </template>
        <el-empty v-if="!topQuestions.length" description="暂无高频问题" />
        <el-table v-else :data="topQuestions" size="small">
          <el-table-column type="index" width="60" />
          <el-table-column prop="query" label="问题" min-width="240" show-overflow-tooltip />
          <el-table-column prop="times" label="次数" width="90" />
        </el-table>
      </el-card>
      <el-card class="panel-card">
        <template #header>
          <span class="panel-title-with-icon">
            <el-icon><QuestionFilled /></el-icon>
            <span>无答案问题</span>
          </span>
        </template>
        <el-empty v-if="!noAnswer.length" description="暂无无答案问题" />
        <el-table v-else :data="noAnswer" size="small">
          <el-table-column type="index" width="60" />
          <el-table-column prop="query" label="问题" min-width="240" show-overflow-tooltip />
          <el-table-column prop="times" label="次数" width="90" />
        </el-table>
      </el-card>
    </div>

    <el-card class="panel-card">
      <template #header>
        <span class="panel-title-with-icon">
          <el-icon><ChatDotRound /></el-icon>
          <span>最近反馈明细</span>
        </span>
      </template>
      <el-empty v-if="!recentFeedback.length" description="暂无反馈数据" />
      <el-table v-else :data="recentFeedback" size="small">
        <el-table-column prop="question" label="问题" min-width="240" show-overflow-tooltip />
        <el-table-column prop="answer" label="回答" min-width="260" show-overflow-tooltip />
        <el-table-column prop="feedback" label="反馈" width="90" />
        <el-table-column prop="comment" label="补充说明" min-width="200" show-overflow-tooltip />
        <el-table-column label="时间" width="180">
          <template #default="{ row }">
            {{ formatDateTime(row.created_at) }}
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </section>
</template>
