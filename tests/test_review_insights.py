import copy
import unittest

from build_product_research_pages import render_review_evidence
from review_insights import compile_review_insights, snapshot_hash


class SemanticReviewTest(unittest.TestCase):
    def setUp(self):
        self.reviews = [{"asin": "B000000001", "review_title": "Mixed experience", "rating": "5",
                         "review_text": "Easy to clean. The cap leaks. I need a funnel to fill it."}]

    def insight(self, kind, quote):
        return {"id": kind, "kind": kind, "summary_zh": kind,
                "need_zh": "不借助其他工具加料", "scenario_zh": "早餐加干粉",
                "gap_zh": "仍须自备漏斗", "validation_zh": "需求普遍性待验证",
                "need_basis": "explicit_request", "evidence": [
                    {"asin": "B000000001", "review_title": "Mixed experience", "quote": quote,
                     "translation_zh": "中文证据"}]}

    def analyze(self, *insights, reviews=None):
        rows = self.reviews if reviews is None else reviews
        return compile_review_insights(rows, {"schema_version": 2, "source_snapshot": snapshot_hash(rows),
                                              "reviewed_on": "2026-09-08", "insights": list(insights)})

    def test_one_five_star_review_can_have_three_distinct_aspects(self):
        result = self.analyze(self.insight("positive", "Easy to clean."),
                              self.insight("negative", "The cap leaks."),
                              self.insight("pain", "I need a funnel to fill it."))
        self.assertEqual({k: len(v) for k, v in result["groups"].items()}, {"positive": 1, "negative": 1, "pain": 1})
        self.assertEqual(result["cited_count"], 1)
        html = render_review_evidence([], analysis=result)
        for text in ("需要什么", "早餐加干粉", "仍须自备漏斗", "完整评论", "原评分 5 星", "中文证据"):
            self.assertIn(text, html)

    def test_low_star_and_missing_ratings_do_not_change_aspect_classification(self):
        for rating in ("1", "", "5"):
            self.reviews[0]["rating"] = rating
            result = self.analyze(self.insight("positive", "Easy to clean."))
            self.assertEqual(len(result["groups"]["positive"]), 1)
            self.assertEqual(result["groups"]["negative"], [])
            self.assertEqual(result["groups"]["pain"], [])

    def test_wish_praise_and_bad_material_are_not_automatically_unmet_needs(self):
        reviews = [{"review_title": "Love it", "review_text": "Wish I had bought it years ago.", "rating": "5"},
                   {"review_title": "Poor quality", "review_text": "Bad material. It leaks.", "rating": "1"}]
        result = compile_review_insights(reviews, None)
        self.assertEqual(result["groups"]["pain"], [])
        self.assertEqual(result["groups"]["negative"], [])
        self.assertEqual(len(result["pending"]), 2)

    def test_pain_requires_need_scenario_gap_and_basis(self):
        for field in ("need_zh", "scenario_zh", "gap_zh", "validation_zh", "need_basis"):
            insight = self.insight("pain", "I need a funnel to fill it.")
            del insight[field]
            result = self.analyze(insight)
            self.assertEqual(result["groups"]["pain"], [])
            self.assertTrue(result["errors"])

    def test_quote_must_match_actual_asin_title_and_text(self):
        for field, value in (("quote", "A need never stated."), ("asin", "B000000002"),
                             ("review_title", "Another title")):
            insight = self.insight("pain", "I need a funnel to fill it.")
            insight["evidence"][0][field] = value
            result = self.analyze(insight)
            self.assertTrue(result["errors"])
            self.assertEqual(result["groups"]["pain"], [])

    def test_changed_review_context_invalidates_old_judgments(self):
        annotations = {"schema_version": 2, "source_snapshot": snapshot_hash(self.reviews),
                       "insights": [self.insight("pain", "I need a funnel to fill it.")]}
        self.reviews[0]["review_text"] += " Actually, I was mistaken; it opens at the bottom."
        result = compile_review_insights(self.reviews, annotations)
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["groups"]["pain"], [])
        self.assertEqual(len(result["pending"]), 1)

    def test_duplicates_and_cross_asin_shared_text_only_count_once(self):
        reviews = self.reviews * 2 + [{**self.reviews[0], "asin": "B000000002"}]
        insight = self.insight("pain", "I need a funnel to fill it.")
        insight["evidence"] *= 2
        before = copy.deepcopy((reviews, insight))
        result = self.analyze(insight, reviews=reviews)
        self.assertEqual(result["raw_count"], 3)
        self.assertEqual(result["unique_count"], 1)
        self.assertEqual(result["groups"]["pain"][0]["mention_count"], 1)
        self.assertEqual(len(result["groups"]["pain"][0]["evidence"]), 1)
        self.assertEqual(len(result["groups"]["pain"][0]["evidence"][0]["sources"]), 2)
        self.assertEqual((reviews, insight), before)

    def test_duplicate_ids_and_empty_evidence_are_rejected(self):
        insight = self.insight("pain", "I need a funnel to fill it.")
        result = self.analyze(insight, insight)
        self.assertEqual(len(result["groups"]["pain"]), 1)
        self.assertTrue(result["errors"])
        insight["evidence"] = []
        self.assertEqual(self.analyze(insight)["groups"]["pain"], [])

    def test_unreviewed_pages_keep_raw_reviews_and_cached_translations(self):
        source = "Easy to clean."
        reviews = [{"review_title": "Fine", "review_text": source}]
        html = render_review_evidence([], {"entries": [{"source_excerpt": source, "translation_zh": "容易清洗。"}]},
                                      compile_review_insights(reviews, None))
        self.assertIn("容易清洗。", html)
        self.assertIn("其他原文（尚未纳入以上观点）· 1 条", html)
        self.assertIn("具体观点待语义核验", html)
        self.assertEqual(html.count("0 项</span>"), 3)

    def test_truncated_translation_keeps_full_source_available(self):
        self.reviews[0]["review_text"] = "Easy to clean. But it leaks."
        html = render_review_evidence([], {"entries": [{"source_excerpt": "Easy to clean.",
                    "translation_zh": "容易清洗。", "source_truncated": True}]},
                    compile_review_insights(self.reviews, None))
        self.assertIn("中文翻译（原文截断）", html)
        self.assertIn("Easy to clean. But it leaks.", html)

    def test_semantic_summaries_and_source_text_are_escaped(self):
        self.reviews[0]["review_text"] = '<script>alert(1)</script>'
        insight = self.insight("negative", self.reviews[0]["review_text"])
        insight["summary_zh"] = '<img src=x onerror=alert(1)>'
        html = render_review_evidence([], analysis=self.analyze(insight))
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()
