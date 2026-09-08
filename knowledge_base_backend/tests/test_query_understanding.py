import unittest
from unittest.mock import patch

from app.services.query_understanding import QueryUnderstandingService


class QueryUnderstandingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = QueryUnderstandingService()
        provider = patch.object(self.service, "understand_with_fallback", return_value=None)
        provider.start()
        self.addCleanup(provider.stop)

    def test_strong_keyword_routes_to_kb_qa(self) -> None:
        result = self.service.understand(
            query="CMIOT 物联网卡开户怎么操作",
            history=[],
            session_summary={},
        )

        self.assertEqual(result.route, "kb_qa")
        self.assertIn("CMIOT", result.rewrite_query)
        self.assertGreaterEqual(result.confidence, 0.88)

    def test_out_of_scope_query_is_blocked_by_rules(self) -> None:
        result = self.service.understand(
            query="今天上海天气怎么样",
            history=[],
            session_summary={},
        )

        self.assertEqual(result.route, "out_of_scope")
        self.assertEqual(result.provider, "rules")

    def test_follow_up_inherits_previous_topic(self) -> None:
        result = self.service.understand(
            query="那线上也能办吗",
            history=[{"role": "user", "content": "集团成员开户怎么办"}],
            session_summary={
                "last_route": "kb_qa",
                "last_user_question": "集团成员开户怎么办",
                "query_understanding": {
                    "route": "kb_qa",
                    "rewrite_query": "集团成员开户怎么办",
                },
            },
        )

        self.assertEqual(result.route, "kb_qa")
        self.assertTrue(result.is_follow_up)
        self.assertTrue(result.need_context)
        self.assertIn("集团成员开户", result.rewrite_query)

    def test_low_confidence_model_result_falls_back_to_rules(self) -> None:
        self.service.semantic_confidence_threshold = 0.9
        self.service.understand_with_fallback = lambda **kwargs: type(
            "LowConfidenceResult",
            (),
            {
                "route": "kb_qa",
                "confidence": 0.2,
                "is_follow_up": False,
                "need_context": False,
                "raw_query": kwargs["query"],
                "rewrite_query": kwargs["query"],
                "keywords_hit": [],
                "entities": [],
                "filters": {},
                "reason": "mock",
                "provider": "mock",
                "model": "mock",
                "fallback_used": False,
            },
        )()

        result = self.service.understand(
            query="那怎么处理",
            history=[{"role": "user", "content": "集团客户注册流程"}],
            session_summary={"last_route": "kb_qa", "last_user_question": "集团客户注册流程"},
        )

        self.assertEqual(result.provider, "rules")
        self.assertEqual(result.route, "kb_qa")


if __name__ == "__main__":
    unittest.main()
