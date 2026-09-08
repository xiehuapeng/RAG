# 知识问答准确性批量评测

这个目录存放当前项目的离线问答评测脚本和测试数据。它们不属于 `pytest` 自动回归，而是用于批量调用真实问答接口，评估知识问答效果。

按当前仓库内容，这里主要有两类评测流程：

1. 规则评分批量评测
2. LLM 裁判批量评测

## 1. 接口适配

批量评测脚本按当前后端真实接口实现：

- 登录：`POST /api/auth/login`
- 创建会话：`POST /api/chat/sessions/create`
- 非流式问答：`POST /api/chat/sessions/{session_id}/messages`
- 认证头：`x-session-token`
- 问答请求体：`{"content": "用户问题"}`
- 回答提取位置：统一响应结构中的 `data.content`

脚本为每个 case 创建一个新的聊天会话，避免上一条问题的上下文影响下一条评测。

## 2. 规则评分批量评测

当前实际可运行的规则评分脚本是：

- `run_qa_eval_reference_with_progress.py`

这个脚本会：

1. 登录后端
2. 为每个 case 创建新会话，或按参数复用一个会话
3. 调用非流式问答接口
4. 读取 `actual_answer`
5. 按 case 中的关键点做规则评分
6. 输出 JSON 和 CSV 报告

### 运行方式

先启动后端服务，默认地址为 `http://127.0.0.1:8000`，然后在仓库根目录运行：

```bash
python knowledge_base_backend/tests/qa_eval/run_qa_eval_reference_with_progress.py
```

可通过环境变量覆盖连接信息：

```bash
set QA_EVAL_BASE_URL=http://127.0.0.1:8000
set QA_EVAL_USERNAME=admin
set QA_EVAL_PASSWORD=admin123
set QA_EVAL_TIMEOUT_SECONDS=120
python knowledge_base_backend/tests/qa_eval/run_qa_eval_reference_with_progress.py
```

也可以指定用例文件和报告目录：

```bash
python knowledge_base_backend/tests/qa_eval/run_qa_eval_reference_with_progress.py --cases knowledge_base_backend/tests/qa_eval/qa_test_cases_3_first20.json --output-dir knowledge_base_backend/tests/qa_eval/reports
```

如果希望接口异常时进程返回非 0：

```bash
python knowledge_base_backend/tests/qa_eval/run_qa_eval_reference_with_progress.py --fail-on-error
```

### Windows PowerShell 中文编码注意事项

在 Windows PowerShell 里直接手写中文问题、中文标签、中文报告标题，再通过内联 Python 或一次性命令生成评测报告时，中文内容可能被吞成 `?`。这个坑在补跑单题、临时重建 Markdown 报告时尤其容易出现。

建议这里统一这样做：

- 测试问题优先从 `qa_test_cases.json`、`docx`、数据库或其他 UTF-8 文件中读取，不要直接写在终端命令里。
- 报告标题、字段标签、固定中文文案，优先放到 Python 脚本文件里，或用 Unicode 转义生成。
- 同时产出 `json` 和 `md` 时，优先让 `json` 做真源，再由脚本从 `json` 重建 `md`。
- 写报告文件时优先使用 `utf-8-sig`，降低 Windows 下再次打开出现乱码的概率。
- 如果发现 `rewrite_query`、问题标题、报告标签突然变成一串 `?`，先排查是不是终端输入阶段已经被编码破坏，而不一定是后端理解链路本身出了问题。

### 当前脚本使用的测试数据格式

`run_qa_eval_reference_with_progress.py` 当前要求的 case 文件是一个 JSON 数组，每条记录至少包含：

```json
{
  "id": "qa_001",
  "name": "简短的用例名称",
  "question": "模拟用户提问",
  "must_have_points": ["必须覆盖的核心点1", "必须覆盖的核心点2"],
  "nice_to_have_points": ["可选加分点1", "可选加分点2"],
  "fatal_mistakes": ["出现即判严重错误的表达"],
  "reference_answer": "人工整理的参考答案",
  "threshold": 0.67,
  "category": "menu_path",
  "source_basis": "对应手册中的依据摘要"
}
```

字段说明：

- `id`：稳定用例编号。
- `name`：用于报告展示的简短名称。
- `question`：实际发送给知识问答接口的问题。
- `must_have_points`：核心必答点，评分和通过判断主要依赖它。
- `nice_to_have_points`：补充加分点，出现则提高加权分，但不直接决定通过。
- `fatal_mistakes`：答案中一旦触发，严格模式下会直接判为失败。
- `reference_answer`：人工整理的参考答案，便于人工复核和后续 LLM 裁判。
- `threshold`：通过阈值，当前用于判断 `must_have_score >= threshold`。
- `category`：用例分类，方便按业务域复核。
- `source_basis`：人工整理的依据摘要，方便定位原始手册依据。

说明：

- 当前脚本默认案例文件是 `qa_test_cases_3_first20.json`。
- 目录中的 `qa_test_cases.json` 属于更早的关键词格式样例，不是这个脚本当前默认消费的格式。

### 评分规则

当前规则评分脚本不使用 LLM 裁判，也不做 embedding 相似度，而是按关键点覆盖率打分。

匹配规则：

- 大小写不敏感。
- 去掉关键词和回答的首尾空白。
- 使用简单子串匹配。
- 空回答会得到 0 分。
- 接口异常会记录到 `error` 字段，并判定该 case 不通过。

分数计算：

```text
must_have_score = 命中的 must_have_points 数 / must_have_points 总数
nice_to_have_score = 命中的 nice_to_have_points 数 / nice_to_have_points 总数；如果 nice_to_have_points 为空则为 0
weighted_score = 0.85 * must_have_score + 0.15 * nice_to_have_score
passed = must_have_score >= threshold

如果开启严格 fatal 逻辑，并且命中了 `fatal_mistakes`，则会被强制判失败。
```

`passed` 优先看核心要点覆盖率；`weighted_score` 主要用于人工观察答案是否还覆盖了补充信息。

### 报告说明

脚本会输出两个报告：

- `reports/qa_test_report.json`
- `reports/qa_test_report.csv`

JSON 报告结构：

```json
{
  "summary": {
    "total": 6,
    "passed": 5,
    "failed": 1,
    "errored": 0,
    "pass_rate": 0.8333
  },
  "metadata": {},
  "results": []
}
```

每条 result 至少包含：

- `id`
- `name`
- `question`
- `category`
- `source_basis`
- `reference_answer`
- `actual_answer`
- `matched_must_have_points`
- `missing_must_have_points`
- `matched_nice_to_have_points`
- `triggered_fatal_mistakes`
- `must_have_score`
- `nice_to_have_score`
- `weighted_score`
- `passed`
- `error`

CSV 报告适合直接用表格工具打开，数组字段会用 ` | ` 拼接展示。

## 3. LLM 裁判批量评测

当前目录里还有一条“后端问答 + 外部模型裁判”的评测路径：

- `run_backend_qa_and_llm_judge_merged.py`

这条链路会先生成 `actual_answer`，再把 `question / source_basis / reference_answer / actual_answer` 发给外部模型，输出：

- `judge_passed`
- `judge_score`
- `factual_conflict`
- `major_missing_points`
- `major_errors`
- `brief_reason`

说明：

- 这类脚本当前主要通过文件头常量配置后端地址、账号和裁判模型参数。
- 运行前请先检查本地配置，不要把敏感配置提交回仓库。
- 默认输出目录通常是 `reports_llm_judge/`。

如果你已经先生成了 `reports/qa_test_report.json`，也可以再使用：

- `judge_qa_report_with_model_sequential.py`
- `judge_qa_report_with_model_sequential_v3.py`
- `judge_qa_report_with_model_sequential_v4_utf8.py`

来做第二阶段裁判。

## 4. 扩展用例建议

新增 case 时建议：

1. 先从手册或知识库原文整理 `source_basis`。
2. 把真正决定答案正确性的点放到 `must_have_points`。
3. 把同义补充、上下文、操作提示放到 `nice_to_have_points`。
4. 把会明显误导业务办理的错误表达放到 `fatal_mistakes`。
5. `threshold` 不要过低。三个核心点时常用 `0.67`，表示至少命中两个核心点。
6. 一个 case 尽量只验证一个明确业务问题，避免一个问题同时覆盖太多规则导致评分难解释。
