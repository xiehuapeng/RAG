# 前端设计方案

## 1. 设计目标

本方案基于《数智化运营知识库_功能设计页面元素.html》整理，目标是设计一套基于 `Vue3 + JavaScript + Element Plus` 的后台前端页面。

设计重点：

- 简洁清晰
- 蓝白主调
- 适合运营后台长期使用
- 操作提示明确、友好
- 便于后续按页面快速开发和联调

## 2. 页面总览

建议前端拆分为 7 个主页面：

1. 登录页
2. 首页 / 总览页
3. 文档管理页
4. 文档详情 / 切片维护页
5. 智能问答页
6. 运营看板页
7. 模型配置页

## 3. 视觉风格

### 3.1 颜色

- 主色：蓝色
- 辅助色：浅蓝、灰白、成功绿、警告橙、错误红
- 页面背景：浅灰白
- 卡片背景：纯白

推荐色值：

- `#2F6BFF`：主按钮、链接、选中态
- `#5B8FF9`：辅助强调
- `#EAF2FF`：浅蓝背景
- `#F5F7FA`：页面背景
- `#1F2D3D`：主标题文字
- `#606266`：正文文字

### 3.2 风格原则

- 大量留白，减少压迫感
- 卡片化布局，突出模块边界
- 状态信息统一用标签颜色区分
- 长文本区域支持折叠、展开、复制
- 危险操作必须二次确认

### 3.3 交互原则

- 所有提交类操作都有 loading 状态
- 成功提示短而明确
- 失败提示说明原因，不只说“失败”
- 空状态必须有引导文案
- 上传、解析、重建索引等耗时操作要显示进度或处理中状态

## 4. 技术方案

### 4.1 技术栈

- 框架：Vue 3
- 语言：JavaScript
- UI 库：Element Plus
- 路由：Vue Router
- 状态管理：Pinia
- 请求库：Axios
- 图表：ECharts

### 4.2 工程结构

建议采用以下目录结构：

```text
src/
├─ assets/
├─ components/
├─ layouts/
├─ pages/
├─ router/
├─ store/
├─ api/
├─ utils/
└─ App.vue
```

建议额外准备：

- 请求拦截器
- 权限路由守卫
- 全局 loading
- 全局错误提示
- 空状态组件

## 5. 路由与菜单

### 5.1 路由建议

- `/login`
- `/home`
- `/documents`
- `/documents/:id`
- `/documents/:id/chunks`
- `/chat`
- `/dashboard`
- `/config`

### 5.2 菜单建议

左侧菜单建议按业务分组：

- 首页
- 文档管理
- 智能问答
- 运营看板
- 模型配置

如果后续要细分权限，可以再加：

- 普通运营菜单
- 管理员菜单

## 6. 页面设计

### 6.1 登录页

#### 目标

提供简洁、低干扰的登录入口，强调管理后台属性。

#### 布局

- 左侧：品牌区，展示系统名称和简介
- 右侧：登录卡片，包含账号、密码、记住我、登录按钮

#### 组件

- `el-card`
- `el-form`
- `el-form-item`
- `el-input`
- `el-checkbox`
- `el-button`
- `el-alert`

#### 交互

- 支持回车登录
- 登录按钮显示 loading
- 账号或密码错误时提示明确原因
- 无权限时提示“当前账号无访问权限”

### 6.2 首页 / 总览页

#### 目标

作为登录后的入口页，用于快速查看系统状态和常用入口。

#### 布局

- 顶部：页面标题、用户信息、退出登录
- 中部：统计卡片
- 下部：快捷入口和最近动态

#### 统计卡片

- 文档总数
- 切片总数
- 提问总数
- 有效回答率

#### 组件

- `el-row`
- `el-col`
- `el-card`
- `el-tag`
- `el-empty`
- `el-button`

### 6.3 文档管理页

#### 目标

支持文档上传、批量上传、查重、筛选、删除和重新解析。

#### 布局

- 顶部：筛选区
- 中部：文档列表表格
- 右上角：上传入口
- 底部：分页器

#### 筛选项

- 关键字搜索
- 状态筛选
- 类型筛选
- 时间范围筛选

#### 列表字段

- 文档标题
- 文件名
- 文件类型
- 文件大小
- 状态
- 切片数量
- 创建时间
- 操作

#### 操作

- 查看详情
- 查看切片
- 查看原文
- 删除
- 重新解析
- 重建索引

#### 组件

- `el-form`
- `el-input`
- `el-select`
- `el-date-picker`
- `el-table`
- `el-table-column`
- `el-pagination`
- `el-dialog`
- `el-upload`
- `el-button`
- `el-popconfirm`

### 6.4 文档详情 / 切片维护页

#### 目标

展示文档解析结果、目录树、切片内容，并支持切片维护。

#### 布局

- 顶部：文档基本信息
- 中部：解析状态与进度
- 左侧或上部：目录树
- 右侧或下部：切片列表

#### 内容区域

- 文档原文查看
- 章节树或兼容树
- 切片列表
- 切片编辑
- 切片删除
- 切片重排或重建

#### 组件

- `el-descriptions`
- `el-tree`
- `el-table`
- `el-collapse`
- `el-dialog`
- `el-drawer`
- `el-input`
- `el-tag`
- `el-tooltip`
- `el-button`

#### 说明

当前后端的目录树接口是兼容展示，若原始文档没有明确章节信息，会退化为按切片序号展示。

### 6.5 智能问答页

#### 目标

提供问答会话、消息发送、引用来源展示和反馈功能。

#### 布局

- 左侧：会话列表
- 中间：对话区
- 右侧：引用来源、推荐问题、命中片段

#### 组件

- `el-aside`
- `el-main`
- `el-scrollbar`
- `el-input`
- `el-button`
- `el-avatar`
- `el-card`
- `el-collapse`
- `el-drawer`
- `el-tag`

#### 交互

- 回车发送，`Shift + Enter` 换行
- 发送后按钮显示 loading
- 回答生成中提示“正在检索相关知识，请稍候”
- 引用来源支持点击跳转到文档详情
- 支持“有用 / 无用”反馈和补充意见

#### 人性化提示

- “你可以直接输入业务问题，也可以从推荐问题中点选。”
- “当前回答基于知识库检索结果生成。”
- “如果命中不足，建议补充更具体的场景或对象。”

### 6.6 运营看板页

#### 目标

面向管理人员展示系统使用情况、问答效果和文档质量。

#### 建议指标

- 提问总量
- 文档总量
- 切片总量
- 有效回答率
- 无答案问题数量
- 用户反馈满意率

#### 图表建议

- 折线图：提问趋势
- 柱状图：高频问题 Top N
- 环图：反馈分布
- 表格：最近反馈明细

#### 组件

- `el-card`
- `el-table`
- `el-progress`
- `el-tag`
- `el-row`
- `el-col`

### 6.7 模型配置页

#### 目标

管理检索和回答相关模型配置，方便后续扩展。

#### 字段

- 模型类型
- 模型名称
- 配置内容
- 是否启用
- 更新时间

#### 组件

- `el-table`
- `el-form`
- `el-input`
- `el-switch`
- `el-select`
- `el-dialog`

## 7. 组件拆分

建议把公共能力拆成以下基础组件：

- `AppLayout`：整体布局框架
- `SidebarMenu`：侧边栏导航
- `PageHeader`：标题和操作区
- `StatCard`：统计卡片
- `StatusTag`：状态标签
- `UploadPanel`：上传面板
- `ChunkList`：切片列表
- `OutlineTree`：目录树
- `ChatWindow`：问答窗口
- `ReferencePanel`：引用来源面板
- `EmptyState`：空状态组件
- `ConfirmAction`：危险操作确认封装

## 8. 页面状态规范

### 8.1 通用状态

所有页面建议统一以下状态：

- `loading`
- `empty`
- `error`
- `no_permission`
- `success`

### 8.2 反馈规范

- 成功：短 toast
- 失败：说明原因和处理建议
- 无权限：明确提示当前账号没有权限
- 空数据：带引导文案

### 8.3 危险操作

包括：

- 删除文档
- 删除切片
- 重建索引
- 覆盖上传

这些操作都应有二次确认。

## 9. API 对照

### 9.1 认证

- `POST /api/auth/login`
- `POST /api/auth/logout`
- `POST /api/auth/me`

### 9.2 文档

- `POST /api/documents/upload`
- `POST /api/documents/upload/check`
- `POST /api/documents/upload/batch`
- `POST /api/documents`
- `POST /api/documents/{document_id}`
- `POST /api/documents/{document_id}/delete`
- `POST /api/documents/{document_id}/chunks`
- `POST /api/documents/{document_id}/content`
- `POST /api/documents/{document_id}/outline`
- `POST /api/documents/{document_id}/reindex`
- `POST /api/documents/{document_id}/resplit`
- `POST /api/documents/{document_id}/chunks/{chunk_id}/update`
- `POST /api/documents/{document_id}/chunks/{chunk_id}/delete`

### 9.3 问答

- `POST /api/chat/sessions`
- `POST /api/chat/sessions/create`
- `POST /api/chat/sessions/{session_id}`
- `POST /api/chat/sessions/{session_id}/messages`
- `POST /api/chat/messages/{message_id}/feedback`
- `POST /api/chat/popular-questions`

### 9.4 运营看板

- `POST /api/dashboard/stats`
- `POST /api/dashboard/top-questions`
- `POST /api/dashboard/no-answer`
- `POST /api/dashboard/feedback`
- `POST /api/dashboard/ask-trend`
- `POST /api/dashboard/upload-trend`
- `POST /api/dashboard/recent-feedback`

### 9.5 配置

- `POST /api/config/models`
- `POST /api/config/models/{model_id}`

## 10. 请求约定

- 统一请求头：`x-session-token`
- 统一返回结构：`code`、`message`、`data`
- 统一错误处理：将后端业务错误转换为前端提示
- 统一 loading：列表、上传、发送消息、保存配置都需要 loading

## 11. 前后端对齐建议

### 11.1 文档上传

前端应支持：

- 单文件上传
- 批量上传
- 同名覆盖确认
- 上传进度提示

### 11.2 文档详情

前端应支持：

- 原文查看
- 目录树
- 切片列表
- 切片编辑
- 切片删除
- 重建索引

### 11.3 问答页

前端应支持：

- 会话列表
- 会话详情
- 推荐问题
- 引用来源
- 反馈和补充意见

### 11.4 看板页

前端应支持：

- 提问趋势
- 上传趋势
- 高频问题
- 无答案问题
- 最近反馈明细

## 12. 开发优先级

建议开发顺序：

1. 登录页
2. 主布局和菜单
3. 文档管理页
4. 文档详情页
5. 问答页
6. 运营看板页
7. 模型配置页

原因：

- 登录和布局是基础
- 文档管理和问答是核心业务
- 看板和配置是增强能力

## 13. 结论

这版前端方案的目标是：

- 让页面结构更清晰
- 让组件拆分更明确
- 让前后端接口更容易对齐
- 让页面状态更统一
- 让后续实现更直接

如果继续推进，下一步建议直接输出：

1. Vue3 项目骨架
2. 路由和菜单配置
3. API 封装层
4. 登录页和主布局
