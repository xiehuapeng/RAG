# -*- coding: utf-8 -*-
"""读取后端最新日志尾部并检查健康端点，输出 JSON 避免终端乱码。"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

LOG = Path(r"e:\Desktop\CMIOT-rag\knowledge_base_backend\logs\backend.log")
OUT = Path(r"e:\Desktop\CMIOT-rag\logs\backend_status_check.json")

result = {"log_tail": [], "health": {}}

if LOG.exists():
    lines = LOG.read_text(encoding="utf-8", errors="ignore").splitlines()
    result["log_tail"] = lines[-40:]

for path in ("/api/health", "/health"):
    url = f"http://127.0.0.1:8000{path}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            result["health"][path] = {"status": resp.status, "body": resp.read(500).decode("utf-8", errors="ignore")}
    except Exception as exc:  # noqa: BLE001
        result["health"][path] = {"error": str(exc)}

OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8-sig")
print(f"written to {OUT}")
