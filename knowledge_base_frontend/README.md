# Knowledge Base Frontend

这是当前知识库项目的前端控制台，基于 `Vue 3 + Vite + Element Plus + Pinia + Vue Router`，面向文档治理、问答交互和运营观察。

## 当前页面能力

- `HomePage.vue`：知识工作台首页，展示文档量、切片量、检索量、反馈率、高频问题和近期反馈。
- `DocumentsPage.vue`：文档列表、筛选、单个/批量上传、重建索引、删除。
- `DocumentDetailPage.vue`：目录树、正文预览、知识单元列表，以及单个 chunk 的编辑/删除。
- `ChatPage.vue`：历史会话、批量删除、流式问答、过程进度条、引用来源面板、推荐追问、回答反馈。
- `DashboardPage.vue`：提问趋势、上传趋势、高频问题、无答案问题、最近反馈。
- `ConfigPage.vue`：模型配置项的新增和编辑。
- `LoginPage.vue`：基于后端 session token 的登录页。

## 路由结构

- `/login`
- `/home`
- `/documents`
- `/documents/:id`
- `/chat`
- `/dashboard`
- `/config`

## 前后端协作方式

- 登录态保存在 `localStorage`，键名为 `kb-session-token` 和 `kb-session-user`。
- 常规接口通过 `axios` 调用，统一封装在 `src/api/service.js`。
- 流式问答使用 `fetch + text/event-stream`，前端会消费 `start / progress / understanding / evidence / delta / end / error` 等事件。
- 默认后端地址来自 `VITE_API_BASE_URL`；未配置时走同源地址。
- Vite 开发服务器会把 `/api` 代理到 `http://127.0.0.1:8000`。
- 生产构建输出到 `knowledge_base_backend/static`，由 FastAPI 托管并提供 SPA 路由兜底。

## 问答页面流程

1. 加载或创建会话；批量管理模式支持全选和批量删除。
2. 提交问题后消费后端 SSE 事件，展示问题理解、检索、过滤和回答生成进度。
3. 流式增量渲染回答，并在结束事件中更新引用、推荐追问和会话标题。
4. 用户可查看引用详情并提交正向或负向反馈。

当前前端只展示后端已经实现的进度步骤。设计文档中的 `expanding` 独立扩展步骤尚未接入。

## 本地启动

```powershell
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

## 构建

```powershell
npm run build
npm run preview
```

`npm run build` 会直接更新后端的 `static/` 目录；该目录属于本地构建产物，通常不提交到 Git。
