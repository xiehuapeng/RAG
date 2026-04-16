<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CircleCheck, Clock, Delete, Document, Loading, RefreshRight, Search, Upload, Warning } from '@element-plus/icons-vue'
import { MAX_UPLOAD_FILE_COUNT, MAX_UPLOAD_SIZE, documentApi } from '../api/service'
import { formatDateTime } from '../utils/datetime'

const router = useRouter()
const loading = ref(false)
const uploading = ref(false)
const uploadVisible = ref(false)
const uploadRef = ref(null)
const uploadFileList = ref([])
const tableData = ref([])
const total = ref(0)
const uploadForm = reactive({
  title: '',
  files: [],
  overwrite: false,
})
const SUPPORTED_UPLOAD_EXTENSIONS = ['txt', 'md', 'json', 'csv', 'docx', 'pdf', 'xls', 'xlsx']
const SUPPORTED_UPLOAD_ACCEPT = SUPPORTED_UPLOAD_EXTENSIONS.map((item) => `.${item}`).join(',')
const filters = reactive({
  keyword: '',
  status: '',
  page_num: 1,
  page_size: 10,
})

function formatSize(bytes) {
  return `${Math.round(bytes / 1024 / 1024)}MB`
}

function getFileExtension(fileName = '') {
  const index = fileName.lastIndexOf('.')
  return index >= 0 ? fileName.slice(index + 1).toLowerCase() : ''
}

async function loadDocuments() {
  loading.value = true
  try {
    const data = await documentApi.list(filters)
    tableData.value = data.rows
    total.value = data.total
  } finally {
    loading.value = false
  }
}

function validateUploadFile(file, showMessage = true) {
  const extension = getFileExtension(file.name)
  if (!SUPPORTED_UPLOAD_EXTENSIONS.includes(extension)) {
    if (showMessage) {
      ElMessage.error(`暂不支持 ${extension ? `.${extension}` : '无扩展名'} 文件，请上传 txt、md、json、csv、docx、pdf、xls、xlsx 文件`)
    }
    return false
  }
  if (file.size > MAX_UPLOAD_SIZE) {
    if (showMessage) {
      ElMessage.error(`文件大小不能超过 200MB，当前文件为 ${formatSize(file.size)}`)
    }
    return false
  }
  return true
}

function syncUploadFiles(uploadFiles) {
  uploadForm.files = uploadFiles.map((item) => item.raw).filter(Boolean)
}

function handleFileChange(uploadFile, uploadFiles) {
  if (uploadFiles.length > MAX_UPLOAD_FILE_COUNT) {
    ElMessage.error(`一次最多上传 ${MAX_UPLOAD_FILE_COUNT} 个文件`)
    uploadRef.value?.handleRemove(uploadFile)
    uploadFileList.value = uploadFiles.filter((item) => item.uid !== uploadFile.uid)
    syncUploadFiles(uploadFileList.value)
    return
  }
  if (uploadFile?.raw && !validateUploadFile(uploadFile.raw)) {
    uploadRef.value?.handleRemove(uploadFile)
    uploadFileList.value = uploadFiles.filter((item) => item.uid !== uploadFile.uid)
    syncUploadFiles(uploadFileList.value)
    return
  }
  syncUploadFiles(uploadFiles)
}

function handleFileRemove(uploadFile, uploadFiles) {
  syncUploadFiles(uploadFiles)
}

async function handleUpload() {
  if (!uploadForm.files.length) {
    ElMessage.warning('请先选择文件')
    return
  }
  if (uploadForm.files.length > MAX_UPLOAD_FILE_COUNT) {
    ElMessage.error(`一次最多上传 ${MAX_UPLOAD_FILE_COUNT} 个文件`)
    return
  }
  const invalidFile = uploadForm.files.find((file) => !validateUploadFile(file, false))
  if (invalidFile) {
    validateUploadFile(invalidFile, true)
    return
  }
  uploading.value = true
  try {
    if (!uploadForm.overwrite) {
      for (const file of uploadForm.files) {
        const check = await documentApi.checkUpload(file.name)
        if (check.exists) {
          ElMessage.warning(`已存在同名文档“${file.name}”，如需覆盖请勾选覆盖上传`)
          return
        }
      }
    }

    if (uploadForm.files.length === 1) {
      await documentApi.upload({ file: uploadForm.files[0], title: uploadForm.title, overwrite: uploadForm.overwrite })
      ElMessage.success('文档上传成功')
    } else {
      const results = await documentApi.uploadBatch({ files: uploadForm.files, overwrite: uploadForm.overwrite })
      const successCount = results.filter((item) => item.status !== 'error').length
      const failCount = results.length - successCount
      if (failCount) {
        ElMessage.warning(`批量上传完成，成功 ${successCount} 个，失败 ${failCount} 个`)
      } else {
        ElMessage.success(`批量上传成功，共 ${successCount} 个文件`)
      }
    }
    uploadVisible.value = false
    uploadForm.title = ''
    uploadForm.files = []
    uploadFileList.value = []
    uploadForm.overwrite = false
    uploadRef.value?.clearFiles()
    await loadDocuments()
  } finally {
    uploading.value = false
  }
}

async function handleDelete(row) {
  await ElMessageBox.confirm(`确认删除文档“${row.title}”吗？`, '删除确认', { type: 'warning' })
  await documentApi.remove(row.id)
  ElMessage.success('文档已删除')
  await loadDocuments()
}

async function handleReindex(row) {
  await documentApi.reindex(row.id)
  ElMessage.success('已触发重建索引')
  await loadDocuments()
}

function goDetail(row) {
  router.push(`/documents/${row.id}`)
}

function resetFilters() {
  filters.keyword = ''
  filters.status = ''
  filters.page_num = 1
  loadDocuments()
}

onMounted(loadDocuments)
</script>

<template>
  <section class="section-stack" v-loading="loading">
    <header class="chat-hero">
      <div>
        <p class="chat-hero-copy">
          统一查看、上传、筛选、重建索引和删除文档。这里是知识库内容治理的入口。
        </p>
      </div>
      <div class="chat-hero-actions">
        <div class="chat-hero-chip">
          <span class="status-dot"></span>
          {{ total }} 条文档记录
        </div>
        <el-button type="primary" :icon="Upload" @click="uploadVisible = true">上传文档</el-button>
      </div>
    </header>

    <el-card class="panel-card">
      <template #header>
        <span class="panel-title-with-icon">
          <el-icon><Search /></el-icon>
          <span>筛选与搜索</span>
        </span>
      </template>
      <el-form class="document-filter-bar" inline>
        <el-form-item>
          <el-input v-model="filters.keyword" :prefix-icon="Search" placeholder="搜索标题或文件名" style="width: 260px" clearable />
        </el-form-item>
        <el-form-item>
          <el-select v-model="filters.status" placeholder="文档状态" style="width: 160px" clearable>
            <el-option label="待处理" value="pending" />
            <el-option label="处理中" value="processing" />
            <el-option label="已完成" value="ready" />
            <el-option label="失败" value="error" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Search" @click="filters.page_num = 1; loadDocuments()">查询</el-button>
          <el-button :icon="RefreshRight" @click="resetFilters">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card class="panel-card">
      <template #header>
        <span class="panel-title-with-icon">
          <el-icon><Document /></el-icon>
          <span>文档列表</span>
        </span>
      </template>
      <el-empty v-if="!tableData.length && !loading" description="暂无文档数据" />
      <el-table v-else :data="tableData" v-loading="loading">
        <el-table-column prop="title" label="文档标题" min-width="220" show-overflow-tooltip />
        <el-table-column prop="file_name" label="文件名" min-width="220" show-overflow-tooltip />
        <el-table-column prop="file_type" label="类型" width="100" />
        <el-table-column prop="status" label="状态" width="120">
          <template #default="{ row }">
            <el-tag
              :type="row.status === 'ready' ? 'success' : row.status === 'processing' ? 'warning' : row.status === 'error' ? 'danger' : 'info'"
            >
              <span class="status-tag-with-icon">
                <el-icon v-if="row.status === 'ready'"><CircleCheck /></el-icon>
                <el-icon v-else-if="row.status === 'processing'" class="is-loading"><Loading /></el-icon>
                <el-icon v-else-if="row.status === 'error'"><Warning /></el-icon>
                <el-icon v-else><Clock /></el-icon>
                <span>{{ row.status }}</span>
              </span>
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="chunk_count" label="切片数" width="100" />
        <el-table-column label="创建时间" width="180">
          <template #default="{ row }">
            {{ formatDateTime(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="260" fixed="right">
          <template #default="{ row }">
            <el-space>
              <el-button link type="primary" :icon="Document" @click="goDetail(row)">详情</el-button>
              <el-button link :icon="RefreshRight" @click="handleReindex(row)">重建索引</el-button>
              <el-button link type="danger" :icon="Delete" @click="handleDelete(row)">删除</el-button>
            </el-space>
          </template>
        </el-table-column>
      </el-table>

      <div style="display: flex; justify-content: flex-end; margin-top: 16px">
        <el-pagination
          v-model:current-page="filters.page_num"
          v-model:page-size="filters.page_size"
          background
          layout="total, prev, pager, next, sizes"
          :total="total"
          @current-change="loadDocuments"
          @size-change="filters.page_num = 1; loadDocuments()"
        />
      </div>
    </el-card>

    <el-dialog v-model="uploadVisible" title="上传文档" width="560px">
      <el-form label-position="top" class="document-upload-form">
        <el-form-item label="文档标题">
          <el-input v-model="uploadForm.title" placeholder="可选，不填则使用文件名" />
        </el-form-item>
        <el-form-item label="选择文件">
          <el-upload
            ref="uploadRef"
            v-model:file-list="uploadFileList"
            :auto-upload="false"
            :accept="SUPPORTED_UPLOAD_ACCEPT"
            multiple
            :on-change="handleFileChange"
            :on-remove="handleFileRemove"
            :show-file-list="true"
          >
            <el-button type="primary" :icon="Upload">选择文件</el-button>
          </el-upload>
        </el-form-item>
        <el-form-item>
          <el-checkbox v-model="uploadForm.overwrite">若存在同名文档则覆盖</el-checkbox>
        </el-form-item>
        <div class="placeholder-note">支持 txt、md、json、csv、docx、pdf、xls、xlsx；一次最多 20 个文件，单个文件大小不能超过 200MB。</div>
      </el-form>
      <template #footer>
        <el-button @click="uploadVisible = false">取消</el-button>
        <el-button type="primary" :icon="Upload" :loading="uploading" @click="handleUpload">开始上传</el-button>
      </template>
    </el-dialog>
  </section>
</template>
