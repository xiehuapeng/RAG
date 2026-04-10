import unittest

from app.services.retrieval import _passes_relevance_gate


class RetrievalGateTests(unittest.TestCase):
    def test_low_score_without_hits_is_blocked(self) -> None:
        self.assertFalse(
            _passes_relevance_gate(
                {
                    "score": 0.2687,
                    "keyword_score": 0.0,
                    "title_hit": False,
                }
            )
        )

    def test_title_hit_is_allowed(self) -> None:
        self.assertTrue(
            _passes_relevance_gate(
                {
                    "score": 0.2687,
                    "keyword_score": 0.0,
                    "title_hit": True,
                }
            )
        )

    def test_keyword_fallback_is_allowed(self) -> None:
        self.assertTrue(
            _passes_relevance_gate(
                {
                    "score": 0.32,
                    "keyword_score": 0.21,
                    "vector_score": 0.56,
                    "title_hit": False,
                }
            )
        )

    def test_keyword_fallback_is_blocked_when_vector_is_low(self) -> None:
        self.assertFalse(
            _passes_relevance_gate(
                {
                    "score": 0.32,
                    "keyword_score": 0.21,
                    "vector_score": 0.52,
                    "title_hit": False,
                }
            )
        )

    def test_high_score_is_allowed(self) -> None:
        self.assertTrue(
            _passes_relevance_gate(
                {
                    "score": 0.50,
                    "keyword_score": 0.0,
                    "title_hit": False,
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
