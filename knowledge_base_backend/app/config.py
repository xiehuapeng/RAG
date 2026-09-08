from __future__ import annotations

import os
from pathlib import Path

try:
    import winreg
except ImportError:  # pragma: no cover - non-Windows fallback
    winreg = None


def strip_proxy_env() -> None:
    # 应用启动阶段全局清除代理环境变量。
    # 系统/用户级 HTTP_PROXY/HTTPS_PROXY 通常指向 Clash Verge 的 127.0.0.1:7897，
    # 关闭代理软件后，openai SDK / httpx / requests 仍会读到这些变量，
    # 导致公网可达的 LLM 网关（43.108.48.44）反而因连接不到本地代理而失败。
    proxy_keys = (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "all_proxy",
    )
    for key in proxy_keys:
        os.environ.pop(key, None)


# 配置加载前先清掉代理环境变量，确保后续所有 HTTP 客户端默认直连。
strip_proxy_env()


# 所有运行期路径都基于当前项目目录推导，避免依赖机器绝对路径。
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "knowledge_base.db"
CHROMA_DIR = DATA_DIR / "chroma"

# 基础应用配置。
APP_NAME = "knowledge-base-backend"
SESSION_EXPIRE_DAYS = 7
MAX_UPLOAD_SIZE = 200 * 1024 * 1024
MAX_UPLOAD_FILE_COUNT = 20
CHUNK_SIZE = 500
CHUNK_OVERLAP = 80
RETRIEVE_TOP_K = 5
QA_RETRIEVE_TOP_K = 12
QA_HISTORY_LIMIT = 6
QA_MAX_CONTEXT_CHARS = 12000
# 旧项目已经在同一个 Chroma 目录里留下过一份 512 维集合。
# 当前 LangChain 改造后使用的 embedding 维度为 384，为了避免和旧集合发生维度冲突，
# 这里显式切到新的集合名。这样既能保留旧数据做回溯，也能保证新链路独立验证。
# 当前向量库集合名单独配置，主要用于和历史版本索引隔离。
CHROMA_COLLECTION_NAME = "knowledge_base_chunks_langchain_v1"
EMBEDDING_MODEL_NAME = "BAAI/bge-small-zh-v1.5"

def _load_env_from_file(file_path: Path) -> dict[str, str]:
    # 读取 .env / .env.local 中的键值对，不依赖额外第三方库。
    if not file_path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in values:
            values[key] = value
    return values


_ENV_FILE_VALUES = {}
for candidate in (BASE_DIR / ".env", BASE_DIR / ".env.local"):
    _ENV_FILE_VALUES.update(_load_env_from_file(candidate))


def _get_env(name: str, default: str = "", prefer_env_file: bool = False) -> str:
    # 配置优先级：
    # 1. 当前进程环境变量
    # 2. 项目根目录 .env / .env.local
    # 3. Windows 用户环境变量
    # 4. 代码默认值
    if prefer_env_file:
        value = _ENV_FILE_VALUES.get(name)
        if value:
            return value
    value = os.getenv(name)
    if value:
        return value
    value = _ENV_FILE_VALUES.get(name)
    if value:
        return value
    if winreg is not None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
                value, _ = winreg.QueryValueEx(key, name)
                if isinstance(value, str) and value:
                    return value
        except FileNotFoundError:
            pass
        except OSError:
            pass
    return default


# 模型相关配置。
OPENAI_BASE_URL = _get_env("OPENAI_BASE_URL", "", prefer_env_file=True)
OPENAI_API_KEY = _get_env("OPENAI_API_KEY", prefer_env_file=True) or _get_env("LLM_API_KEY", prefer_env_file=True)
LLM_MODEL_NAME = _get_env("LLM_MODEL_NAME", "deepseek-v4-flash", prefer_env_file=True)
LLM_TEMPERATURE = float(_get_env("LLM_TEMPERATURE", "0.2", prefer_env_file=True))
LLM_MAX_OUTPUT_TOKENS = int(_get_env("LLM_MAX_OUTPUT_TOKENS", "2048", prefer_env_file=True))
LLM_REASONING_SPLIT = _get_env("LLM_REASONING_SPLIT", "false", prefer_env_file=True).lower() not in {"0", "false", "no"}
# 答案生成专用思考链开关：通过 extra_body 向上游传 thinking.type=disabled 关闭思考链（DeepSeek 网关实测有效）。
# 关闭可降延迟；开启（false）则保留思考链，通常能提升复杂/数值类问题的回答准确性。
LLM_NO_THINK = _get_env("LLM_NO_THINK", "false", prefer_env_file=True).lower() not in {"0", "false", "no"}
LLM_THINKING_BUDGET = int(_get_env("LLM_THINKING_BUDGET", "0", prefer_env_file=True))
OLLAMA_BASE_URL = _get_env("OLLAMA_BASE_URL", "http://127.0.0.1:11434", prefer_env_file=True)
QUERY_UNDERSTANDING_ENABLED = _get_env("QUERY_UNDERSTANDING_ENABLED", "true", prefer_env_file=True).lower() not in {
    "0",
    "false",
    "no",
}
QUERY_UNDERSTANDING_PROVIDER = _get_env("QUERY_UNDERSTANDING_PROVIDER", "ollama", prefer_env_file=True).strip().lower()
QUERY_UNDERSTANDING_MODEL = _get_env("QUERY_UNDERSTANDING_MODEL", "gemma4:e4b", prefer_env_file=True).strip()
QUERY_UNDERSTANDING_FALLBACK_PROVIDER = _get_env(
    "QUERY_UNDERSTANDING_FALLBACK_PROVIDER",
    "remote",
    prefer_env_file=True,
).strip().lower()
QUERY_UNDERSTANDING_FALLBACK_MODEL = _get_env(
    "QUERY_UNDERSTANDING_FALLBACK_MODEL",
    LLM_MODEL_NAME,
    prefer_env_file=True,
).strip()
# 问题理解专用思考链开关：默认关闭。问题理解只需输出 JSON，关闭思考可降延迟并避免 reasoning 占用预算导致空响应。
QUERY_UNDERSTANDING_NO_THINK = _get_env("QUERY_UNDERSTANDING_NO_THINK", "true", prefer_env_file=True).lower() not in {
    "0",
    "false",
    "no",
}
FOLLOW_UP_ENABLED = _get_env("FOLLOW_UP_ENABLED", "true", prefer_env_file=True).lower() not in {"0", "false", "no"}
FOLLOW_UP_MAX_TURNS = int(_get_env("FOLLOW_UP_MAX_TURNS", "4", prefer_env_file=True))
SEMANTIC_CONFIDENCE_THRESHOLD = float(_get_env("SEMANTIC_CONFIDENCE_THRESHOLD", "0.6", prefer_env_file=True))
QUERY_UNDERSTANDING_TIMEOUT_SECONDS = float(
    _get_env("QUERY_UNDERSTANDING_TIMEOUT_SECONDS", "45", prefer_env_file=True)
)
QUERY_UNDERSTANDING_TOTAL_TIMEOUT_SECONDS = float(
    _get_env("QUERY_UNDERSTANDING_TOTAL_TIMEOUT_SECONDS", "60", prefer_env_file=True)
)

# 交叉编码器重排（Cross-Encoder Reranker）。
# 启发式粗排（关键词+向量加权）难以弥合“自然语言问句 vs 章节标题”的语义鸿沟，
# 例如用户问“需要满足哪些条件”，而目标小节标题是“2.2 前置条件”。
# 在粗排完成后用 Cross-Encoder 对 (query, chunk) 对做联合编码精排。
RERANKER_ENABLED = _get_env("RERANKER_ENABLED", "true", prefer_env_file=True).lower() not in {"0", "false", "no"}
RERANKER_MODEL_NAME = _get_env("RERANKER_MODEL_NAME", "BAAI/bge-reranker-base", prefer_env_file=True).strip()
# 单次检索最多送入重排的候选数：CPU 推理约每条 30~80ms，32 条可把额外延迟控制在 1~3s。
RERANKER_MAX_CANDIDATES = int(_get_env("RERANKER_MAX_CANDIDATES", "16", prefer_env_file=True))
# 重排得分地板线：只有 reranker_score 超过该值才产生加成，避免模型对长块的低估反向惩罚。
RERANKER_CE_FLOOR = float(_get_env("RERANKER_CE_FLOOR", "0.70", prefer_env_file=True))
# 加成权重：bonus = w * max(0, reranker_score - floor)，最终分为“只升不降”的有界提升。
# 实测 bge-reranker-base 在同文档域内打分趋同（含关键词即 0.94+），直接加权融合会破坏
# 启发式排序并误伤长合并块，因此采用加成模式而非替换式融合。
RERANKER_SCORE_WEIGHT = float(_get_env("RERANKER_SCORE_WEIGHT", "0.25", prefer_env_file=True))

# 上传格式白名单。即便前端做了校验，服务端仍然需要兜底检查。
SUPPORTED_EXTENSIONS = {
    ".txt",
    ".md",
    ".json",
    ".csv",
    ".docx",
    ".pdf",
    ".xls",
    ".xlsx",
}

if __name__ == "__main__":
    # 便于本地直接执行 `python app/config.py` 快速查看关键配置。
    print("Configuration:")
    print(f"  OPENAI_BASE_URL: {OPENAI_BASE_URL}")
    print(f"  OPENAI_API_KEY: {OPENAI_API_KEY if OPENAI_API_KEY else '(not set)'}")
    print(f"  LLM_MODEL_NAME: {LLM_MODEL_NAME}")
    print(f"  LLM_TEMPERATURE: {LLM_TEMPERATURE}")
    print(f"  LLM_MAX_OUTPUT_TOKENS: {LLM_MAX_OUTPUT_TOKENS}")
    print(f"  LLM_REASONING_SPLIT: {LLM_REASONING_SPLIT}")
    print(f"  LLM_NO_THINK: {LLM_NO_THINK}")
