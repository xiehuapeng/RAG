<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ChatDotRound,
  CollectionTag,
  Compass,
  Delete,
  Histogram,
  Link,
  Loading,
  MagicStick,
  Plus,
  Promotion,
} from '@element-plus/icons-vue'
import { chatApi } from '../api/service'
import { formatDateTime } from '../utils/datetime'
import { deleteSessionBatch } from '../utils/sessionBatch'

const loading = ref(false)
const sending = ref(false)
const sessions = ref([])
const sessionId = ref(null)
const sessionTitle = ref('')
const messages = ref([])
const references = ref([])
const suggestedQuestions = ref([])
const draft = ref('')
const chatBodyRef = ref()
const referencePanelRef = ref()
const sidePanelHeight = ref(360)
const swipedSessionId = ref(null)
const dragSessionId = ref(null)
const dragOffsetX = ref(0)
const dragStartX = ref(0)
const dragStartY = ref(0)
const dragMoved = ref(false)
const streamingStatus = ref('')
const streamingFlow = ref(null)
const referenceDetailVisible = ref(false)
const activeReference = ref(null)
const batchManageMode = ref(false)
const batchDeleting = ref(false)
const selectedSessionIds = ref([])

let streamRenderTimer = null
let streamRenderQueue = ''
let streamRenderMessageId = null

const DELETE_REVEAL_WIDTH = 88
const MIN_SWIPE_DISTANCE = 18

const sessionScrollbarHeight = computed(() => `${sidePanelHeight.value}px`)
const referenceScrollbarHeight = computed(() => `${sidePanelHeight.value}px`)
const sessionCountLabel = computed(() => `${sessions.value.length} 个会话`)
const selectedSessionCount = computed(() => selectedSessionIds.value.length)
const allSessionsSelected = computed(
  () => sessions.value.length > 0 && selectedSessionIds.value.length === sessions.value.length,
)
const messageCountLabel = computed(() => `${messages.value.length} 条消息`)
const activeQuestionPreview = computed(() => {
  const lastUserMessage = [...messages.value].reverse().find((item) => item.role === 'user')
  return lastUserMessage?.content || '等待新的问题输入'
})
const referenceAverageScore = computed(() => {
  if (!references.value.length) {
    return '0.00'
  }
  const total = references.value.reduce((sum, item) => sum + Number(item?.score || 0), 0)
  return (total / references.value.length).toFixed(2)
})
const heroSignalCards = computed(() => [
  {
    title: '会话网格',
    value: sessions.value.length || 0,
    note: '个活跃会话',
    icon: Compass,
  },
  {
    title: '证据密度',
    value: references.value.length || 0,
    note: '条关联来源',
    icon: Link,
  },
  {
    title: '追问动能',
    value: suggestedQuestions.value.length || 0,
    note: '个可用追问',
    icon: MagicStick,
  },
])

const FLOW_STEPS = [
  { key: 'start', label: '开始' },
  { key: 'understanding', label: '理解问题' },
  { key: 'retrieving', label: '检索知识库' },
  { key: 'filtering', label: '分片过滤' },
  { key: 'generating', label: '整理答案' },
  { key: 'completed', label: '完成' },
]
const FLOW_MESSAGES = {
  start: '开始处理问题',
  understanding: '正在理解问题',
  retrieving: '正在检索知识库',
  filtering: '正在分片过滤',
  generating: '正在整理答案',
  completed: '处理完成',
}

function createDefaultStreamingFlow() {
  return {
    current_step: 'start',
    message: FLOW_MESSAGES.start,
    steps: FLOW_STEPS.map((step, index) => ({
      ...step,
      status: index === 0 ? 'active' : 'waiting',
    })),
  }
}

function normalizeStreamingFlow(payload) {
  const incomingSteps = Array.isArray(payload?.steps) ? payload.steps : []
  const incomingMap = new Map(incomingSteps.map((step) => [step.key, step]))
  const baseFlow = createDefaultStreamingFlow()
  return {
    current_step: payload?.current_step || baseFlow.current_step,
    message: FLOW_MESSAGES[payload?.current_step] || payload?.message || baseFlow.message,
    steps: FLOW_STEPS.map((step, index) => {
      const matched = incomingMap.get(step.key)
      return {
        ...step,
        status: matched?.status || baseFlow.steps[index].status,
      }
    }),
  }
}

function getPendingStatusText(message) {
  return message?.flow?.message || message?.status || '正在处理中'
}

function getPointerX(event) {
  return event?.touches?.[0]?.clientX ?? event?.changedTouches?.[0]?.clientX ?? event?.clientX ?? 0
}

function getPointerY(event) {
  return event?.touches?.[0]?.clientY ?? event?.changedTouches?.[0]?.clientY ?? event?.clientY ?? 0
}

function getSessionTranslateX(id) {
  if (dragSessionId.value === id) {
    return `${dragOffsetX.value}px`
  }
  if (swipedSessionId.value === id) {
    return `-${DELETE_REVEAL_WIDTH}px`
  }
  return '0px'
}

function syncSidePanelHeight() {
  nextTick(() => {
    const referenceBody = referencePanelRef.value?.$el?.querySelector?.('.side-scroll-body')
    const referenceEmpty = referencePanelRef.value?.$el?.querySelector?.('.el-empty')
    const measuredHeight = referenceBody?.offsetHeight || referenceEmpty?.offsetHeight || 360
    sidePanelHeight.value = Math.max(220, Math.round(measuredHeight))
  })
}

function scrollChatToBottom() {
  nextTick(() => {
    chatBodyRef.value?.setScrollTop?.(999999)
    syncSidePanelHeight()
  })
}

function stopStreamRender() {
  if (streamRenderTimer) {
    clearTimeout(streamRenderTimer)
    streamRenderTimer = null
  }
  streamRenderQueue = ''
  streamRenderMessageId = null
}

function flushStreamRender(final = false) {
  if (!streamRenderMessageId) {
    stopStreamRender()
    return
  }

  const currentMessage = messages.value.find((item) => item.id === streamRenderMessageId)
  if (!currentMessage) {
    stopStreamRender()
    return
  }

  if (final) {
    if (streamRenderQueue) {
      updatePendingAssistant(streamRenderMessageId, {
        content: `${currentMessage.content || ''}${streamRenderQueue}`,
        status: '正在整理答案',
      })
    }
    stopStreamRender()
    scrollChatToBottom()
    return
  }

  const batchSize = streamRenderQueue.length > 120 ? 36 : 18
  const chunk = streamRenderQueue.slice(0, batchSize)
  streamRenderQueue = streamRenderQueue.slice(batchSize)

  if (chunk) {
    updatePendingAssistant(streamRenderMessageId, {
      content: `${currentMessage.content || ''}${chunk}`,
      status: '正在整理答案',
    })
    scrollChatToBottom()
  }

  if (!streamRenderQueue) {
    streamRenderTimer = null
    return
  }

  streamRenderTimer = setTimeout(() => flushStreamRender(false), 24)
}

function enqueueStreamText(messageId, text) {
  if (!text) {
    return
  }
  if (streamRenderMessageId && streamRenderMessageId !== messageId) {
    flushStreamRender(true)
  }
  streamRenderMessageId = messageId
  streamRenderQueue += text
  if (!streamRenderTimer) {
    flushStreamRender(false)
  }
}

function escapeHtml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function formatInlineMarkdown(text) {
  return text
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
}

function renderMessageHtml(message) {
  const raw = escapeHtml(message?.content || '').trim()
  if (!raw) {
    return ''
  }

  const lines = raw.split('\n')
  const html = []
  let inList = false
  let paragraph = []

  const flushParagraph = () => {
    if (!paragraph.length) {
      return
    }
    html.push(`<p>${formatInlineMarkdown(paragraph.join('<br>'))}</p>`)
    paragraph = []
  }

  const closeList = () => {
    if (inList) {
      html.push('</ul>')
      inList = false
    }
  }

  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed) {
      flushParagraph()
      closeList()
      continue
    }

    const headingMatch = trimmed.match(/^(#{1,3})\s+(.*)$/)
    if (headingMatch) {
      flushParagraph()
      closeList()
      const level = Math.min(headingMatch[1].length + 1, 4)
      html.push(`<h${level}>${formatInlineMarkdown(headingMatch[2])}</h${level}>`)
      continue
    }

    const listMatch = trimmed.match(/^[-*]\s+(.*)$/)
    if (listMatch) {
      flushParagraph()
      if (!inList) {
        html.push('<ul>')
        inList = true
      }
      html.push(`<li>${formatInlineMarkdown(listMatch[1])}</li>`)
      continue
    }

    closeList()
    paragraph.push(trimmed)
  }

  flushParagraph()
  closeList()
  return html.join('')
}

function getReferenceCardTitle(item, index) {
  return item?.document_title || item?.title || `来源 ${index + 1}`
}

function getReferenceSourceTitle(item, index) {
  return item?.section_title || item?.chapter_path || item?.title || `知识单元 ${getReferenceUnitNumber(item, index)}`
}

function getReferenceUnitNumber(item, index) {
  const chunkIndex = Number(item?.chunk_index)
  if (Number.isInteger(chunkIndex) && chunkIndex >= 0) {
    return chunkIndex + 1
  }

  const evidenceId = Number(item?.evidence_id)
  if (Number.isInteger(evidenceId) && evidenceId > 0) {
    return evidenceId
  }

  return index + 1
}

function getReferenceSnippet(item) {
  return item?.snippet || item?.content || item?.full_content || JSON.stringify(item)
}

function getReferenceContent(item) {
  return item?.full_content || item?.content || item?.snippet || '暂无正文内容'
}

function getReferenceScoreWidth(item) {
  const score = Number(item?.score || 0)
  const width = Math.max(12, Math.min(100, Math.round(score * 100)))
  return `${width}%`
}

function closeReferenceDetail() {
  referenceDetailVisible.value = false
  activeReference.value = null
}

function openReferenceDetail(item, index) {
  activeReference.value = {
    ...item,
    __display_index: index,
  }
  referenceDetailVisible.value = true
}

function createPendingAssistantMessage() {
  const flow = createDefaultStreamingFlow()
  return {
    id: `local-assistant-${Date.now()}`,
    role: 'assistant',
    content: '',
    references: [],
    created_at: new Date().toISOString(),
    pending: true,
    failed: false,
    status: flow.message,
    flow,
  }
}

function updatePendingAssistant(messageId, patch) {
  const index = messages.value.findIndex((item) => item.id === messageId)
  if (index === -1) {
    return
  }
  messages.value[index] = {
    ...messages.value[index],
    ...patch,
  }
}

async function loadSessions(selectLatest = true) {
  const data = await chatApi.sessions()
  sessions.value = data
  selectedSessionIds.value = selectedSessionIds.value.filter((id) => data.some((item) => item.id === id))
  if (!data.length) {
    batchManageMode.value = false
  }
  if (!data.some((item) => item.id === sessionId.value)) {
    sessionId.value = null
    sessionTitle.value = ''
    messages.value = []
    references.value = []
    closeReferenceDetail()
  }
  if (selectLatest && data.length) {
    await selectSession(data[0].id)
  } else {
    syncSidePanelHeight()
  }
}

async function selectSession(id) {
  if (batchManageMode.value) {
    toggleSessionSelection(id)
    return
  }
  if (dragMoved.value) {
    dragMoved.value = false
    return
  }
  loading.value = true
  swipedSessionId.value = null
  try {
    const detail = await chatApi.sessionDetail(id)
    sessionId.value = detail.id
    sessionTitle.value = detail.title
    messages.value = detail.messages.map((item) => ({
      ...item,
      pending: false,
      failed: false,
      status: '',
      flow: null,
    }))
    const lastAssistant = [...detail.messages].reverse().find((item) => item.role === 'assistant')
    references.value = lastAssistant?.references || []
    closeReferenceDetail()
    suggestedQuestions.value = []
    scrollChatToBottom()
  } finally {
    loading.value = false
  }
}

async function createSession() {
  const { value } = await ElMessageBox.prompt('请输入会话名称', '新建会话', {
    inputValue: `知识库会话 ${formatDateTime(new Date())}`,
  })
  const data = await chatApi.createSession(value)
  await loadSessions(false)
  await selectSession(data.id)
  ElMessage.success('会话已创建')
}

async function deleteSession(item) {
  await ElMessageBox.confirm(`确认删除会话“${item.title}”吗？`, '删除确认', { type: 'warning' })
  await chatApi.deleteSession(item.id)
  selectedSessionIds.value = selectedSessionIds.value.filter((id) => id !== item.id)
  if (sessionId.value === item.id) {
    sessionId.value = null
    sessionTitle.value = ''
    messages.value = []
    references.value = []
    closeReferenceDetail()
  }
  swipedSessionId.value = null
  await loadSessions(false)
  if (!sessionId.value && sessions.value.length) {
    await selectSession(sessions.value[0].id)
  }
  ElMessage.success('会话已删除')
}

function toggleBatchManageMode() {
  if (batchDeleting.value || sending.value) return
  batchManageMode.value = !batchManageMode.value
  selectedSessionIds.value = []
  swipedSessionId.value = null
  dragSessionId.value = null
  dragOffsetX.value = 0
}

function toggleSessionSelection(id) {
  if (!batchManageMode.value || batchDeleting.value) {
    return
  }
  if (selectedSessionIds.value.includes(id)) {
    selectedSessionIds.value = selectedSessionIds.value.filter((item) => item !== id)
    return
  }
  selectedSessionIds.value = [...selectedSessionIds.value, id]
}

function toggleSelectAllSessions() {
  if (batchDeleting.value) return
  if (allSessionsSelected.value) {
    selectedSessionIds.value = []
    return
  }
  selectedSessionIds.value = sessions.value.map((item) => item.id)
}

async function deleteSelectedSessions() {
  if (batchDeleting.value || sending.value) return
  if (!selectedSessionIds.value.length) {
    ElMessage.warning('请先选择要删除的会话')
    return
  }
  const deletingIds = [...selectedSessionIds.value]
  batchDeleting.value = true
  try {
    try {
      await ElMessageBox.confirm(
        `确认批量删除已选中的 ${deletingIds.length} 个会话吗？`,
        '批量删除确认',
        { type: 'warning' },
      )
    } catch {
      return
    }
    const { successfulIds, failedIds } = await deleteSessionBatch(deletingIds, (id) => chatApi.deleteSession(id))
    sessions.value = sessions.value.filter((item) => !successfulIds.includes(item.id))
    if (successfulIds.includes(sessionId.value)) {
      sessionId.value = null
      sessionTitle.value = ''
      messages.value = []
      references.value = []
      closeReferenceDetail()
    }
    selectedSessionIds.value = failedIds
    swipedSessionId.value = null
    batchManageMode.value = failedIds.length > 0
    try {
      await loadSessions(false)
      if (!batchManageMode.value && !sessionId.value && sessions.value.length) {
        await selectSession(sessions.value[0].id)
      }
    } catch {
      ElMessage.warning('会话列表刷新失败，已删除项已移除，请稍后刷新确认')
    }
    if (failedIds.length) {
      ElMessage.warning(`已删除 ${successfulIds.length} 个会话，${failedIds.length} 个未确认删除，请重试`)
    } else {
      ElMessage.success(`已删除 ${successfulIds.length} 个会话`)
    }
  } finally {
    batchDeleting.value = false
  }
}

function handleSwipeStart(event, id) {
  if (batchManageMode.value) {
    return
  }
  swipedSessionId.value = swipedSessionId.value === id ? id : null
  dragSessionId.value = id
  dragOffsetX.value = swipedSessionId.value === id ? -DELETE_REVEAL_WIDTH : 0
  dragStartX.value = getPointerX(event)
  dragStartY.value = getPointerY(event)
  dragMoved.value = false
}

function handleSwipeMove(event, id) {
  if (batchManageMode.value) {
    return
  }
  if (dragSessionId.value !== id) {
    return
  }
  const deltaX = getPointerX(event) - dragStartX.value
  const deltaY = getPointerY(event) - dragStartY.value
  if (Math.abs(deltaY) > Math.abs(deltaX) && Math.abs(deltaY) > 10) {
    dragSessionId.value = null
    dragOffsetX.value = 0
    return
  }
  if (Math.abs(deltaX) > 4) {
    dragMoved.value = true
  }
  dragOffsetX.value = Math.max(-DELETE_REVEAL_WIDTH, Math.min(0, deltaX))
  if (dragMoved.value) {
    event.preventDefault?.()
  }
}

function handleSwipeEnd(id) {
  if (batchManageMode.value) {
    return
  }
  if (dragSessionId.value !== id) {
    return
  }
  swipedSessionId.value = dragOffsetX.value <= -MIN_SWIPE_DISTANCE ? id : null
  dragSessionId.value = null
  dragOffsetX.value = 0
}

async function ensureSession() {
  if (sessionId.value) {
    return sessionId.value
  }
  const data = await chatApi.createSession(`知识库会话 ${formatDateTime(new Date())}`)
  await loadSessions(false)
  sessionId.value = data.id
  sessionTitle.value = data.title
  return data.id
}

async function sendMessage(content = draft.value) {
  if (!content.trim() || sending.value || batchDeleting.value) {
    return
  }

  const userContent = content.trim()
  const activeSessionId = await ensureSession()
  const pendingAssistant = createPendingAssistantMessage()
  stopStreamRender()

  sending.value = true
  streamingFlow.value = createDefaultStreamingFlow()
  streamingStatus.value = streamingFlow.value.message
  suggestedQuestions.value = []
  references.value = []
  closeReferenceDetail()
  messages.value.push({
    id: `local-user-${Date.now()}`,
    role: 'user',
    content: userContent,
    references: [],
    created_at: new Date().toISOString(),
    pending: false,
    failed: false,
    status: '',
  })
  messages.value.push(pendingAssistant)
  draft.value = ''
  scrollChatToBottom()

  try {
    await chatApi.streamMessage(activeSessionId, userContent, {
      status(payload) {
        const message = payload?.message || '正在处理中'
        if (!streamingFlow.value) {
          streamingStatus.value = message
          updatePendingAssistant(pendingAssistant.id, { status: message })
        }
        scrollChatToBottom()
      },
      progress(payload) {
        const flow = normalizeStreamingFlow(payload)
        streamingFlow.value = flow
        streamingStatus.value = flow.message
        updatePendingAssistant(pendingAssistant.id, {
          status: flow.message,
          flow,
        })
        scrollChatToBottom()
      },
      evidence(payload) {
        references.value = payload?.evidence || []
        scrollChatToBottom()
      },
      delta(payload) {
        const contentPart = payload?.content || ''
        enqueueStreamText(pendingAssistant.id, contentPart)
      },
      end(payload) {
        flushStreamRender(true)
        const completedFlow = normalizeStreamingFlow({
          current_step: 'completed',
          message: '处理完成',
          steps: FLOW_STEPS.map((step) => ({
            key: step.key,
            label: step.label,
            status: 'done',
          })),
        })
        updatePendingAssistant(pendingAssistant.id, {
          content: payload?.answer || messages.value.find((item) => item.id === pendingAssistant.id)?.content || '',
          pending: false,
          failed: false,
          status: '',
          flow: completedFlow,
        })
        suggestedQuestions.value = payload?.suggestions || []
        streamingFlow.value = null
        streamingStatus.value = ''
        scrollChatToBottom()
      },
      error(payload) {
        throw new Error(payload?.message || '本次回答生成失败，请稍后重试。')
      },
    })

    await loadSessions(false)
    scrollChatToBottom()
  } catch (error) {
    stopStreamRender()
    updatePendingAssistant(pendingAssistant.id, {
      pending: false,
      failed: true,
      status: '',
      flow: null,
      content: error?.message || '本次回答生成失败，请稍后重试。',
    })
    ElMessage.error(error?.message || '发送消息失败')
  } finally {
    stopStreamRender()
    sending.value = false
    streamingFlow.value = null
    streamingStatus.value = ''
  }
}

async function loadPopularQuestions() {
  suggestedQuestions.value = await chatApi.popularQuestions()
}

async function submitFeedback(message, feedback) {
  await chatApi.feedback(message.id, { feedback, comment: '' })
  ElMessage.success('反馈已提交')
}

onMounted(async () => {
  await Promise.all([loadSessions(), loadPopularQuestions()])
  syncSidePanelHeight()
})
</script>

<template>
  <section class="chat-shell section-stack">
    <header class="chat-hero chat-hero-poster">
      <div class="chat-hero-copy-block">
        <div class="soft-tag poster-kicker">
          <el-icon><Histogram /></el-icon>
          <span>问答信号甲板</span>
        </div>
        <h2 class="chat-hero-title">把每一次提问都变成一张会发光的情报卡，而不是一段孤立文本。</h2>
        <p class="chat-hero-copy">
          支持历史会话、流式生成、引用来源和追问建议。答案、证据和推荐动作被排成可浏览、可回看的互动舞台。
        </p>

        <div class="chat-hero-actions">
          <div class="chat-hero-chip">
            <span class="status-dot"></span>
            {{ sending ? '回答生成中' : '检索在线' }}
          </div>
          <div class="chat-hero-chip">平均证据分 {{ referenceAverageScore }}</div>
          <el-button type="primary" :icon="Plus" @click="createSession">新建会话</el-button>
        </div>
      </div>

      <div class="chat-hero-stage">
        <div class="chat-hero-stage-glow"></div>
        <div class="chat-stage-headline">
          <span>最新问题</span>
          <strong>{{ activeQuestionPreview }}</strong>
        </div>
        <div class="chat-stage-signal-grid">
          <article v-for="item in heroSignalCards" :key="item.title" class="chat-stage-card">
            <el-icon class="chat-stage-card-icon">
              <component :is="item.icon" />
            </el-icon>
            <span>{{ item.title }}</span>
            <strong>{{ item.value }}</strong>
            <small>{{ item.note }}</small>
          </article>
        </div>
      </div>
    </header>

    <div class="chat-layout">
      <el-card class="chat-panel chat-panel--sessions">
        <template #header>
          <div class="chat-panel-header">
            <div>
              <div class="chat-panel-title panel-title-with-icon">
                <el-icon><ChatDotRound /></el-icon>
                <span>会话列表</span>
              </div>
              <div class="chat-panel-meta">
                {{ batchManageMode ? '勾选会话后可批量删除' : '支持滑动删除与快速切换' }}
              </div>
            </div>
            <div class="chat-session-tools">
              <div class="chat-panel-meta">
                {{ batchManageMode ? `已选 ${selectedSessionCount} 项` : sessionCountLabel }}
              </div>
              <el-button text size="small" :disabled="batchDeleting || sending" @click="toggleBatchManageMode">
                {{ batchManageMode ? '取消' : '批量管理' }}
              </el-button>
              <el-button
                v-if="batchManageMode && sessions.length"
                text
                size="small"
                @click="toggleSelectAllSessions"
                :disabled="batchDeleting"
              >
                {{ allSessionsSelected ? '取消全选' : '全选' }}
              </el-button>
              <el-button
                v-if="batchManageMode"
                type="danger"
                size="small"
                :disabled="!selectedSessionCount || sending"
                :loading="batchDeleting"
                @click="deleteSelectedSessions"
              >
                删除所选
              </el-button>
            </div>
          </div>
        </template>

        <el-empty v-if="!sessions.length" description="暂无会话" />
        <el-scrollbar v-else :max-height="sessionScrollbarHeight" class="chat-session-scroll">
          <div class="chat-session-list">
            <div
              v-for="item in sessions"
              :key="item.id"
              :class="[
                'chat-session-row',
                {
                  'is-revealed': !batchManageMode && swipedSessionId === item.id,
                  'is-dragging': !batchManageMode && dragSessionId === item.id,
                  'is-batch-mode': batchManageMode,
                  'is-selected': selectedSessionIds.includes(item.id),
                },
              ]"
            >
              <button
                v-if="!batchManageMode"
                class="chat-session-action"
                type="button"
                @click.stop="deleteSession(item)"
              >
                <el-icon><Delete /></el-icon>
                <span>删除</span>
              </button>
              <button
                type="button"
                :class="[
                  'chat-session-item',
                  {
                    active: !batchManageMode && item.id === sessionId,
                    'is-batch-mode': batchManageMode,
                    'is-selected': selectedSessionIds.includes(item.id),
                  },
                ]"
                :style="{ transform: `translateX(${getSessionTranslateX(item.id)})` }"
                @click="selectSession(item.id)"
                @touchstart.passive="handleSwipeStart($event, item.id)"
                @touchmove="handleSwipeMove($event, item.id)"
                @touchend="handleSwipeEnd(item.id)"
                @mousedown="handleSwipeStart($event, item.id)"
                @mousemove="handleSwipeMove($event, item.id)"
                @mouseup="handleSwipeEnd(item.id)"
                @mouseleave="handleSwipeEnd(item.id)"
              >
                <span v-if="batchManageMode" class="chat-session-check">
                  <el-checkbox
                    :model-value="selectedSessionIds.includes(item.id)"
                    :disabled="batchDeleting"
                    @click.stop
                    @change="toggleSessionSelection(item.id)"
                  />
                </span>
                <span class="chat-session-title">{{ item.title }}</span>
              </button>
            </div>
          </div>
        </el-scrollbar>
      </el-card>

      <el-card class="chat-panel">
        <template #header>
          <div class="chat-panel-header">
            <div>
              <div class="chat-panel-title">{{ sessionTitle || '对话区' }}</div>
              <div class="chat-panel-meta">{{ messageCountLabel }}</div>
            </div>
            <div class="chat-panel-meta">{{ sending ? '正在生成答案' : '等待提问' }}</div>
          </div>
        </template>

        <div class="chat-composer">
          <div v-if="sending && streamingFlow" class="chat-stream-banner">
            <div class="chat-stream-banner-head">
              <el-icon class="is-loading">
                <Loading />
              </el-icon>
              <span>{{ streamingFlow.message || streamingStatus }}</span>
            </div>
            <div class="chat-progress-steps">
              <div
                v-for="step in streamingFlow.steps"
                :key="step.key"
                :class="['chat-progress-step', `is-${step.status}`]"
              >
                <span class="chat-progress-node"></span>
                <span class="chat-progress-label">{{ step.label }}</span>
              </div>
            </div>
          </div>

          <el-scrollbar ref="chatBodyRef" max-height="540px" v-loading="loading">
            <div v-if="!messages.length" class="chat-empty-poster">
              <div class="chat-empty-orb"></div>
              <div class="chat-empty-copy">
                <span class="soft-tag">Start Scene</span>
                <h3>从一个业务问题开始，把它变成带证据的答案海报。</h3>
                <p>你可以直接问开户流程、业务规则、文档操作路径，右侧会同步展示引用来源与后续追问建议。</p>
              </div>
            </div>

            <div class="section-stack">
              <div
                v-for="message in messages"
                :key="message.id"
                :class="['chat-message', message.role === 'user' ? 'user' : 'assistant']"
              >
                <div class="chat-message-meta">
                  <span class="chat-role-badge">{{ message.role === 'user' ? '用户提问' : '知识库回答' }}</span>
                  <span class="chat-message-time">{{ formatDateTime(message.created_at) }}</span>
                </div>

                <div :class="['chat-bubble', message.role === 'user' ? 'user' : 'assistant', { failed: message.failed }]">
                  <div v-if="message.pending" class="chat-pending-line">
                    <el-icon class="is-loading">
                      <Loading />
                    </el-icon>
                    <span>{{ getPendingStatusText(message) }}</span>
                  </div>
                  <div
                    v-if="message.content"
                    :class="['chat-rich-text', { user: message.role === 'user', assistant: message.role === 'assistant' }]"
                    v-html="renderMessageHtml(message)"
                  />
                </div>

                <div
                  v-if="message.role === 'assistant' && !message.pending && !message.failed"
                  class="chat-feedback-bar"
                >
                  <el-button size="small" text class="chat-feedback-button" @click="submitFeedback(message, 'positive')">
                    有帮助
                  </el-button>
                  <el-button size="small" text class="chat-feedback-button" @click="submitFeedback(message, 'negative')">
                    没帮助
                  </el-button>
                </div>
              </div>
            </div>
          </el-scrollbar>

          <div class="chat-composer">
            <el-input
              v-model="draft"
              type="textarea"
              :rows="4"
              :disabled="sending"
              placeholder="请输入问题，Enter 发送，Shift + Enter 换行"
              @keydown.enter.exact.prevent="sendMessage()"
            />
            <div class="chat-composer-hint">
              <span>Enter 发送，Shift + Enter 换行</span>
              <el-button type="primary" :loading="sending" @click="sendMessage()">
                {{ sending ? '发送中...' : '发送问题' }}
              </el-button>
            </div>
          </div>
        </div>
      </el-card>

      <div class="section-stack">
        <el-card ref="referencePanelRef" class="chat-panel">
          <template #header>
              <div class="chat-panel-header">
                <div>
                  <div class="chat-panel-title panel-title-with-icon">
                    <el-icon><Link /></el-icon>
                    <span>引用来源</span>
                  </div>
                  <div class="chat-panel-meta">答案命中的知识片段</div>
                </div>
                <div class="chat-panel-meta">{{ references.length }} 条</div>
            </div>
          </template>

          <div class="side-scroll-body">
            <el-empty v-if="!references.length" description="发送问题后显示命中片段" />
            <el-scrollbar v-else :max-height="referenceScrollbarHeight">
              <div class="reference-list">
                <article v-for="(item, index) in references" :key="index" class="reference-item">
                  <div class="reference-item-head">
                    <button
                      type="button"
                      class="reference-item-title reference-title-button"
                      @click="openReferenceDetail(item, index)"
                    >
                      {{ getReferenceCardTitle(item, index) }}
                    </button>
                    <el-tag size="small" effect="dark" class="source-tag-with-icon">
                      <el-icon><CollectionTag /></el-icon>
                      <span>Source {{ index + 1 }}</span>
                    </el-tag>
                  </div>
                  <div class="reference-score-track">
                    <span class="reference-score-fill" :style="{ width: getReferenceScoreWidth(item) }"></span>
                  </div>
                  <p class="reference-item-snippet">{{ getReferenceSnippet(item) }}</p>
                  <div class="reference-meta-row">
                    <span>Score {{ Number(item?.score || 0).toFixed(2) }}</span>
                    <span>{{ item?.section_title || item?.chapter_path || '未标注章节' }}</span>
                  </div>
                </article>
              </div>
            </el-scrollbar>
          </div>
        </el-card>

        <el-card class="chat-panel">
          <template #header>
            <div class="chat-panel-header">
              <div>
                <div class="chat-panel-title panel-title-with-icon">
                  <el-icon><Promotion /></el-icon>
                  <span>推荐追问</span>
                </div>
                <div class="chat-panel-meta">基于当前会话生成</div>
              </div>
              <div class="chat-panel-meta">{{ suggestedQuestions.length }} 项</div>
            </div>
          </template>

          <el-empty v-if="!suggestedQuestions.length" description="暂无推荐问题" />
          <div v-else class="quick-question-grid">
            <el-button
              v-for="item in suggestedQuestions"
              :key="item"
              round
              class="quick-question"
              :icon="ChatDotRound"
              :disabled="sending"
              @click="sendMessage(item)"
            >
              {{ item }}
            </el-button>
          </div>
        </el-card>
      </div>
    </div>

    <el-dialog
      v-model="referenceDetailVisible"
      title="知识单元详情"
      width="760px"
      destroy-on-close
      @closed="closeReferenceDetail"
    >
      <div v-if="activeReference" class="reference-detail-stack">
        <div class="preview-block">
          <div class="preview-block-head">
            <span>来源信息</span>
            <el-tag size="small" type="primary" effect="light">
              知识单元 {{ getReferenceUnitNumber(activeReference, activeReference.__display_index || 0) }}
            </el-tag>
          </div>
          <div class="reference-detail-meta">
            <div class="reference-detail-field">
              <span class="reference-detail-label">来源文档</span>
              <p class="reference-detail-value">
                {{ activeReference.document_title || '未提供来源文档' }}
              </p>
            </div>
            <div class="reference-detail-field">
              <span class="reference-detail-label">来源标题</span>
              <p class="reference-detail-value">
                {{ getReferenceSourceTitle(activeReference, activeReference.__display_index || 0) }}
              </p>
            </div>
            <div class="reference-detail-field">
              <span class="reference-detail-label">章节路径</span>
              <p class="reference-detail-value">
                {{ activeReference.chapter_path || '未提供章节路径' }}
              </p>
            </div>
            <div class="reference-detail-field">
              <span class="reference-detail-label">检索得分</span>
              <p class="reference-detail-value">
                {{ activeReference.score ?? '未提供' }}
              </p>
            </div>
          </div>
        </div>

        <div class="preview-block">
          <div class="preview-block-head">
            <span>正文内容</span>
          </div>
          <p class="preview-text reference-detail-content">{{ getReferenceContent(activeReference) }}</p>
        </div>
      </div>
    </el-dialog>
  </section>
</template>
