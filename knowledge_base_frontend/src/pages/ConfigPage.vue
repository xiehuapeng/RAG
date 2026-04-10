<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { CirclePlus, Cpu, EditPen, MagicStick, RefreshRight } from '@element-plus/icons-vue'
import { configApi } from '../api/service'
import { formatDateTime } from '../utils/datetime'

const loading = ref(false)
const saving = ref(false)
const models = ref([])
const editingId = ref(null)
const form = reactive({
  model_type: 'embedding',
  model_name: '',
  config_text: '{\n  "device": "cpu"\n}',
  is_active: true,
})

async function loadModels() {
  loading.value = true
  try {
    models.value = await configApi.list()
  } finally {
    loading.value = false
  }
}

function pickRow(row) {
  editingId.value = row.id
  form.model_type = row.model_type
  form.model_name = row.model_name
  form.config_text = JSON.stringify(row.config || {}, null, 2)
  form.is_active = !!row.is_active
}

function resetForm() {
  editingId.value = null
  form.model_type = 'embedding'
  form.model_name = ''
  form.config_text = '{\n  "device": "cpu"\n}'
  form.is_active = true
}

async function saveConfig() {
  saving.value = true
  try {
    const payload = {
      model_type: form.model_type,
      model_name: form.model_name,
      config: JSON.parse(form.config_text || '{}'),
      is_active: form.is_active,
    }
    if (editingId.value) {
      await configApi.update(editingId.value, payload)
      ElMessage.success('配置已更新')
    } else {
      await configApi.create(payload)
      ElMessage.success('配置已新增')
    }
    resetForm()
    await loadModels()
  } finally {
    saving.value = false
  }
}

onMounted(loadModels)
</script>

<template>
  <section class="section-stack">
    <header class="chat-hero">
      <div>
        <p class="chat-hero-copy">
          统一管理 Embedding、Rerank 和问答模型配置，保持检索与生成能力一致。
        </p>
      </div>
      <div class="chat-hero-actions">
        <div class="chat-hero-chip">
          <span class="status-dot"></span>
          {{ models.length }} 个配置项
        </div>
        <el-button :icon="CirclePlus" @click="resetForm">新建配置</el-button>
      </div>
    </header>

    <div class="two-col">
      <el-card class="panel-card" v-loading="loading">
        <template #header>
          <span class="panel-title-with-icon">
            <el-icon><Cpu /></el-icon>
            <span>配置列表</span>
          </span>
        </template>
        <el-empty v-if="!models.length" description="暂无模型配置" />
        <el-table v-else :data="models">
          <el-table-column prop="model_type" label="类型" width="120" />
          <el-table-column prop="model_name" label="模型名称" min-width="220" show-overflow-tooltip />
          <el-table-column label="启用状态" width="100">
            <template #default="{ row }">
              <el-tag :type="row.is_active ? 'success' : 'info'">
                {{ row.is_active ? '启用' : '停用' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="更新时间" width="180">
            <template #default="{ row }">
              {{ formatDateTime(row.updated_at) }}
            </template>
          </el-table-column>
          <el-table-column label="操作" width="100">
            <template #default="{ row }">
              <el-button link type="primary" :icon="EditPen" @click="pickRow(row)">编辑</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <el-card class="panel-card">
        <template #header>
          <span class="panel-title-with-icon">
            <el-icon><MagicStick /></el-icon>
            <span>{{ editingId ? '编辑配置' : '新建配置' }}</span>
          </span>
        </template>
        <el-form label-position="top" class="config-form">
          <el-form-item label="模型类型">
            <el-select v-model="form.model_type">
              <el-option label="Embedding" value="embedding" />
              <el-option label="Rerank" value="rerank" />
              <el-option label="Chat" value="chat" />
            </el-select>
          </el-form-item>
          <el-form-item label="模型名称">
            <el-input v-model="form.model_name" placeholder="请输入模型名称" />
          </el-form-item>
          <el-form-item label="配置 JSON">
            <el-input v-model="form.config_text" type="textarea" :rows="10" />
          </el-form-item>
          <el-form-item label="是否启用">
            <el-switch v-model="form.is_active" />
          </el-form-item>
          <el-space>
            <el-button :icon="RefreshRight" @click="resetForm">重置</el-button>
            <el-button type="primary" :icon="EditPen" :loading="saving" @click="saveConfig">保存配置</el-button>
          </el-space>
        </el-form>
      </el-card>
    </div>
  </section>
</template>
