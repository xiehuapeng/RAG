import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.models import ChatSession
from app.schemas import QueryUnderstandingResult
from app.services.chat import stream_message


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *args, **kwargs):
        del args, kwargs
        return self

    def first(self):
        return self._result


class _FakeDB:
    def __init__(self, session_row):
        self.session_row = session_row
        self.commit_count = 0
        self.rollback_count = 0

    def add(self, obj):
        del obj

    def commit(self):
        self.commit_count += 1

    def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = self.commit_count + 1

    def rollback(self):
        self.rollback_count += 1

    def query(self, model):
        del model
        return _FakeQuery(self.session_row)


async def _collect_sse_events(response):
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)

    events = []
    buffer = "".join(chunks)
    for block in buffer.split("\n\n"):
        if not block.strip():
            continue
        event_name = "message"
        data_lines = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].strip())
        payload = json.loads("\n".join(data_lines)) if data_lines else None
        events.append((event_name, payload))
    return events


class ChatStreamProgressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session_row = ChatSession(id=1, user_id=1, title="新建会话", summary_json=None)
        self.analysis = SimpleNamespace(
            route="kb_qa",
            intent="qa",
            is_follow_up=False,
            keywords_hit=[],
            entities=[],
            reason="unit_test",
            confidence=0.95,
        )

    def test_rag_stream_emits_all_progress_steps(self) -> None:
        db = _FakeDB(self.session_row)
        understanding = QueryUnderstandingResult(
            route="kb_qa",
            confidence=0.95,
            is_follow_up=False,
            need_context=False,
            raw_query="开户流程是什么",
            rewrite_query="开户流程是什么",
            keywords_hit=["开户"],
            entities=[],
            search_terms=[],
            search_queries=[],
            filters={},
            reason="unit_test",
            provider="rules",
            model="rules",
            fallback_used=False,
        )

        with (
            patch("app.services.chat._load_prompt_messages", return_value=[]),
            patch("app.services.chat.parse_session_summary", return_value={}),
            patch("app.services.chat._build_memory_context", return_value="memory"),
            patch("app.services.chat.get_query_understanding_service") as mock_service_factory,
            patch("app.services.chat._record_query_understanding_log"),
            patch("app.services.chat._analysis_from_understanding", return_value=self.analysis),
            patch(
                "app.services.chat.retrieve_with_understanding",
                return_value=[{"score": 0.9, "content": "开户流程", "document_title": "开户文档"}],
            ),
            patch(
                "app.services.chat.build_evidence_context",
                return_value=("evidence context", [{"snippet": "开户流程", "score": 0.9}]),
            ),
            patch("app.services.chat.stream_answer_chain", return_value=iter(["开户", "流程说明"])),
            patch("app.services.chat.extract_followup_questions", return_value=["下一步是什么"]),
            patch("app.services.chat.extract_answer_summary", return_value="摘要"),
            patch("app.services.chat.build_session_summary", return_value={"summary": "ok"}),
        ):
            mock_service_factory.return_value.understand.return_value = understanding
            response = stream_message(db, self.session_row, "开户流程是什么")
            events = asyncio.run(_collect_sse_events(response))

        progress_steps = [payload["current_step"] for event, payload in events if event == "progress"]
        self.assertEqual(
            progress_steps,
            ["start", "understanding", "retrieving", "filtering", "generating", "completed"],
        )
        completed_payload = [payload for event, payload in events if event == "progress" and payload["current_step"] == "completed"][0]
        self.assertTrue(all(step["status"] == "done" for step in completed_payload["steps"]))
        self.assertIn(("end", {"answer": "开户流程说明", "summary": "摘要", "suggestions": ["下一步是什么"]}), events)

    def test_out_of_scope_stream_marks_rag_steps_skipped(self) -> None:
        db = _FakeDB(self.session_row)
        understanding = QueryUnderstandingResult(
            route="out_of_scope",
            confidence=0.92,
            is_follow_up=False,
            need_context=False,
            raw_query="今天天气怎么样",
            rewrite_query="今天天气怎么样",
            keywords_hit=[],
            entities=[],
            search_terms=[],
            search_queries=[],
            filters={},
            reason="unit_test",
            provider="rules",
            model="rules",
            fallback_used=False,
        )
        out_of_scope_result = {
            "answer": "这个问题不在知识库范围内。",
            "references": [],
            "suggestions": ["问业务问题"],
            "summary": "范围外",
            "session_summary": {"summary": "out"},
        }

        with (
            patch("app.services.chat._load_prompt_messages", return_value=[]),
            patch("app.services.chat.parse_session_summary", return_value={}),
            patch("app.services.chat._build_memory_context", return_value="memory"),
            patch("app.services.chat.get_query_understanding_service") as mock_service_factory,
            patch("app.services.chat._record_query_understanding_log"),
            patch("app.services.chat._analysis_from_understanding", return_value=self.analysis),
            patch("app.services.chat._build_out_of_scope_result", return_value=out_of_scope_result),
        ):
            mock_service_factory.return_value.understand.return_value = understanding
            response = stream_message(db, self.session_row, "今天天气怎么样")
            events = asyncio.run(_collect_sse_events(response))

        progress_steps = [payload["current_step"] for event, payload in events if event == "progress"]
        self.assertEqual(progress_steps, ["start", "understanding", "completed"])
        completed_payload = [payload for event, payload in events if event == "progress" and payload["current_step"] == "completed"][0]
        skipped_map = {step["key"]: step["status"] for step in completed_payload["steps"]}
        self.assertEqual(skipped_map["retrieving"], "skipped")
        self.assertEqual(skipped_map["filtering"], "skipped")
        self.assertEqual(skipped_map["generating"], "skipped")
        self.assertEqual(skipped_map["understanding"], "done")


if __name__ == "__main__":
    unittest.main()
