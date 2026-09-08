import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.models import ChatSession
from app.qa_logging import QuestionLogSession, build_question_log_path, emit_qa_log
from app.schemas import QueryUnderstandingResult
from app.services.chat import _run_query_understanding
from app.services.qa_langchain import build_evidence_context
from app.services.retrieval_langchain import _log_retrieval_selection


class ChatObservabilityLogTests(unittest.TestCase):
    def test_understanding_logs_source_and_rewritten_question_only(self) -> None:
        session = ChatSession(id=1, user_id=7, title='新建会话', summary_json=None)
        understanding = QueryUnderstandingResult(
            route="kb_qa",
            confidence=0.96,
            is_follow_up=False,
            need_context=False,
            raw_query="open account process",
            rewrite_query="cmiot open account process",
            keywords_hit=["open", "account"],
            entities=["cmiot"],
            search_terms=[],
            search_queries=[],
            filters={},
            reason="unit_test",
            provider="rules",
            model="rules",
            fallback_used=False,
        )

        with (
            patch("app.services.chat.get_query_understanding_service") as mock_service_factory,
            patch("app.services.chat._record_query_understanding_log"),
            patch("app.services.chat._analysis_from_understanding", return_value=SimpleNamespace(route="kb_qa")),
            self.assertLogs("app.qa", level="INFO") as captured,
        ):
            mock_service_factory.return_value.understand.return_value = understanding
            _run_query_understanding(
                object(),
                session=session,
                content="open account process",
                recent_messages=[{"role": "user", "content": "previous question"}],
                session_summary={"last_route": "kb_qa"},
            )

        logs = "\n".join(captured.output)
        self.assertIn('（理解问题）', logs)
        self.assertIn('源问题=open account process', logs)
        self.assertIn('改写后问题=cmiot open account process', logs)
        self.assertNotIn("previous question", logs)

    def test_retrieval_logs_chunk_count_and_documents_only(self) -> None:
        with self.assertLogs("app.qa", level="INFO") as captured:
            _log_retrieval_selection(
                [
                    {
                        "chunk_id": 101,
                        "document_title": "CMIOT guide",
                        "section_title": "Create account",
                    },
                    {
                        "chunk_id": 102,
                        "document_title": "CMIOT policy",
                        "chapter_path": "Chapter 2 / Preconditions",
                    },
                ]
            )

        logs = "\n".join(captured.output)
        self.assertIn('（检索知识库）', logs)
        self.assertIn('检索分片数=2', logs)
        self.assertIn("CMIOT guide", logs)
        self.assertIn("CMIOT policy", logs)
        self.assertNotIn("Create account", logs)

    def test_evidence_context_logs_filtered_chunk_count_and_documents_only(self) -> None:
        retrieval_results = [
            {
                "chunk_id": 201,
                "document_id": 1,
                "document_title": "CMIOT guide",
                "chunk_index": 0,
                "section_title": "Create account",
                "chapter_path": "Guide / Create account",
                "content": "Step one is prepare documents.",
                "score": 0.91,
                "keyword_score": 0.42,
                "vector_score": 0.88,
                "channels": ["keyword"],
            },
            {
                "chunk_id": 202,
                "document_id": 2,
                "document_title": "CMIOT policy",
                "chunk_index": 1,
                "section_title": "Submit materials",
                "chapter_path": "Guide / Submit materials",
                "content": "Step two is submit materials online.",
                "score": 0.87,
                "keyword_score": 0.39,
                "vector_score": 0.84,
                "channels": ["vector"],
            },
        ]

        with self.assertLogs("app.qa", level="INFO") as captured:
            _, evidence = build_evidence_context(retrieval_results, max_chars=10000)

        logs = "\n".join(captured.output)
        self.assertEqual(len(evidence), 2)
        self.assertIn('（分片过滤）', logs)
        self.assertIn('过滤后分片数=2', logs)
        self.assertIn("CMIOT guide", logs)
        self.assertIn("CMIOT policy", logs)
        self.assertNotIn("Create account", logs)

    def test_question_log_session_creates_dedicated_question_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with patch("app.qa_logging._question_log_dir", return_value=temp_path):
                log_path = build_question_log_path('开户流程是什么？')
                self.assertTrue(log_path.name.endswith(".log"))
                self.assertIn('开户流程是什么', log_path.stem)

                with QuestionLogSession('开户流程是什么？') as session_log_path:
                    emit_qa_log('理解问题', '源问题=开户流程是什么？ | 改写后问题=开户流程')

                content = session_log_path.read_text(encoding="utf-8")
                self.assertIn('（理解问题） 源问题=开户流程是什么？ | 改写后问题=开户流程', content)


if __name__ == "__main__":
    unittest.main()
