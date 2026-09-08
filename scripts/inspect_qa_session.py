"""检查验证会话在数据库中实际落盘的问答消息。"""

import json
import sqlite3
import sys
from pathlib import Path

db_path = Path(__file__).resolve().parent.parent / "knowledge_base_backend" / "data" / "knowledge_base.db"
out_path = Path(__file__).resolve().parent.parent / "logs" / "qa_flow_session_messages.json"

session_id = int(sys.argv[1]) if len(sys.argv) > 1 else 18

con = sqlite3.connect(db_path)
rows = con.execute(
    "SELECT id, role, content, references_json, created_at FROM qa_message WHERE session_id=? ORDER BY id",
    (session_id,),
).fetchall()

messages = []
for row in rows:
    messages.append(
        {
            "id": row[0],
            "role": row[1],
            "content": row[2],
            "references": json.loads(row[3]) if row[3] else [],
            "created_at": row[4],
        }
    )

result = {"session_id": session_id, "messages": messages}
out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8-sig")
print(f"Session {session_id}: {len(messages)} messages -> {out_path}")
for m in messages:
    print(f"[{m['role']}] {len(m['content'])} chars, refs={len(m['references'])}")
