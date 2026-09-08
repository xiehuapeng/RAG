# CMIOT RAG

CMIOT RAG 是一套前后端分离的知识库问答系统，覆盖文档治理、问题理解、混合检索、证据增强生成、会话管理、效果评测和运营统计。

## 当前实现概览

- 后端：`FastAPI + SQLAlchemy + SQLite + LangChain + Chroma`
- 前端：`Vue 3 + Vite + Element Plus + Pinia + Vue Router`
- 主回答模型：`deepseek-v4-flash`，通过 OpenAI 兼容接口调用
- 问题理解模型：按配置选择主备 provider；当前示例和本机配置为远程 `deepseek-v4-flash` 优先、Ollama `gemma4:e4b` 降级（无配置时的代码默认顺序相反）
- Embedding：`BAAI/bge-small-zh-v1.5`
- Reranker：本地 Cross-Encoder `BAAI/bge-reranker-base`，对规则粗排前 16 个候选做有界加分
- 开发环境：Python `3.12`、Node.js `20+`

当前已实现的主要能力：

- 登录鉴权、服务端会话 token 和管理员权限控制
- `txt/md/json/csv/docx/pdf/xls/xlsx` 文档上传、重名检查和批量上传
- 结构化解析、切片、索引重建、重新切分和单个知识单元维护
- 规则优先、LLM 按需补充的问题理解和追问改写
- 关键词召回、向量召回、多查询扩展、规则粗排与可选模型精排、门槛过滤和证据引用
- 非流式问答与 SSE 流式问答，前端展示理解、检索、过滤、生成等进度
- 历史会话切换、单个删除和批量删除
- 回答反馈、热门问题、无答案问题、提问/上传趋势及最近反馈
- `pytest` 自动化回归、规则评分批量评测和 LLM 裁判评测

> 当前的“问题理解分层方案”是下一阶段可实施设计，尚未拆成“小模型分类 + 大模型扩展 + 并行首轮检索”的两阶段运行链路。

## 项目结构

```text
CMIOT-rag/
├─ knowledge_base_backend/       FastAPI 后端、数据模型、文档与问答服务
│  ├─ app/                       应用代码
│  ├─ tests/                     自动化测试与 qa_eval 批量评测
│  └─ data/                      本地数据库、上传文件和 Chroma 数据（运行时生成）
├─ knowledge_base_frontend/      Vue 前端
├─ scripts/                      启动、停止、测试和报告辅助脚本
├─ 项目测试说明.md               项目级测试入口与评测流程
└─ README.md                     项目总览
```

更详细的实现说明：

- [后端 README](knowledge_base_backend/README.md)
- [前端 README](knowledge_base_frontend/README.md)
- [后端项目概述](knowledge_base_backend/项目概述.md)
- [智能问答逻辑详解](knowledge_base_backend/智能问答逻辑详解.md)
- [问题理解分层方案设计](knowledge_base_backend/问题理解分层方案设计.md)
- [项目测试说明](项目测试说明.md)

## 当前工作流程

### 文档入库

```text
上传/批量上传 -> 类型和重名校验 -> 原文件落盘 -> 结构化解析
-> 切片写入 SQLite -> Embedding 写入 Chroma -> 文档状态 ready
```

同名覆盖先准备新文件及向量，再在一个 SQLite 事务中替换记录，提交后清理旧文件和旧向量。解析、空内容、向量写入或提交失败时保留旧文档；向量清理失败会记录待清理日志。此保护目前针对覆盖上传，不代表所有编辑/重建接口均具备跨存储事务保证。

### 智能问答

```text
用户提问 -> 保存用户消息 -> 规则判断与追问识别
-> 必要时调用问题理解模型 -> 多路关键词/向量召回
-> 规则粗排 + 可选 Cross-Encoder 加分 -> 门槛过滤和来源分桶 -> 组织证据上下文
-> LLM 生成回答 -> 保存消息/引用/摘要 -> 返回完整结果或 SSE 流
```

明显超出知识库范围的问题会跳过检索和生成链路；模型不可用或证据不足时会返回本地兜底结果。

## 本地配置

后端从 `knowledge_base_backend/.env`、`.env.local` 和环境变量读取运行配置。先从示例文件创建本地配置：

```powershell
Copy-Item .\knowledge_base_backend\.env.example .\knowledge_base_backend\.env
```

至少需要设置有效的 `OPENAI_API_KEY`。关键默认值包括：

```dotenv
OPENAI_BASE_URL=https://your-llm-gateway.example.com/v1
LLM_MODEL_NAME=deepseek-v4-flash
OLLAMA_BASE_URL=http://127.0.0.1:11434
QUERY_UNDERSTANDING_PROVIDER=remote
QUERY_UNDERSTANDING_MODEL=deepseek-v4-flash
QUERY_UNDERSTANDING_FALLBACK_PROVIDER=ollama
QUERY_UNDERSTANDING_FALLBACK_MODEL=gemma4:e4b
QUERY_UNDERSTANDING_TIMEOUT_SECONDS=45
QUERY_UNDERSTANDING_TOTAL_TIMEOUT_SECONDS=60
```

问题理解主备请求共享 60 秒调度预算，单次 HTTP 超时不超过 45 秒或剩余预算，远程客户端不做 SDK/transport 隐式重试。HTTP 超时按连接/读写阶段计时，不等同于整条问答链路的硬截止时间；答案生成仍使用独立配置。

运行时配置真源是 `.env` / 环境变量；前端“模型配置”页面目前只维护数据库中的配置记录，不会覆盖后端进程已经加载的模型配置。

## 启动与停止

在仓库根目录一键启动：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-dev.ps1
```

脚本会创建后端 `.venv`、安装前后端依赖，并在独立终端中启动服务。依赖已安装时可运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-dev.ps1 -SkipInstall
```

启动后访问：

- 前端：`http://127.0.0.1:5173`
- 后端：`http://127.0.0.1:8000`
- Swagger：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/health`

关闭开发服务：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\stop-dev.ps1
```

## 手动启动

```powershell
cd .\knowledge_base_backend
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

```powershell
cd .\knowledge_base_frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

## 测试入口

后端自动化回归：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test-backend.ps1 -Install
```

已安装依赖时可省略 `-Install`。真实输入数据驱动的批量问答、规则评分、LLM 裁判和报告说明见 [项目测试说明](项目测试说明.md)。

前端回归在 `knowledge_base_frontend` 下执行 `npm test`，构建执行 `npm run build`。后端回归拦截外部 HTTP，使用临时日志和隔离测试数据，不要求在线模型；PowerShell 入口会传播测试失败退出码。

## 当前边界

- PDF 目前以文本提取为主，不包含完整 OCR 链路，扫描件效果受限。
- SQLite 和本地 Chroma 适合单机或轻量部署，多实例部署需要重新设计共享存储和并发策略。
- 当前精排不是纯模型排序：模型只做有界加分，保留来源配额和覆盖度选择；桶内优先按最终得分，追问优先使用改写后的完整 query。效果提升仍需固定评测集 A/B 验证。
- 本地 Embedding 和 Ollama 模型需要首次下载并保持运行环境可用。
- 问答质量仍高度依赖文档结构、切片质量、知识覆盖和评测数据质量。

## 本地产物与 Git

以下内容属于本地运行产物，不应提交：

- `.env`、`.env.local`
- `.venv`、`node_modules`
- 后端日志和测试报告
- SQLite、Chroma 和上传文件
- 后端静态构建产物

## Windows 编码注意事项

批量评测和报告生成应优先从 UTF-8 文件读取中文，不要在 PowerShell 内联命令里直接拼接大量中文。报告建议使用 `utf-8-sig` 写出，并尽量以 JSON 作为真源再派生 Markdown/CSV，避免中文被转换成 `?`。
