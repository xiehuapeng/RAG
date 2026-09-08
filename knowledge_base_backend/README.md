# Knowledge Base Backend

这是一个基于 `FastAPI` 的知识库问答后端，面向“文档上传 - 切分 - 向量化 - 检索 - 问答 - 反馈 - 统计”这一整条业务链路。

当前项目采用“业务数据落 SQLite，向量数据落 Chroma”的分层设计。回答生成通过 OpenAI 兼容接口调用 LLM；问题理解主备顺序由配置决定。当前 `.env.example` 和本机配置为远程优先、Ollama 降级，无配置时的代码默认顺序相反。

## 项目定位

- 面向知识库场景的后端服务
- 支持文档上传、文档重建索引、单个 chunk 维护
- 支持登录、会话管理、问答记录、反馈记录
- 支持管理端统计看板和模型配置管理
- 支持 `txt/md/json/csv/docx/pdf/xls/xlsx` 上传白名单
- 当前问答主链路已包含问题理解、多路混合检索、证据引用和 `SSE` 流式进度

## 技术栈

- Web 框架：`FastAPI`
- ORM：`SQLAlchemy`
- 数据库：`SQLite`
- 向量库：`Chroma`
- Embedding：`sentence-transformers`，默认模型 `BAAI/bge-small-zh-v1.5`
- 问题理解：当前示例为远程 `deepseek-v4-flash` 优先、Ollama `gemma4:e4b` 降级
- 精排：可选本地 Cross-Encoder `BAAI/bge-reranker-base`，前 16 个候选有界加分
- 问答模型：默认 `deepseek-v4-flash`，通过 OpenAI 兼容协议接入
- 文档解析：自定义结构化解析器 + `LangChain`
- 启动方式：`uv` / `uvicorn`

## 运行目录

项目运行时会使用以下目录：

- `data/knowledge_base.db`：SQLite 数据库
- `data/uploads/`：原始上传文件
- `data/chroma/`：Chroma 持久化目录

## 启动方式

```bash
uv sync
uv run start
```

如果你习惯直接使用 Python：

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

运行测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

如果你在仓库根目录，也可以用统一脚本：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test-backend.ps1 -Install
```

启动后可以访问：

- Swagger：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/health`

## 请求与响应约定

统一响应格式由 `app.responses.success()` 和 `app.responses.error()` 输出：

```json
{
  "code": 0,
  "message": "success",
  "data": {}
}
```

异常处理统一由 `app.main` 中的 `HTTPException` handler 包装。

- 普通错误会返回 `{"code": 1000, "message": "...", "data": null}`
- 已显式携带业务 `code` 的异常会保持原始 `code` 和 `message`

## 认证方式

系统使用“服务端会话 token”模式：

1. 调用 `POST /api/auth/login`
2. 服务端校验用户名和密码
3. 生成随机 `token`
4. token 写入 `sys_session`
5. 后续请求在请求头中携带 `x-session-token`

`x-session-token` 失效后会直接返回会话过期错误。

## 数据模型

### `sys_user`

系统用户表，存储用户名、密码哈希、角色、最后登录时间。

### `sys_session`

会话表，存储登录 token、过期时间和所属用户。

### `kb_document`

知识库文档表，存储文件元信息、处理状态、错误信息、chunk 数量和创建者。

### `kb_chunk`

知识块表，存储文档切片内容、顺序、`chroma_id` 和元数据 JSON。

### `qa_session`

问答会话表，存储标题和压缩后的会话摘要。

### `qa_message`

问答消息表，存储用户消息、助手回复和引用信息。

### `qa_retrieval_log`

检索日志表，存储每次检索的 query、候选结果和最终重排结果。

### `qa_feedback`

反馈表，存储对回答的正负反馈和补充评论。

### `sys_model_config`

模型配置表，存储模型类型、模型名称、配置 JSON 和启用状态。

说明：

- 当前它主要服务于管理端配置页面的数据维护。
- 实际运行时使用的模型参数仍然由 `app/config.py` 从 `.env` / 环境变量读取。

## 代码结构总览

### 核心入口

- `app/main.py`：FastAPI 应用创建、CORS、路由注册、启动初始化、静态资源托管、统一异常处理
- `app/__main__.py`：命令行启动入口

### 基础设施

- `app/config.py`：路径、环境变量、模型参数、上传白名单
- `app/database.py`：SQLite 引擎、Session 工厂、数据库上下文
- `app/models.py`：ORM 表结构
- `app/schemas.py`：请求体 Pydantic 模型
- `app/responses.py`：统一响应格式
- `app/deps.py`：认证依赖和管理员依赖
- `app/security.py`：密码哈希、密码验证、token 生成
- `app/services/migrations.py`：启动迁移

### 路由层

- `app/routers/auth.py`
- `app/routers/documents.py`
- `app/routers/chat.py`
- `app/routers/dashboard.py`
- `app/routers/config.py`

### 服务层

- `app/services/auth.py`
- `app/services/documents.py`
- `app/services/documents_langchain.py`
- `app/services/query_understanding.py`
- `app/services/chat.py`
- `app/services/dashboard.py`
- `app/services/retrieval.py`
- `app/services/retrieval_langchain.py`
- `app/services/qa.py`
- `app/services/qa_langchain.py`
- `app/services/llm_client.py`
- `app/services/langchain_runtime.py`
- `app/services/parsers.py`
- `app/services/vector_store.py`

### 测试与辅助

- `tests/test_query_understanding.py`
- `tests/test_chat_stream_progress.py`
- `tests/test_chat_observability_logs.py`
- `tests/test_retrieval_gate.py`
- `tests/test_chat_fallback.py`
- `tests/qa_eval/`：真实接口批量问答、规则评分和 LLM 裁判
- `app/test.py`：本地模型流式调试脚本

## 文件级说明

### `app/main.py`

职责：

- 创建 `FastAPI` 应用
- 注册 CORS
- 挂载业务路由
- 挂载前端静态资源
- 在启动时初始化数据库、迁移历史字段、创建默认管理员、回填向量库
- 统一封装 `HTTPException` 为项目响应格式

启动逻辑：

1. `Base.metadata.create_all(bind=engine)`
2. 执行 `run_startup_migrations(engine)`
3. 调用 `bootstrap_admin(db)`
4. 调用 `backfill_vector_store(db)`

路由逻辑：

- `GET /health`：健康检查
- `GET /`：返回前端入口页
- `GET /{full_path:path}`：SPA 路由兜底，非 API 路径统一回落到前端

### `app/__main__.py`

职责：

- 作为 `python -m app` 的启动入口
- 直接调用 `uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)`

### `app/config.py`

职责：

- 统一管理项目根目录、数据目录、上传目录、数据库路径和 Chroma 路径
- 统一读取环境变量
- 定义模型参数、上传大小限制、切片参数和检索参数

关键配置：

- `OPENAI_BASE_URL`
- `OPENAI_API_KEY` / `LLM_API_KEY`
- `LLM_MODEL_NAME`
- `LLM_TEMPERATURE`
- `LLM_MAX_OUTPUT_TOKENS`
- `LLM_REASONING_SPLIT`
- `SESSION_EXPIRE_DAYS`
- `MAX_UPLOAD_SIZE`
- `CHUNK_SIZE`
- `CHUNK_OVERLAP`
- `RETRIEVE_TOP_K`
- `QA_RETRIEVE_TOP_K`
- `QA_HISTORY_LIMIT`
- `QA_MAX_CONTEXT_CHARS`
- `SUPPORTED_EXTENSIONS`

### `app/database.py`

职责：

- 创建 `data/` 和 `data/uploads/`
- 创建 SQLite 引擎
- 提供 `SessionLocal`
- 提供依赖注入函数 `get_db()`
- 提供可用于非请求场景的 `db_context()`
- 提供统一时间函数 `utcnow()`

### `app/models.py`

职责：

- 定义所有 ORM 表模型
- 建立用户、会话、文档、切片、消息、检索日志、反馈和模型配置之间的关系

核心关系：

- `User` 关联 `SessionToken`
- `Document` 关联 `Chunk`
- `ChatSession` 关联 `Message`
- `Message` 关联 `Feedback`
- `Chunk` 通过 `chroma_id` 和 Chroma 向量库建立映射

### `app/schemas.py`

职责：

- 定义接口请求体
- 统一前后端输入字段结构

主要请求模型：

- `LoginRequest`
- `DocumentListRequest`
- `DocumentUploadCheckRequest`
- `ChunkUpdateRequest`
- `CreateSessionRequest`
- `SendMessageRequest`
- `FeedbackRequest`
- `TrendRequest`
- `ModelConfigPayload`

### `app/responses.py`

职责：

- 统一成功响应和错误响应结构

核心函数：

- `success(data=None, message="success", **extra)`
- `error(code, message)`

### `app/deps.py`

职责：

- 根据请求头 `x-session-token` 获取当前用户
- 校验 token 是否存在、是否过期
- 提供管理员权限判断

关键函数：

- `get_current_user()`
- `require_admin()`

### `app/security.py`

职责：

- 生成和验证密码哈希
- 生成随机登录 token

实现方式：

- 密码：`PBKDF2-HMAC-SHA256`
- token：`secrets.token_urlsafe(32)`

### `app/services/auth.py`

职责：

- 初始化默认管理员
- 登录
- 登出

默认管理员：

- 用户名：`admin`
- 密码：`admin123`

### `app/services/migrations.py`

职责：

- 在启动时补齐历史数据库字段

迁移内容：

- `qa_session.summary_json`
- `kb_chunk.chroma_id`
- `qa_feedback.comment`

### `app/services/vector_store.py`

职责：

- 封装 Chroma 的增删查接口
- 保持上层代码只面对统一的向量库 API

主要函数：

- `upsert_chunks()`
- `delete_chunks()`
- `query_chunks()`
- `get_collection()`
- `get_embedding_function()`

### `app/services/langchain_runtime.py`

职责：

- 封装 LangChain 运行时资源
- 提供 embedding、文本切分器、Chroma 实例和 ChatOpenAI 适配器
- 将结构化文档转换成 LangChain `Document`
- 给切分后的片段补齐 `piece_index / piece_count`

核心对象和函数：

- `SentenceTransformerEmbeddings`
- `StructuredDocumentLoader`
- `resolve_sentence_transformer_path()`
- `get_text_splitter()`
- `get_embeddings()`
- `get_vector_store()`
- `build_langchain_documents()`
- `annotate_split_documents()`
- `chroma_upsert()`
- `chroma_delete()`
- `chroma_query()`
- `get_chat_llm()`

### `app/services/llm_client.py`

职责：

- 封装 LLM 的 OpenAI 兼容客户端
- 提供非流式和流式模型调用

核心函数：

- `get_client()`
- `chat_completion()`
- `stream_chat_completion()`

### `app/services/parsers.py`

职责：

- 解析 `txt/md/json/csv/docx`
- 构建结构化章节树
- 输出文档全文和章节树

核心能力：

- Markdown 标题识别
- DOCX 标题和编号识别
- 文本正文兜底解析
- 结构化页面预览支持

### `app/services/documents.py`

职责：

- 旧版文档处理逻辑
- 保留了基于章节结构的切片与向量同步实现
- 适合作为“非 LangChain 版本”的参考实现

它和 `documents_langchain.py` 的关系：

- 两者都处理文档入库和 chunk 同步
- `documents_langchain.py` 是当前主实现
- `documents.py` 更偏向历史版本和兼容性保留

### `app/services/documents_langchain.py`

职责：

- 当前主用的文档处理链路
- 上传文件校验
- 文件落盘
- 文档解析
- 生成 chunk
- 同步 SQLite 和 Chroma
- 支持删除、重建索引、手动更新 chunk

核心流程：

1. 校验文件格式和大小
2. 保存原始文件
3. 解析文档结构
4. 基于章节树构建 chunk
5. 写入 `kb_document` 和 `kb_chunk`
6. 同步 Chroma
7. 更新文档状态为 `ready`

关键函数：

- `validate_upload()`
- `check_existing_file()`
- `save_upload()`
- `save_upload_batch()`
- `ingest_document()`
- `rebuild_document_index()`
- `delete_document_with_vectors()`
- `delete_chunk_with_vector()`
- `update_chunk_content_and_metadata()`
- `get_document_content()`
- `get_document_outline()`
- `backfill_vector_store()`

### `app/services/retrieval.py`

职责：

- 旧版混合检索实现
- 包含关键词召回、向量召回、重排、相关性门槛和检索日志

说明：

- 这个文件仍然有完整逻辑，并且测试文件会直接验证 `_passes_relevance_gate()`
- 当前聊天主链路实际使用的是 `retrieval_langchain.py`

核心函数：

- `tokenize()`
- `_keyword_recall()`
- `_vector_recall()`
- `_merge_candidates()`
- `_rerank_candidates()`
- `_passes_relevance_gate()`
- `retrieve()`

### `app/services/retrieval_langchain.py`

职责：

- 当前聊天主链路使用的检索实现
- 关键词召回和 LangChain 向量召回融合
- 重排、相关性门槛、检索日志落库

和旧版 `retrieval.py` 的区别：

- 向量召回直接使用 `langchain_chroma` 的 `similarity_search_with_score`
- 保留数据库校验，确保只使用当前 `ready` 文档的 chunk
- 检索结果更贴近当前 LangChain 数据结构

核心函数：

- `tokenize()`
- `_keyword_recall()`
- `_vector_recall()`
- `_merge_candidates()`
- `_rerank_candidates()`
- `_passes_relevance_gate()`
- `retrieve()`

### `app/services/qa.py`

职责：

- 传统问答链路的 Prompt 组织和结果整理
- 构建记忆摘要、证据上下文、回答 Prompt
- 提取回答摘要和追问建议

核心函数：

- `load_history_messages()`
- `parse_session_summary()`
- `build_memory_summary()`
- `build_evidence_context()`
- `build_history_block()`
- `build_answer_prompt()`
- `extract_answer_summary()`
- `extract_followup_questions()`
- `build_session_summary()`

### `app/services/qa_langchain.py`

职责：

- 当前主问答链路的 LangChain 版本
- 负责 PromptTemplate、Runnable、流式输出和摘要提取

特点：

- 仍然保留 `SemanticAnalysis` 数据结构
- 以 LangChain 方式构建 `ChatPromptTemplate -> ChatOpenAI -> StrOutputParser`
- 支持流式与非流式回答生成

核心函数：

- `load_history_messages()`
- `parse_session_summary()`
- `build_memory_summary()`
- `build_evidence_context()`
- `build_history_block()`
- `build_answer_chain()`
- `invoke_answer_chain()`
- `stream_answer_chain()`
- `extract_answer_summary()`
- `extract_followup_questions()`
- `build_session_summary()`

### `app/services/chat.py`

职责：

- 当前聊天业务主控层
- 管理会话生命周期
- 拉取历史消息
- 执行检索
- 组织回答
- 支持同步回答和 SSE 流式回答

当前实现特点：

- 每轮先执行问题理解：规则关键词/追问判断 + LLM 按需补充
- `QueryUnderstandingResult` 会通过 `_analysis_from_understanding()` 转成 `SemanticAnalysis`
- `route=kb_qa` 时使用 `retrieve_with_understanding()` 做 `raw_query / rewrite_query / search_terms / search_queries` 多路混合检索
- `route=out_of_scope` 时直接返回范围外回答
- 模型失败或证据不足时，会触发本地兜底回答

核心函数：

- `create_session()`
- `delete_session()`
- `send_message()`
- `stream_message()`
- `_run_query_understanding()`
- `_answer_with_llm()`
- `_analysis_from_understanding()`
- `_should_fallback_no_answer()`
- `build_answer()`

### `app/services/dashboard.py`

职责：

- 统计看板相关聚合查询
- 提供文档、检索、反馈和趋势图数据

核心函数：

- `basic_stats()`
- `ask_trend()`
- `upload_trend()`
- `top_questions()`
- `popular_questions()`
- `no_answer_questions()`
- `feedback_stats()`
- `recent_feedbacks()`

## 接口总览

### 认证接口

- `POST /api/auth/login`
- `POST /api/auth/logout`
- `POST /api/auth/me`

### 文档接口

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

### 聊天接口

- `POST /api/chat/sessions`
- `POST /api/chat/sessions/create`
- `POST /api/chat/sessions/{session_id}`
- `POST /api/chat/sessions/{session_id}/delete`
- `POST /api/chat/sessions/{session_id}/messages`
- `POST /api/chat/sessions/{session_id}/messages/stream`
- `POST /api/chat/popular-questions`
- `POST /api/chat/messages/{message_id}/feedback`

### 看板接口

- `POST /api/dashboard/stats`
- `POST /api/dashboard/top-questions`
- `POST /api/dashboard/no-answer`
- `POST /api/dashboard/feedback`
- `POST /api/dashboard/ask-trend`
- `POST /api/dashboard/upload-trend`
- `POST /api/dashboard/recent-feedback`

### 配置接口

- `POST /api/config/models`
- `POST /api/config/models/create`
- `POST /api/config/models/{model_id}`

## 接口业务逻辑说明

### `POST /api/auth/login`

处理逻辑：

1. 接收 `username` 和 `password`
2. 调用 `app.services.auth.login()`
3. 校验用户名和密码哈希
4. 生成会话 token
5. 写入 `sys_session`
6. 返回用户信息和 token

失败时：

- 账号或密码错误返回 `code=2001`

### `POST /api/auth/logout`

处理逻辑：

1. 从请求头读取 `x-session-token`
2. 删除对应 session 记录
3. 返回注销成功

### `POST /api/auth/me`

处理逻辑：

1. 通过 `get_current_user` 解析当前登录用户
2. 返回用户 `id`、`username`、`role`

### `POST /api/documents/upload`

处理逻辑：

1. 仅管理员可调用
2. 接收 `title`、`file`、`overwrite`
3. 保存原始文件到 `data/uploads/`
4. 写入 `kb_document`
5. 进入文档解析和索引构建流程
6. 切片后写入 `kb_chunk`
7. 同步 Chroma
8. 返回文档 ID 和状态

冲突处理：

- 若同名文件已存在且未指定 `overwrite`，返回 `code=3005`
- 若指定 `overwrite`，会先删除旧文档和旧向量再重新导入

### `POST /api/documents/upload/check`

处理逻辑：

1. 仅管理员可调用
2. 按 `file_name` 查找最新文档
3. 返回是否存在同名文件
4. 如果存在，返回该文档的基础信息

### `POST /api/documents/upload/batch`

处理逻辑：

1. 仅管理员可调用
2. 接收多文件上传
3. 每个文件独立执行单文件上传逻辑
4. 单个失败不会中断整个批次
5. 返回每个文件的独立结果

### `POST /api/documents`

处理逻辑：

1. 当前登录用户可调用
2. 支持 `keyword` 和 `status` 过滤
3. 按创建时间倒序分页返回
4. 返回文档列表、总数和分页信息

### `POST /api/documents/{document_id}`

处理逻辑：

1. 当前登录用户可调用
2. 根据文档 ID 返回文档详情
3. 包含状态、错误信息、chunk 数量、存储路径等

### `POST /api/documents/{document_id}/delete`

处理逻辑：

1. 仅管理员可调用
2. 查找文档
3. 删除 Chroma 中对应向量
4. 删除磁盘文件
5. 删除 SQLite 中文档记录及其 chunk

### `POST /api/documents/{document_id}/chunks`

处理逻辑：

1. 当前登录用户可调用
2. 查找文档及其 chunks
3. 返回每个 chunk 的正文和 metadata
4. 适合前端调试、人工修订和预览

### `POST /api/documents/{document_id}/content`

处理逻辑：

1. 当前登录用户可调用
2. 从原始文件重新解析全文
3. 返回纯文本内容

### `POST /api/documents/{document_id}/outline`

处理逻辑：

1. 当前登录用户可调用
2. 重新解析文档结构
3. 返回章节树大纲
4. 适合前端目录树展示

### `POST /api/documents/{document_id}/reindex`

处理逻辑：

1. 仅管理员可调用
2. 重新执行完整入库流程
3. 重新解析、切片和写入向量库
4. 返回最新状态和 chunk 数

### `POST /api/documents/{document_id}/resplit`

处理逻辑：

1. 仅管理员可调用
2. 当前实现与 `reindex` 走同一条链路
3. 语义上表示“重新切片并重建索引”

### `POST /api/documents/{document_id}/chunks/{chunk_id}/update`

处理逻辑：

1. 仅管理员可调用
2. 校验 chunk 是否属于当前文档
3. 更新 chunk 正文
4. 合并 metadata
5. 同步更新 Chroma 中的向量内容

### `POST /api/documents/{document_id}/chunks/{chunk_id}/delete`

处理逻辑：

1. 仅管理员可调用
2. 删除指定 chunk
3. 删除 Chroma 中对应向量
4. 重新整理剩余 chunk 的顺序号
5. 重算文档 chunk 数并同步向量库

### `POST /api/chat/sessions`

处理逻辑：

1. 当前登录用户可调用
2. 返回当前用户的所有会话
3. 每个会话附带最后一条消息预览和时间
4. 用于前端会话列表

### `POST /api/chat/sessions/create`

处理逻辑：

1. 当前登录用户可调用
2. 新建会话
3. 标题为空时自动使用“新建会话”
4. 返回新会话 ID 和标题

### `POST /api/chat/sessions/{session_id}`

处理逻辑：

1. 当前登录用户可调用
2. 校验会话是否属于当前用户
3. 返回会话基本信息、摘要和全部消息
4. 适合聊天页恢复上下文

### `POST /api/chat/sessions/{session_id}/delete`

处理逻辑：

1. 当前登录用户可调用
2. 删除会话
3. 删除会话下的消息、反馈和检索日志
4. 避免留存孤儿数据

### `POST /api/chat/sessions/{session_id}/messages`

处理逻辑：

1. 当前登录用户可调用
2. 写入用户消息
3. 调用检索模块获取证据
4. 组织问答 Prompt
5. 调用模型生成回答
6. 写入助手消息
7. 更新会话摘要
8. 返回完整回答、引用、相关推荐问题

兜底逻辑：

- 如果证据不足，会返回固定的“当前知识库里没有找到足够可靠的内容”类回答

### `POST /api/chat/sessions/{session_id}/messages/stream`

处理逻辑：

1. 当前登录用户可调用
2. 写入用户消息
3. 通过 SSE 输出 `start`
4. 输出检索阶段 `status`
5. 输出检索证据 `evidence`
6. 输出生成阶段 `status`
7. 持续输出 `delta`
8. 输出 `end`

和非流式接口的区别：

- 交互更实时
- 前端能先看到检索证据再等完整回答

### `POST /api/chat/popular-questions`

处理逻辑：

1. 当前登录用户可调用
2. 返回当前热门问题列表
3. 数据来源于检索日志

### `POST /api/chat/messages/{message_id}/feedback`

处理逻辑：

1. 当前登录用户可调用
2. 接收 `positive` 或 `negative`
3. 验证消息是否存在
4. 写入反馈记录
5. 返回保存成功

非法输入：

- 反馈值不在白名单中时返回错误

### `POST /api/dashboard/stats`

处理逻辑：

1. 仅管理员可调用
2. 统计文档数、ready 数、chunk 数、检索次数、上传数、解析成功率、正反馈率

### `POST /api/dashboard/top-questions`

处理逻辑：

1. 仅管理员可调用
2. 汇总检索日志中的高频 query
3. 返回前 20 条

### `POST /api/dashboard/no-answer`

处理逻辑：

1. 仅管理员可调用
2. 统计 `reranked_chunks == "[]"` 的检索日志
3. 返回高频无答案问题

### `POST /api/dashboard/feedback`

处理逻辑：

1. 仅管理员可调用
2. 统计正反馈、负反馈和总反馈数
3. 返回正反馈率

### `POST /api/dashboard/ask-trend`

处理逻辑：

1. 仅管理员可调用
2. 按天统计检索次数
3. 返回最近 `days` 天趋势

### `POST /api/dashboard/upload-trend`

处理逻辑：

1. 仅管理员可调用
2. 按天统计上传文档数
3. 返回最近 `days` 天趋势

### `POST /api/dashboard/recent-feedback`

处理逻辑：

1. 仅管理员可调用
2. 读取最近反馈
3. 联表查出对应回答
4. 反查用户问题
5. 返回便于运营分析的反馈明细

### `POST /api/config/models`

处理逻辑：

1. 仅管理员可调用
2. 读取全部模型配置
3. 按更新时间倒序返回

### `POST /api/config/models/create`

处理逻辑：

1. 仅管理员可调用
2. 新建模型配置记录
3. 将 `config` 字典序列化成 JSON
4. 返回新建记录

### `POST /api/config/models/{model_id}`

处理逻辑：

1. 仅管理员可调用
2. 按 ID 查找模型配置
3. 更新模型类型、名称、配置和启用状态
4. 未找到时返回“model config not found”

## 检索与问答链路

当前主链路可以概括为：

1. 用户在聊天页发问
2. 服务端保存 `user` 消息
3. 规则层执行关键词、追问和路由判断
4. 规则不确定或需要语义扩展时，按配置调用主模型和备用 provider；远程无隐式重试，主备共享超时调度预算
5. `out_of_scope` 问题跳过 RAG；`kb_qa` 问题使用原始、改写和扩展 query 做多路检索
6. 合并关键词与向量候选，执行规则粗排、可选 Cross-Encoder 加分、相关性过滤和来源分桶；桶内最终得分优先于旧召回名次
7. 把最终 chunk 组织成带引用的证据上下文
8. 调用 LLM 生成回答，并提取摘要和追问建议
9. 保存 `assistant` 消息、引用、日志和会话摘要
10. 返回同步 JSON 或 `start -> understanding -> retrieving -> filtering -> generating -> completed` SSE 流

## 当前已知限制

- PDF 当前主要依赖文本提取，完整 OCR 仍是预留方向
- 当前问题理解仍由一次模型调用同时补充分类、改写和检索扩展；两阶段分层方案尚未实施
- 已接入 Cross-Encoder，但仍是有界加分与来源配额结合，并非纯模型排序；质量增益需 A/B 评测
- 同名覆盖上传先完成新内容解析和向量写入，再事务切换记录；失败保留旧文档，提交后清理旧资源。向量清理失败可能留孤儿数据，需按日志补偿
- 问题理解单次超时由 `QUERY_UNDERSTANDING_TIMEOUT_SECONDS` 控制，主备共享 `QUERY_UNDERSTANDING_TOTAL_TIMEOUT_SECONDS` 调度预算；HTTP 分阶段超时不是整条链路的硬截止
- SQLite 适合轻量场景，不适合高并发大规模部署
- Chroma 与本地 embedding 模型都依赖可用的本地环境

## 测试

当前自动化测试主要覆盖：

- 问题理解、追问改写、路由判断和模型降级
- 检索门槛逻辑
- 问答兜底逻辑
- SSE 进度协议
- 问题理解、检索和证据日志

测试文件：

- `tests/test_query_understanding.py`
- `tests/test_chat_stream_progress.py`
- `tests/test_chat_observability_logs.py`
- `tests/test_retrieval_gate.py`
- `tests/test_chat_fallback.py`

推荐在仓库根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test-backend.ps1 -Install
```

真实接口批量问答、规则评分和 LLM 裁判流程见 [项目测试说明](../项目测试说明.md)。
