"""扫描最新 QA 单题日志中的时间戳行，还原服务端各阶段耗时。"""

import json
import re
from pathlib import Path

qa_dir = Path(__file__).resolve().parent.parent / "knowledge_base_backend" / "logs" / "qa"
out_path = Path(__file__).resolve().parent.parent / "logs" / "qa_log_timeline.json"

files = sorted(qa_dir.iterdir(), key=lambda p: p.stat().st_mtime)
latest = files[-1]
text = latest.read_bytes().decode("utf-8", errors="replace")
lines = text.splitlines()

ts_pattern = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}")
hits = []
for i, line in enumerate(lines):
    if ts_pattern.search(line):
        hits.append({"line": i + 1, "text": line.strip()[:150]})

# 同时找阶段标题行（如"理解问题"、"检索"、"整理答案"等），带行号
stage_hits = []
for i, line in enumerate(lines):
    stripped = line.strip()
    if len(stripped) < 30 and stripped and not stripped.startswith(("Title", "Score", "Content", "Keyword", "Vector", "Channels")):
        stage_hits.append({"line": i + 1, "text": stripped})

result = {
    "file": latest.name,
    "total_lines": len(lines),
    "timestamp_lines": hits[:30],
    "short_lines": stage_hits[:60],
}
out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8-sig")
print("done:", out_path)
