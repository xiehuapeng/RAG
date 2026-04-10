import http, { API_BASE_URL, post } from './http'

export const MAX_UPLOAD_SIZE = 200 * 1024 * 1024

function mapChatErrorMessage(code, fallbackMessage = '') {
  // 后端在流式问答里通常会返回结构化错误码。
  // 这里把错误码映射成更适合给用户看的中文提示，避免直接暴露内部实现细节。
  const mapped = {
    session_not_found: '当前会话不存在，请刷新页面后重试。',
    auth_error: '登录状态已失效，请重新登录。',
    timeout: '回答生成时间过长，请稍后重试或缩短问题内容。',
    network_error: '模型服务暂时不可用，请稍后重试。',
    config_error: '模型配置不完整，请联系管理员检查配置。',
    chat_failed: '本次回答生成失败，请稍后重试。',
  }
  return mapped[code] || fallbackMessage || '发送消息失败'
}

export const authApi = {
  login(payload) {
    return post('/api/auth/login', payload)
  },
  logout() {
    return post('/api/auth/logout')
  },
  me() {
    return post('/api/auth/me')
  },
}

export const documentApi = {
  list(payload) {
    // 文档列表接口用于“文档管理”页的分页查询。
    // 这里手动把后端返回整理成 table 组件更容易消费的结构。
    return http.post('/api/documents', payload).then((response) => {
      const body = response.data
      if (body.code !== 0) {
        throw new Error(body.message || '获取文档列表失败')
      }
      return {
        rows: body.data || [],
        total: body.total || 0,
        page_num: body.page_num || payload.page_num,
        page_size: body.page_size || payload.page_size,
      }
    })
  },
  detail(id) {
    return post(`/api/documents/${id}`)
  },
  chunks(id) {
    return post(`/api/documents/${id}/chunks`)
  },
  content(id) {
    return post(`/api/documents/${id}/content`)
  },
  outline(id) {
    return post(`/api/documents/${id}/outline`)
  },
  reindex(id) {
    return post(`/api/documents/${id}/reindex`)
  },
  resplit(id) {
    return post(`/api/documents/${id}/resplit`)
  },
  remove(id) {
    return post(`/api/documents/${id}/delete`)
  },
  updateChunk(documentId, chunkId, payload) {
    return post(`/api/documents/${documentId}/chunks/${chunkId}/update`, payload)
  },
  deleteChunk(documentId, chunkId) {
    return post(`/api/documents/${documentId}/chunks/${chunkId}/delete`)
  },
  checkUpload(fileName) {
    return post('/api/documents/upload/check', { file_name: fileName })
  },
  upload({ file, title = '', overwrite = false }) {
    // 上传文档时使用 multipart/form-data。
    // file 是原始文件对象，title 和 overwrite 则是附加字段。
    const formData = new FormData()
    formData.append('file', file)
    formData.append('title', title)
    formData.append('overwrite', String(overwrite))
    return http
      .post('/api/documents/upload', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      .then((response) => {
        if (response.data.code !== 0) {
          throw new Error(response.data.message || '上传失败')
        }
        return response.data.data
      })
  },
}

export const chatApi = {
  sessions() {
    // 获取当前账号下的历史会话列表。
    return post('/api/chat/sessions')
  },
  createSession(title) {
    // 创建一个新的会话，用于承接后续多轮问答。
    return post('/api/chat/sessions/create', { title })
  },
  sessionDetail(id) {
    // 拉取指定会话的完整消息记录。
    return post(`/api/chat/sessions/${id}`)
  },
  deleteSession(id) {
    return post(`/api/chat/sessions/${id}/delete`)
  },
  sendMessage(sessionId, content) {
    // 非流式发送接口，保留给后端或其它场景复用。
    return post(`/api/chat/sessions/${sessionId}/messages`, { content })
  },
  async streamMessage(sessionId, content, handlers = {}) {
    // 问答主流程使用 SSE 流式响应：
    // 1. 前端先发起 POST 请求
    // 2. 后端持续推送 status / evidence / delta / end / error 等事件
    // 3. 前端边接收边渲染，用户会看到“先检索，再逐步生成答案”的过程
    const token = localStorage.getItem('kb-session-token')
    const response = await fetch(`${API_BASE_URL}/api/chat/sessions/${sessionId}/messages/stream`, {
      method: 'POST',
      headers: {
        Accept: 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Content-Type': 'application/json',
        ...(token ? { 'x-session-token': token } : {}),
      },
      body: JSON.stringify({ content }),
    })

    if (!response.ok) {
      let message = '发送消息失败'
      try {
        const body = await response.json()
        message = body?.message || message
      } catch {
        message = response.statusText || message
      }
      throw new Error(message)
    }

    if (!response.body) {
      throw new Error('当前浏览器不支持流式响应')
    }

    // 下面是一个简化版 SSE 解析器。
    // 后端每次发送的数据块通常以空行分隔，单个块里可能包含 event 和多行 data。
    const reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''

    const emit = (eventName, payload) => {
      const handler = handlers[eventName]
      if (typeof handler === 'function') {
        handler(payload)
      }
    }

    while (true) {
      const { value, done } = await reader.read()
      if (done) {
        break
      }

      buffer += decoder.decode(value, { stream: true })
      const chunks = buffer.split('\n\n')
      buffer = chunks.pop() || ''

      for (const chunk of chunks) {
        const lines = chunk.split('\n')
        let eventName = 'message'
        const dataLines = []

        for (const line of lines) {
          if (line.startsWith('event:')) {
            eventName = line.slice(6).trim()
          } else if (line.startsWith('data:')) {
            dataLines.push(line.slice(5).trim())
          }
        }

        if (!dataLines.length) {
          continue
        }

        const raw = dataLines.join('\n')
        let payload = raw
        try {
          // 优先尝试按 JSON 解析；如果后端发的是纯文本，也不强制报错。
          payload = JSON.parse(raw)
        } catch {
          payload = raw
        }
        if (eventName === 'error' && payload && typeof payload === 'object') {
          payload.message = mapChatErrorMessage(payload.code, payload.message)
        }
        emit(eventName, payload)
      }
    }
  },
  popularQuestions() {
    // 首页/问答页右侧的推荐问题，由后端基于热度或统计结果生成。
    return post('/api/chat/popular-questions')
  },
  feedback(messageId, payload) {
    // 对回答“有帮助/没帮助”的反馈，会写回后端做运营分析和质量优化。
    return post(`/api/chat/messages/${messageId}/feedback`, payload)
  },
}

export const dashboardApi = {
  stats() {
    return post('/api/dashboard/stats')
  },
  topQuestions() {
    return post('/api/dashboard/top-questions')
  },
  noAnswer() {
    return post('/api/dashboard/no-answer')
  },
  feedback() {
    return post('/api/dashboard/feedback')
  },
  askTrend(days) {
    return post('/api/dashboard/ask-trend', { days })
  },
  uploadTrend(days) {
    return post('/api/dashboard/upload-trend', { days })
  },
  recentFeedback() {
    return post('/api/dashboard/recent-feedback')
  },
}

export const configApi = {
  list() {
    return post('/api/config/models', null)
  },
  create(payload) {
    return post('/api/config/models/create', payload)
  },
  update(id, payload) {
    return post(`/api/config/models/${id}`, payload)
  },
}
