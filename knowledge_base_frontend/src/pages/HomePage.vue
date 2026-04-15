<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  ChatLineRound,
  Connection,
  DataAnalysis,
  Document,
  InfoFilled,
  MagicStick,
  Monitor,
  Setting,
  TrendCharts,
} from '@element-plus/icons-vue'
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

const quickCards = [
  {
    title: '进入问答舱',
    copy: '直接进入流式问答界面，查看证据来源、上下文追问与答案生成过程。',
    icon: ChatLineRound,
    path: '/chat',
    badge: '实时问答',
  },
  {
    title: '治理知识资产',
    copy: '上传、切片、预览文档内容，让知识源保持可检索、可追踪、可复用。',
    icon: Document,
    path: '/documents',
    badge: '知识治理',
  },
  {
    title: '查看运营脉冲',
    copy: '观察问答热度、反馈质量和近期波动趋势，快速判断系统使用强度。',
    icon: DataAnalysis,
    path: '/dashboard',
    badge: '运营洞察',
  },
  {
    title: '微调模型配置',
    copy: '管理模型参数、检索链路和回答策略，让问答风格与业务场景更贴合。',
    icon: Setting,
    path: '/config',
    badge: '模型控制',
  },
]

const capabilityCards = computed(() => [
  {
    title: '知识信号编排',
    value: stats.value.document_count,
    suffix: '份文档',
    copy: '把分散文档组织成可检索、可追踪、可验证的知识资产。',
    icon: Connection,
  },
  {
    title: '语义切片密度',
    value: stats.value.chunk_count,
    suffix: '个知识单元',
    copy: '通过切片与召回提升命中效率，支撑更细粒度的答案引用。',
    icon: MagicStick,
  },
  {
    title: '互动热度',
    value: stats.value.retrieval_count,
    suffix: '次检索',
    copy: '持续观察近期问答活跃度，快速判断系统使用强度。',
    icon: TrendCharts,
  },
])

const feedbackRate = computed(() => `${Math.round((stats.value.feedback_positive_rate || 0) * 100)}%`)

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
  <section class="section-stack home-stage" v-loading="loading">
    <header class="poster-hero knowledge-ocean-hero">
      <div class="poster-copy">
        <div class="soft-tag poster-kicker">
          <el-icon><Monitor /></el-icon>
          <span>知识海洋总控台</span>
        </div>
        <h2 class="poster-title">
          <span class="poster-title-line">把文档、检索、问答与运营反馈，</span>
          <span class="poster-title-line">汇入一片会发光的知识海洋。</span>
        </h2>
        <p class="poster-subtitle">
          这里不是传统后台首页，而是一块可观察、可进入、可追溯的知识控制台。你可以从这里进入问答、治理知识资产，并观察系统热度变化。
        </p>

        <div class="ocean-ribbon" aria-label="知识海洋能力">
          <span>语义潮汐</span>
          <span>证据航线</span>
          <span>追问回流</span>
        </div>

        <div class="poster-action-row">
          <el-button type="primary" size="large" @click="go('/chat')">立即问答</el-button>
          <el-button size="large" plain @click="go('/documents')">查看文档资产</el-button>
        </div>

        <div class="poster-chip-row">
          <div class="poster-chip">
            <span class="status-dot"></span>
            系统在线
          </div>
          <div class="poster-chip">语义问答就绪</div>
          <div class="poster-chip">证据链已连接</div>
        </div>
      </div>

      <div class="poster-visual knowledge-ocean-visual" aria-hidden="true">
        <div class="ocean-wave wave-a"></div>
        <div class="ocean-wave wave-b"></div>
        <div class="ocean-current current-a"></div>
        <div class="ocean-current current-b"></div>
        <div class="poster-orbit poster-orbit-a"></div>
        <div class="poster-orbit poster-orbit-b"></div>
        <div class="poster-core-card">
          <p>知识密度</p>
          <strong>{{ stats.chunk_count }}</strong>
          <span>个知识单元已入海</span>
        </div>
        <div class="poster-floating-card card-top">
          <span>反馈健康度</span>
          <strong>{{ feedbackRate }}</strong>
        </div>
        <div class="poster-floating-card card-bottom">
          <span>热门意图</span>
          <strong>{{ topQuestions[0]?.query || '等待新问题' }}</strong>
        </div>
        <div class="ocean-beacon beacon-a">召回</div>
        <div class="ocean-beacon beacon-b">重排</div>
        <div class="ocean-beacon beacon-c">引用</div>
      </div>
    </header>

    <div class="metric-grid">
      <div class="metric-card metric-card-accent">
        <div class="metric-label metric-label-with-icon">
          <el-icon><Document /></el-icon>
          <span>文档总数</span>
        </div>
        <div class="metric-value">{{ stats.document_count }}</div>
      </div>
      <div class="metric-card metric-card-accent">
        <div class="metric-label metric-label-with-icon">
          <el-icon><InfoFilled /></el-icon>
          <span>切片总数</span>
        </div>
        <div class="metric-value">{{ stats.chunk_count }}</div>
      </div>
      <div class="metric-card metric-card-accent">
        <div class="metric-label metric-label-with-icon">
          <el-icon><ChatLineRound /></el-icon>
          <span>检索次数</span>
        </div>
        <div class="metric-value">{{ stats.retrieval_count }}</div>
      </div>
      <div class="metric-card metric-card-accent">
        <div class="metric-label metric-label-with-icon">
          <el-icon><DataAnalysis /></el-icon>
          <span>有效反馈率</span>
        </div>
        <div class="metric-value">{{ feedbackRate }}</div>
      </div>
    </div>

    <div class="capability-strip">
      <article v-for="item in capabilityCards" :key="item.title" class="capability-card">
        <div class="capability-head">
          <div class="capability-icon">
            <el-icon><component :is="item.icon" /></el-icon>
          </div>
          <div>
            <p class="capability-title">{{ item.title }}</p>
            <p class="capability-copy">{{ item.copy }}</p>
          </div>
        </div>
        <div class="capability-value-row">
          <strong>{{ item.value }}</strong>
          <span>{{ item.suffix }}</span>
        </div>
      </article>
    </div>

    <div class="home-quick-grid">
      <button
        v-for="item in quickCards"
        :key="item.title"
        class="command-card"
        type="button"
        @click="go(item.path)"
      >
        <div class="command-card-top">
          <span class="command-card-badge">{{ item.badge }}</span>
          <el-icon class="command-card-icon">
            <component :is="item.icon" />
          </el-icon>
        </div>
        <h3>{{ item.title }}</h3>
        <p>{{ item.copy }}</p>
      </button>
    </div>

    <div class="two-col">
      <el-card class="panel-card cinematic-panel">
        <template #header>
          <div class="panel-header-inline">
            <span>高频问题雷达</span>
            <span class="panel-subtag">前 5</span>
          </div>
        </template>
        <el-empty v-if="!topQuestions.length" description="暂无提问数据" />
        <div v-else class="question-signal-list">
          <article v-for="(item, index) in topQuestions" :key="item.query" class="question-signal-card">
            <div class="question-signal-rank">0{{ index + 1 }}</div>
            <div class="question-signal-body">
              <h4>{{ item.query }}</h4>
              <p>已被触发 {{ item.times }} 次，适合做模板化优化与追问补齐。</p>
            </div>
          </article>
        </div>
      </el-card>

      <el-card class="panel-card cinematic-panel">
        <template #header>
          <div class="panel-header-inline">
            <span>近期反馈温度</span>
            <span class="panel-subtag">最近</span>
          </div>
        </template>
        <el-empty v-if="!recentFeedback.length" description="暂无反馈数据" />
        <div v-else class="feedback-poster-list">
          <article v-for="item in recentFeedback" :key="`${item.question}-${item.created_at}`" class="feedback-poster-card">
            <div class="feedback-poster-top">
              <span class="feedback-chip" :class="item.feedback === 'positive' ? 'positive' : 'negative'">
                {{ item.feedback === 'positive' ? '正向反馈' : '待优化' }}
              </span>
              <span class="feedback-time">{{ formatDateTime(item.created_at) }}</span>
            </div>
            <h4>{{ item.question }}</h4>
            <p>{{ item.comment || '用户未填写额外说明。' }}</p>
          </article>
        </div>
      </el-card>
    </div>
  </section>
</template>
