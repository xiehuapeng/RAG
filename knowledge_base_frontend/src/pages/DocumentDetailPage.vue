<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Collection, Delete, Document, EditPen, Files, MagicStick, Operation, RefreshRight } from '@element-plus/icons-vue'
import { documentApi } from '../api/service'

const route = useRoute()
const loading = ref(false)
const loadingText = ref('页面加载中...')
const documentInfo = ref(null)
const outline = ref([])
const chunks = ref([])
const content = ref('')
const activeNodeId = ref('')
const activeNodePath = ref('')
const editVisible = ref(false)
const editingChunk = ref(null)
const editForm = reactive({
  content: '',
  metadataText: '{}',
})

const documentId = computed(() => route.params.id)

function flattenOutline(nodes, acc = [], depth = 0) {
  for (const node of nodes) {
    acc.push({ ...node, depth })
    if (node.children?.length) {
      flattenOutline(node.children, acc, depth + 1)
    }
  }
  return acc
}

const outlineNodes = computed(() => flattenOutline(outline.value))
const outlineOptions = computed(() =>
  outlineNodes.value.map((node) => ({
    ...node,
    optionLabel: `${'· '.repeat(node.depth)}${node.label || ''}`,
  })),
)

const displayWidth = computed(() => {
  const lengths = outlineNodes.value.map((node) => (node.label || '').replace(/\s+/g, '').length)
  const longest = lengths.length ? Math.max(...lengths) : 24
  return Math.min(40, Math.max(24, longest + 6))
})

function ellipsisText(text, limit) {
  const value = String(text || '').trim()
  if (!value) {
    return ''
  }
  return value.length > limit ? `${value.slice(0, limit)}...` : value
}

function normalizeChunks(list) {
  return list.map((item) => {
    const metadata = item.metadata || {}
    return {
      ...item,
      chapterPath: metadata.chapter_path || metadata.section_title || '',
      sectionTitle: metadata.section_title || '',
    }
  })
}

const normalizedChunks = computed(() => normalizeChunks(chunks.value))

const selectedChunks = computed(() => {
  const path = activeNodePath.value
  if (!path) {
    return normalizedChunks.value.slice(0, 1)
  }
  return normalizedChunks.value.filter((chunk) => {
    const chapterPath = chunk.chapterPath || ''
    return chapterPath === path || chapterPath.startsWith(`${path} /`)
  })
})

const previewChunks = computed(() => {
  const limit = Math.max(displayWidth.value * 5, 120)
  return selectedChunks.value.map((chunk) => ({
    ...chunk,
    preview: ellipsisText(chunk.content, limit),
  }))
})

const previewTitle = computed(() => {
  if (activeNodePath.value) {
    return activeNodePath.value
  }
  return documentInfo.value?.title || '正文预览'
})

async function withPageLoading(text, task) {
  loading.value = true
  loadingText.value = text
  try {
    return await task()
  } finally {
    loading.value = false
  }
}

async function loadData(options = {}) {
  const { showLoading = true } = options
  const run = async () => {
    const [detail, outlineData, chunkList, contentData] = await Promise.all([
      documentApi.detail(documentId.value),
      documentApi.outline(documentId.value),
      documentApi.chunks(documentId.value),
      documentApi.content(documentId.value),
    ])
    documentInfo.value = detail
    outline.value = outlineData
    chunks.value = chunkList
    content.value = contentData.content || ''
    const flatOutline = flattenOutline(outlineData || [])
    if (!activeNodeId.value || !flatOutline.some((node) => node.id === activeNodeId.value)) {
      activeNodeId.value = flatOutline?.[0]?.id || ''
      activeNodePath.value = flatOutline?.[0]?.path || flatOutline?.[0]?.label || ''
    }
  }

  if (!showLoading) {
    await run()
    return
  }
  await withPageLoading('页面加载中...', run)
}

function handleNodeClick(data) {
  if (loading.value) {
    return
  }
  activeNodeId.value = data.id || ''
  activeNodePath.value = data.path || data.label || ''
}

function handleOutlineSelect(nodeId) {
  if (loading.value) {
    return
  }
  if (!nodeId) {
    activeNodeId.value = ''
    activeNodePath.value = ''
    return
  }
  const node = outlineNodes.value.find((item) => item.id === nodeId)
  if (node) {
    activeNodeId.value = node.id || ''
    activeNodePath.value = node.path || node.label || ''
  }
}

function openEdit(chunk) {
  editingChunk.value = chunk
  editForm.content = chunk.content
  editForm.metadataText = JSON.stringify(chunk.metadata || {}, null, 2)
  editVisible.value = true
}

async function saveChunk() {
  const metadata = JSON.parse(editForm.metadataText || '{}')
  await withPageLoading('正在保存知识单元...', async () => {
    await documentApi.updateChunk(documentId.value, editingChunk.value.id, {
      content: editForm.content,
      metadata,
    })
    editVisible.value = false
    await loadData({ showLoading: false })
  })
  ElMessage.success('知识单元已更新')
}

async function deleteChunk(chunk) {
  await ElMessageBox.confirm(`确认删除知识单元 ${chunk.chunk_index} 吗？`, '删除确认', { type: 'warning' })
  await withPageLoading('正在删除知识单元...', async () => {
    await documentApi.deleteChunk(documentId.value, chunk.id)
    await loadData({ showLoading: false })
  })
  ElMessage.success('知识单元已删除')
}

async function handleReindex() {
  await withPageLoading('正在重建索引，请稍候...', async () => {
    await documentApi.reindex(documentId.value)
    await loadData({ showLoading: false })
  })
  ElMessage.success('已重建索引')
}

async function handleResplit() {
  await withPageLoading('正在重新切分文档，请稍候...', async () => {
    await documentApi.resplit(documentId.value)
    await loadData({ showLoading: false })
  })
  ElMessage.success('已重新切分')
}

onMounted(loadData)
</script>

<template>
  <section
    class="section-stack document-detail"
    :style="{ '--detail-preview-width': `${displayWidth}ch` }"
    v-loading.fullscreen.lock="loading"
    :element-loading-text="loadingText"
  >
    <header class="chat-hero">
      <div>
        <h1 class="chat-hero-title page-title-with-icon">
          <el-icon><Document /></el-icon>
          <span>文档详情</span>
        </h1>
        <p class="chat-hero-copy">
          查看目录树、正文预览和知识单元，并支持切片编辑、删除、重建索引和重新切分。
        </p>
      </div>
      <div class="chat-hero-actions">
        <el-button :icon="Operation" @click="handleResplit">重新切分</el-button>
        <el-button type="primary" :icon="RefreshRight" @click="handleReindex">重建索引</el-button>
      </div>
    </header>

    <el-card v-if="documentInfo" class="panel-card">
      <div class="document-meta-grid">
        <div class="metric-card">
          <div class="metric-label">文档标题</div>
          <div class="metric-value" style="font-size: 22px">{{ documentInfo.title }}</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">文件类型</div>
          <div class="metric-value" style="font-size: 22px">{{ documentInfo.file_type }}</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">状态</div>
          <div class="metric-value" style="font-size: 22px">{{ documentInfo.status }}</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">切片数</div>
          <div class="metric-value" style="font-size: 22px">{{ documentInfo.chunk_count }}</div>
        </div>
      </div>
    </el-card>

    <div class="two-col detail-grid">
      <el-card class="panel-card detail-tree-card">
        <template #header>
          <span class="panel-title-with-icon">
            <el-icon><Collection /></el-icon>
            <span>章节目录</span>
          </span>
        </template>
        <el-empty v-if="!outline.length" description="暂无目录数据" />
        <div v-else class="detail-tree-panel">
          <el-select
            v-model="activeNodeId"
            class="chapter-select"
            filterable
            clearable
            placeholder="搜索或选择章节"
            @change="handleOutlineSelect"
          >
            <el-option
              v-for="node in outlineOptions"
              :key="node.id"
              :label="node.optionLabel"
              :value="node.id"
            />
          </el-select>
          <div class="outline-hint">章节较多时可直接通过下拉定位，树形目录用于辅助浏览。</div>
          <el-scrollbar max-height="360px">
            <el-tree
              class="detail-tree"
              :data="outline"
              node-key="id"
              :props="{ label: 'label', children: 'children' }"
              :current-node-key="activeNodeId"
              highlight-current
              @node-click="handleNodeClick"
            >
              <template #default="{ data }">
                <div class="detail-tree-node">
                  <div class="detail-tree-title" :title="data.label">{{ data.label }}</div>
                  <div v-if="data.path" class="detail-tree-path" :title="data.path">{{ data.path }}</div>
                </div>
              </template>
            </el-tree>
          </el-scrollbar>
        </div>
      </el-card>

      <el-card class="panel-card detail-preview-card">
        <template #header>
          <span class="panel-title-with-icon">
            <el-icon><Files /></el-icon>
            <span>正文预览</span>
          </span>
        </template>
        <div class="preview-header">
          <div class="preview-title" :title="previewTitle">{{ previewTitle }}</div>
          <div class="placeholder-note">预览长度会随目录标题宽度自动调整</div>
        </div>
        <el-scrollbar max-height="420px">
          <div v-if="previewChunks.length" class="preview-stack">
            <article v-for="chunk in previewChunks" :key="chunk.id" class="preview-block">
              <div class="preview-block-head">
                <span>知识单元 {{ chunk.chunk_index }}</span>
                <span v-if="chunk.chapterPath" class="placeholder-note" :title="chunk.chapterPath">
                  {{ ellipsisText(chunk.chapterPath, 28) }}
                </span>
              </div>
              <pre class="preview-text">{{ chunk.preview }}</pre>
            </article>
          </div>
          <pre v-else class="preview-text">{{ content || '暂无正文内容' }}</pre>
        </el-scrollbar>
      </el-card>
    </div>

    <el-card class="panel-card">
      <template #header>
        <span class="panel-title-with-icon">
          <el-icon><MagicStick /></el-icon>
          <span>知识单元列表</span>
        </span>
      </template>
      <el-empty v-if="!chunks.length" description="暂无知识单元" />
      <el-collapse v-else>
        <el-collapse-item v-for="chunk in normalizedChunks" :key="chunk.id" :name="chunk.id">
          <template #title>
            <span>
              单元 {{ chunk.chunk_index }}
              <span v-if="chunk.chapterPath" class="placeholder-note"> · {{ ellipsisText(chunk.chapterPath, 32) }}</span>
            </span>
          </template>
          <div class="section-stack">
            <pre style="white-space: pre-wrap; margin: 0">{{ chunk.content }}</pre>
            <el-card shadow="never" class="panel-card">
              <template #header>元数据</template>
              <pre style="white-space: pre-wrap; margin: 0">{{ JSON.stringify(chunk.metadata || {}, null, 2) }}</pre>
            </el-card>
            <el-space>
              <el-button size="small" :icon="EditPen" @click="openEdit(chunk)">编辑</el-button>
              <el-button size="small" type="danger" :icon="Delete" @click="deleteChunk(chunk)">删除</el-button>
            </el-space>
          </div>
        </el-collapse-item>
      </el-collapse>
    </el-card>

    <el-dialog v-model="editVisible" title="编辑知识单元" width="720px">
      <el-form label-position="top">
        <el-form-item label="单元内容">
          <el-input v-model="editForm.content" type="textarea" :rows="8" />
        </el-form-item>
        <el-form-item label="元数据 JSON">
          <el-input v-model="editForm.metadataText" type="textarea" :rows="8" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" @click="saveChunk">保存</el-button>
      </template>
    </el-dialog>
  </section>
</template>
