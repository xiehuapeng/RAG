# 后台设计方案

## 1. 目标

本项目是一个知识库问答后台，核心目标是把“文档上传、切片、向量化、检索、问答、反馈、统计”串成一个可落地的闭环。

设计原则：

- 认证简单明确，适合内网系统部署
- 业务数据和向量数据分层存储
- 检索逻辑兼顾“关键词命中”和“语义相似”
- 运行方式尽量轻量，支持 `uv` 启动
- 在没有外网的环境里也能尽可能稳定运行

## 2. 总体架构

系统分为四层：

1. 接口层：`FastAPI` 路由层，负责参数接收、鉴权、返回格式统一
2. 业务层：认证、文档入库、向量化、检索、问答、统计
3. 数据层：`SQLite` 保存业务元数据，`Chroma` 保存向量索引
4. 模型层：`BAAI/bge-small-zh-v1.5` 负责文本 embedding

### 2.1 关键目录

- `app/routers/`：API 路由
- `app/services/`：核心业务逻辑
- `app/models.py`：数据库表定义
- `app/database.py`：数据库连接与会话管理
- `data/uploads/`：上传文件落盘目录
- `data/chroma/`：Chroma 持久化目录
- `data/knowledge_base.db`：SQLite 文件

### 2.2 运行链路

典型请求链路如下：

1. 用户登录并拿到 session token
2. 上传文档
3. 文档解析、切片、写入 SQLite
4. 切片同步写入 Chroma
5. 用户提问
6. 系统执行混合检索
7. 返回答案摘要、引用片段和追问建议
8. 记录检索日志和反馈数据

## 3. 认证设计

## 3.1 认证方式

当前系统采用“服务端会话 + 请求头 token”的认证方式。

流程如下：

1. 用户调用 `/api/auth/login`
2. 服务端校验用户名和密码
3. 生成随机 `token`
4. 将 token 写入 `sys_session`
5. 前端后续请求统一携带 `x-session-token`

这种方式的优点：

- 实现简单
- 可立即失效
- 便于服务端主动控制会话状态
- 适合内网场景和管理后台

### 3.2 认证对象

认证相关表：

- `sys_user`
- `sys_session`

字段职责：

- `sys_user.password_hash`：保存加盐后的密码摘要
- `sys_session.token`：会话 token
- `sys_session.expires_at`：过期时间
- `sys_session.user_id`：会话所属用户

### 3.3 密码安全

密码存储采用：

- `PBKDF2-HMAC-SHA256`
- 随机盐
- 常量时间比较

设计原因：

- 不保存明文密码
- 防止彩虹表攻击
- 降低时序攻击风险

### 3.4 会话过期

当前会话过期策略：

- 默认有效期：`SESSION_EXPIRE_DAYS = 7`
- 过期后直接视为失效
- 登录后重新签发 token

### 3.5 权限模型

系统内置两类权限：

- 普通用户
- 管理员

权限判断依赖：

- `get_current_user`：登录校验
- `require_admin`：管理员校验

推荐约束：

- 普通用户只能查看自己的会话和消息
- 管理员可以上传文档、删除文档、查看统计、维护配置

### 3.6 鉴权接口

- `POST /api/auth/login`
- `POST /api/auth/logout`
- `POST /api/auth/me`

### 3.7 认证建议

如果后续需要增强安全性，可以继续补：

- 登录失败次数限制
- token 刷新机制
- 设备指纹或 IP 风控
- 更细的 RBAC 角色体系

## 4. 文档与向量库设计

## 4.1 存储分层

系统采用“元数据和向量分离”的设计：

- `SQLite`：保存文档、切片、会话、日志、反馈等业务数据
- `Chroma`：保存切片文本的 embedding 和向量检索索引

分层的目的：

- SQLite 负责事务一致性和业务可追溯
- Chroma 负责高效向量召回
- 避免把所有内容都塞进单一数据库

### 4.2 文档表设计

`kb_document` 保存文档级信息：

- `title`
- `file_name`
- `storage_path`
- `file_type`
- `file_size`
- `status`
- `error_message`
- `chunk_count`
- `created_by`
- `created_at`
- `updated_at`

文档状态建议分为：

- `pending`：已创建但未处理
- `processing`：正在解析和切片
- `ready`：可参与检索
- `error`：处理失败

### 4.3 切片表设计

`kb_chunk` 保存切片级信息：

- `document_id`
- `chunk_index`
- `chroma_id`
- `content`
- `metadata_json`
- `created_at`

关键字段说明：

- `content`：切片原文
- `chroma_id`：Chroma 中该切片的主键
- `metadata_json`：可追溯元数据，方便检索后回查

### 4.4 Chroma 设计

当前向量库设计要点：

- 持久化目录：`data/chroma/`
- collection 名称：`knowledge_base_chunks_langchain_v1`
- 距离空间：`cosine`
- embedding 模型：`BAAI/bge-small-zh-v1.5`

Chroma 中每个记录建议包含：

- `id`：切片唯一主键，也就是 `chroma_id`
- `document`：切片正文
- `metadata`：
  - `document_id`
  - `document_title`
  - `file_name`
  - `chunk_id`
  - `chunk_index`
  - `chroma_id`

### 4.5 向量模型设计

当前 embedding 模型：

- `BAAI/bge-small-zh-v1.5`

原因：

- 中文检索效果较稳
- 体积较小，适合内网部署
- 速度和精度平衡较好

离线部署注意：

- 模型首次加载通常会触发下载
- 如果内网无外网访问，需要提前离线缓存模型
- 更稳妥的方式是改成“本地模型目录加载”

### 4.6 入库流程

文档上传后，入库流程如下：

1. 校验文件扩展名和大小
2. 原文件落盘
3. 新建 `kb_document`
4. 解析文本内容
5. 按 `CHUNK_SIZE` 切片
6. 为每个切片生成 `chroma_id`
7. 写入 `kb_chunk`
8. 将切片向量写入 Chroma
9. 更新文档状态为 `ready`

### 4.7 切片策略

当前切片策略：

- 固定窗口切片
- 片段之间保留重叠
- 尽量在段落或句子边界切分

设计原因：

- 固定窗口简单稳定
- 重叠可以减少边界信息丢失
- 自然边界切分让片段更易读

### 4.8 删除和回填

删除文档时需要同步删除：

- SQLite 中的文档记录
- SQLite 中的切片记录
- Chroma 中的向量记录

回填机制：

- 启动时扫描历史 `ready` 文档
- 对缺少 `chroma_id` 或元数据不完整的切片自动回填
- 避免旧数据必须重新上传

## 5. 检索逻辑设计

## 5.1 设计目标

检索模块要满足以下目标：

- 关键词命中要有体现
- 语义相似要有体现
- 标题命中要加权
- 新版本内容略优先
- 候选结果要去重
- 最终排序要简单可解释

### 5.2 混合检索方案

当前推荐的混合检索分为两路：

1. 关键词召回
2. 向量召回

然后统一进入：

- 候选合并
- 去重
- 简单重排

### 5.3 关键词召回

关键词召回的作用：

- 命中用户输入中的显式关键词
- 提升专有名词、表格字段、标题片段的召回能力

建议打分信号：

- 关键词命中数量
- 关键词命中比例
- 标题命中
- 词组短语命中
- 文本整体词频相似度

关键词召回的特点：

- 对精确词更敏感
- 对同义表达不够鲁棒
- 适合和向量召回搭配使用

### 5.4 向量召回

向量召回的作用：

- 捕捉语义相近但字面不同的内容
- 解决同义改写、口语化提问的问题

当前实现：

- 查询问题先做 embedding
- Chroma 按余弦距离返回候选切片
- 距离越近，语义越相似

### 5.5 候选合并

关键词召回和向量召回合并时，需要按 `chunk_id` 去重。

合并原则：

- 同一个 chunk 只保留一条候选
- 关键词分数和向量分数都保留
- 如果两个通道都命中，同一条候选应获得额外加分

### 5.6 重排策略

重排时建议考虑以下信号：

- `keyword_score`
- `vector_score`
- 是否双通道命中
- 标题命中情况
- 文档更新时间

一个可解释的简单策略是：

- `最终分数 = 关键词分数权重 + 向量分数权重 + 标题加权 + 双通道加权 + 新版本微加权`

### 5.7 标题权重

标题命中应视为强信号。

原因：

- 标题通常更概括主题
- 标题命中的内容往往更贴近用户意图
- 很多业务提问实际上是在问“某一章节/某一流程/某一报表”

建议：

- 标题完全命中时额外加分
- 标题关键词部分命中时给较小加分

### 5.8 新版本优先

当前没有独立版本号字段时，可以暂时使用：

- `Document.updated_at`

作为“新版本略优先”的近似信号。

建议：

- 只做轻微加权
- 不能让“新”压过“更相关”
- 未来最好补显式版本字段

### 5.9 检索结果输出

最终返回给问答模块的结果建议包含：

- `chunk_id`
- `document_id`
- `document_title`
- `content`
- `score`
- `keyword_score`
- `vector_score`
- `channels`
- `title_hit`
- `freshness_bonus`
- `chroma_id`

这样后续：

- 便于解释答案来源
- 便于统计命中情况
- 便于调参和排查

## 6. 问答生成设计

当前问答生成采用"检索增强生成（RAG）+ LLM 统一推理"的架构。

流程：

1. 用户提问
2. 问题理解（规则判断 + LLM 按需补充）
3. 多路混合检索
4. 证据打包
5. LLM 生成回答
6. 流式输出
7. 答案落库和会话摘要更新

问题理解采用"规则优先 + LLM 按需补充"策略：

- 规则判断：关键词匹配、追问检测、路由判断
- LLM 调用：规则不确定或需要 enrichment 时，一次调用完成路由+改写+扩展

详见《智能问答逻辑详解》。问题理解的分层改进方案见《问题理解分层方案设计》。

## 7. 数据一致性设计

### 7.1 SQLite 与 Chroma 的关系

SQLite 记录：

- 文档状态
- 切片文本
- 检索日志
- 反馈数据

Chroma 记录：

- 切片向量
- 检索所需 embedding

二者通过：

- `kb_chunk.id`
- `kb_chunk.chroma_id`

建立映射关系。

### 7.2 一致性要求

要求：

- 文档状态为 `ready` 才能被检索
- 切片写入 SQLite 成功后才能写 Chroma
- 删除文档时两边都要删
- 文档重建切片时旧向量不能残留

### 7.3 失败处理

推荐处理原则：

- 解析失败：文档状态置为 `error`
- 向量写入失败：文档状态不能进入 `ready`
- 删除失败：不要留下“SQLite 已删、Chroma 未删”的悬挂数据

## 8. 接口分层

### 8.1 认证接口

- `POST /api/auth/login`
- `POST /api/auth/logout`
- `POST /api/auth/me`

### 8.2 文档接口

- `POST /api/documents/upload`
- `POST /api/documents`
- `POST /api/documents/{document_id}`
- `POST /api/documents/{document_id}/delete`
- `POST /api/documents/{document_id}/chunks`

### 8.3 问答接口

- `POST /api/chat/sessions`
- `POST /api/chat/sessions/create`
- `POST /api/chat/sessions/{session_id}`
- `POST /api/chat/sessions/{session_id}/messages`
- `POST /api/chat/messages/{message_id}/feedback`

### 8.4 统计接口

- `POST /api/dashboard/stats`
- `POST /api/dashboard/top-questions`
- `POST /api/dashboard/no-answer`
- `POST /api/dashboard/feedback`

### 8.5 配置接口

- `POST /api/config/models`
- `POST /api/config/models/{model_id}`

## 9. 启动与部署

### 9.1 本地启动

```bash
uv sync
uv run start
```

### 9.2 健康检查

- `GET /health`

### 9.3 文档调试入口

- Swagger：`/docs`

### 9.4 内网部署重点

如果部署在不能访问外网的环境中，需要提前准备：

- Python 依赖包
- Chroma 本地持久化目录
- BGE 模型本地缓存或本地模型目录

否则首次启动时，模型下载可能失败。

## 10. 风险点

- 目前还没有真正的 PDF/OCR 文本解析
- 版本优先级是"弱规则"，不是严格版本管理
- SQLite 适合轻量场景，不适合高并发大规模部署
- 同步写 Chroma 在大文件场景下会拉长上传时间
- 小模型分类偶尔误判，但规则兜底可降级

## 11. 后续优化建议

1. 增加本地模型路径配置，完全脱离外网依赖
2. 给文档表增加显式版本字段
3. 把文档解析和向量化改成异步任务
4. 增加 PDF 和 OCR 解析能力
5. 增加检索评估指标和离线测试集
6. 增加更严格的权限和审计日志
7. 优化问题理解分层架构（详见《问题理解分层方案设计》）

## 12. 结论

这套后台方案的核心是：

- `SQLite` 管业务与一致性
- `Chroma` 管向量召回
- `BGE` 管中文语义表示
- 混合检索管召回质量
- 会话 token 管鉴权

如果按当前代码继续演进，最值得优先补强的地方是：

- 内网模型加载
- PDF/OCR 解析
- 异步索引
- 显式版本管理
- LLM 回答生成
