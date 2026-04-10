import unittest

from app.services.chat import NO_ANSWER_TEXT, build_answer


class ChatFallbackTests(unittest.TestCase):
    def test_irrelevant_query_returns_no_answer(self) -> None:
        retrieval_results = [
            {
                "score": 0.2252,
                "keyword_score": 0.0,
                "vector_score": 0.4648,
                "title_hit": False,
                "document_title": "CMIOT-B04-集团群组开户操作指南",
                "content": "无关问题被向量召回到的噪音内容",
            },
            {
                "score": 0.2211,
                "keyword_score": 0.0,
                "vector_score": 0.4550,
                "title_hit": False,
                "document_title": "CMIOT-AI灵犀助手操作指南",
                "content": "另一条无关噪音内容",
            },
        ]

        answer, references, suggestions = build_answer("今天天气怎么样", retrieval_results)

        self.assertEqual(answer, NO_ANSWER_TEXT)
        self.assertEqual(references, [])
        self.assertTrue(suggestions)

    def test_relevant_query_keeps_reference_summary(self) -> None:
        retrieval_results = [
            {
                "score": 0.8123,
                "keyword_score": 0.3410,
                "vector_score": 0.7420,
                "title_hit": True,
                "document_title": "CMIOT-B04-集团群组开户操作指南",
                "content": "3 集团群组开户-语音群组开户操作步骤 / 3.1 路径：进入群组开户页面。",
            }
        ]

        answer, references, suggestions = build_answer("群组开户怎么操作", retrieval_results)

        self.assertIn("CMIOT-B04-集团群组开户操作指南", answer)
        self.assertIn("1. 3 集团群组开户", answer)
        self.assertEqual(references, retrieval_results[:1])
        self.assertTrue(suggestions)


if __name__ == "__main__":
    unittest.main()
