from __future__ import annotations

import os
from pathlib import Path

try:
    import winreg
except ImportError:  # pragma: no cover - non-Windows fallback
    winreg = None


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
CHUNK_SIZE = 500
CHUNK_OVERLAP = 80
RETRIEVE_TOP_K = 5
QA_RETRIEVE_TOP_K = 8
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
OPENAI_BASE_URL = _get_env("OPENAI_BASE_URL", "https://api.minimaxi.com/v1", prefer_env_file=True)
OPENAI_API_KEY = _get_env("OPENAI_API_KEY", prefer_env_file=True) or _get_env("MINIMAX_API_KEY", prefer_env_file=True)
MINIMAX_MODEL_NAME = _get_env("MINIMAX_MODEL_NAME", "MiniMax-M2.5", prefer_env_file=True)
MINIMAX_TEMPERATURE = float(_get_env("MINIMAX_TEMPERATURE", "0.2", prefer_env_file=True))
MINIMAX_MAX_OUTPUT_TOKENS = int(_get_env("MINIMAX_MAX_OUTPUT_TOKENS", "2048", prefer_env_file=True))
MINIMAX_REASONING_SPLIT = _get_env("MINIMAX_REASONING_SPLIT", "false", prefer_env_file=True).lower() not in {"0", "false", "no"}
OLLAMA_BASE_URL = _get_env("OLLAMA_BASE_URL", "http://127.0.0.1:11434", prefer_env_file=True)
QUERY_UNDERSTANDING_ENABLED = _get_env("QUERY_UNDERSTANDING_ENABLED", "true", prefer_env_file=True).lower() not in {
    "0",
    "false",
    "no",
}
QUERY_UNDERSTANDING_PROVIDER = _get_env("QUERY_UNDERSTANDING_PROVIDER", "ollama", prefer_env_file=True).strip().lower()
QUERY_UNDERSTANDING_MODEL = _get_env("QUERY_UNDERSTANDING_MODEL", "gemma4 e4b", prefer_env_file=True).strip()
QUERY_UNDERSTANDING_FALLBACK_PROVIDER = _get_env(
    "QUERY_UNDERSTANDING_FALLBACK_PROVIDER",
    "minimax",
    prefer_env_file=True,
).strip().lower()
QUERY_UNDERSTANDING_FALLBACK_MODEL = _get_env(
    "QUERY_UNDERSTANDING_FALLBACK_MODEL",
    MINIMAX_MODEL_NAME,
    prefer_env_file=True,
).strip()
FOLLOW_UP_ENABLED = _get_env("FOLLOW_UP_ENABLED", "true", prefer_env_file=True).lower() not in {"0", "false", "no"}
FOLLOW_UP_MAX_TURNS = int(_get_env("FOLLOW_UP_MAX_TURNS", "4", prefer_env_file=True))
SEMANTIC_CONFIDENCE_THRESHOLD = float(_get_env("SEMANTIC_CONFIDENCE_THRESHOLD", "0.6", prefer_env_file=True))
QUERY_UNDERSTANDING_TIMEOUT_SECONDS = float(
    _get_env("QUERY_UNDERSTANDING_TIMEOUT_SECONDS", "20", prefer_env_file=True)
)

# 上传格式白名单。即便前端做了校验，服务端仍然需要兜底检查。
SUPPORTED_EXTENSIONS = {
    ".txt",
    ".md",
    ".json",
    ".csv",
    ".docx",
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
}

if __name__ == "__main__":
    # 便于本地直接执行 `python app/config.py` 快速查看关键配置。
    print("Configuration:")
    print(f"  OPENAI_BASE_URL: {OPENAI_BASE_URL}")
    print(f"  OPENAI_API_KEY: {OPENAI_API_KEY if OPENAI_API_KEY else '(not set)'}")
    print(f"  MINIMAX_MODEL_NAME: {MINIMAX_MODEL_NAME}")
    print(f"  MINIMAX_TEMPERATURE: {MINIMAX_TEMPERATURE}")
    print(f"  MINIMAX_MAX_OUTPUT_TOKENS: {MINIMAX_MAX_OUTPUT_TOKENS}")
    print(f"  MINIMAX_REASONING_SPLIT: {MINIMAX_REASONING_SPLIT}")
