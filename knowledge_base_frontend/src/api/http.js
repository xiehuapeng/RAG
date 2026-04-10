import axios from 'axios'
import { ElMessage } from 'element-plus'

export const API_BASE_URL = import.meta.env.DEV ? 'http://127.0.0.1:8000' : ''

const http = axios.create({
  baseURL: API_BASE_URL || undefined,
})

http.interceptors.request.use((config) => {
  // 统一在请求头里补上会话 token。
  // 这样后端可以用同一套鉴权方式处理文档、问答和配置接口。
  const token = localStorage.getItem('kb-session-token')
  if (token) {
    config.headers['x-session-token'] = token
  }
  return config
})

http.interceptors.response.use(
  (response) => response,
  (error) => {
    // 这里把后端错误统一转成前端可读的提示。
    // 401 单独处理：清理本地登录态并跳回登录页。
    const message =
      error?.response?.data?.message ||
      error?.message ||
      '请求失败，请稍后重试'

    if (error?.response?.status === 401) {
      localStorage.removeItem('kb-session-token')
      localStorage.removeItem('kb-session-user')
      window.location.href = '/login'
    } else {
      ElMessage.error(message)
    }
    return Promise.reject(error)
  },
)

export async function post(path, payload, config = {}) {
  // 业务接口约定统一返回 { code, message, data }。
  // 这里把“接口层判断成功/失败”的逻辑集中起来，页面只关心 data。
  const response = await http.post(path, payload, config)
  const body = response.data
  if (body.code !== 0) {
    throw new Error(body.message || '接口返回失败')
  }
  return body.data
}

export default http
