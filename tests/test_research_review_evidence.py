import copy
import unittest
from html.parser import HTMLParser

from build_product_research_pages import (
    render_demand,
    render_review_evidence,
)


class ReviewMarkup(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.elements = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class ReviewEvidenceTest(unittest.TestCase):
    def row(self, **overrides):
        return {
            "product_form": "bottle", "demand_type": "expected",
            "theme": "cleaning_maintenance", "sentiment": "positive",
            "mention_count": "25", "user_profiles": "beginner; family",
            "use_scenes": "daily_home; travel", "example_asins": "B0FPFYXD5Z; B0FL2CHYV2",
            "evidence_excerpt": "Easy to clean.", **overrides,
        }

    def test_legacy_sentiments_stay_pending_without_mutating_evidence(self):
        rows = [self.row(), self.row(sentiment="negative"), self.row(sentiment="neutral"),
                self.row(sentiment=""), self.row(sentiment="unknown")]
        before = copy.deepcopy(rows)
        html = render_review_evidence(rows)
        self.assertIn("旧主题统计（待语义核验）· 5 项", html)
        self.assertEqual(html.count("0 项</span>"), 3)
        self.assertEqual(rows, before)

    def test_negative_topics_do_not_automatically_become_unmet_needs(self):
        rows = [
            self.row(),
            self.row(sentiment="negative", theme="performance", evidence_excerpt="It leaks."),
            self.row(sentiment="negative", theme="durability", demand_type="fulfillment_noise"),
            self.row(sentiment="negative", theme="packaging_delivery"),
            self.row(sentiment="negative", theme="other"),
            self.row(sentiment="neutral", theme="performance"),
            self.row(sentiment="negative", theme="unrecognized_theme"),
        ]
        html = render_review_evidence(rows)
        self.assertEqual(html.count("0 项</span>"), 3)
        self.assertIn("旧主题统计（待语义核验）· 7 项", html)

    def test_more_than_thirty_positive_rows_do_not_hide_negative_evidence(self):
        rows = [self.row(evidence_excerpt=f"Positive evidence {i}") for i in range(40)]
        rows.append(self.row(sentiment="negative", evidence_excerpt="The cap leaks."))
        self.assertEqual(render_demand(rows).count("<tr>"), 41)
        html = render_review_evidence(rows)
        self.assertIn("Positive evidence 39", html)
        self.assertIn("The cap leaks.", html)
        self.assertIn("41 项", html)
        self.assertNotIn("40 条评论", html)

    def test_accessible_tabs_reference_panels_with_one_initial_selection(self):
        elements = ReviewMarkup(render_review_evidence([self.row()])).elements
        tabs = [attrs for tag, attrs in elements if attrs.get("role") == "tab"]
        panels = [attrs for tag, attrs in elements if attrs.get("role") == "tabpanel"]
        self.assertEqual(len(tabs), 3)
        self.assertEqual(len(panels), 3)
        self.assertEqual(sum(tab["aria-selected"] == "true" for tab in tabs), 1)
        self.assertEqual(sum("hidden" not in panel for panel in panels), 1)
        for tab, panel in zip(tabs, panels):
            self.assertEqual(tab["aria-controls"], panel["id"])
            self.assertEqual(panel["aria-labelledby"], tab["id"])
            self.assertEqual(tab["tabindex"], "0" if tab["aria-selected"] == "true" else "-1")

    def test_neutral_evidence_is_retained_outside_sentiment_tabs(self):
        html = render_review_evidence([self.row(sentiment="neutral", evidence_excerpt="No score.")])
        self.assertIn("旧主题统计（待语义核验）· 1 项", html)
        self.assertEqual(html.count("No score."), 1)
        self.assertEqual(html.count("0 项</span>"), 3)

    def test_empty_states_do_not_claim_no_customer_problems(self):
        html = render_review_evidence([])
        self.assertIn("尚未整理出已核验的正面体验", html)
        self.assertIn("不代表产品没有缺点", html)
        self.assertIn("不把一般抱怨改名为痛点", html)
        self.assertNotIn('<details class="review-unclassified">', html)

    def test_context_is_translated_and_saved_excerpt_is_not_truncated(self):
        excerpt = "x" * 180 + " evidence tail"
        html = render_demand([self.row(evidence_excerpt=excerpt)])
        for value in ("清洗与维护", "常规需求", "新手、家庭用户", "日常家用、旅行", excerpt):
            self.assertIn(value, html)

    def test_untrusted_text_is_escaped_and_only_asins_become_links(self):
        html = render_demand([self.row(
            product_form='<script>alert(1)</script>', demand_type='<img src=x>',
            theme='<b>theme</b>', evidence_excerpt='<img src=x onerror=alert(1)>',
            user_profiles='<script>', use_scenes='<script>',
            example_asins='B0FPFYXD5Z; javascript:alert(1); <script>',
        )])
        elements = ReviewMarkup(html).elements
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img", html)
        links = [attrs for tag, attrs in elements if tag == "a"]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["href"], "https://www.amazon.com/dp/B0FPFYXD5Z")
        self.assertEqual(links[0]["rel"], "noreferrer")

    def test_counts_describe_overlapping_evidence_and_pain_is_qualified(self):
        html = render_review_evidence([self.row(sentiment="negative")])
        for text in ("页签计数为观点数", "不是差评的子集", "不按星级分类", "具体场景与缺口证据"):
            self.assertIn(text, html)

    def test_translation_is_a_separate_paragraph_after_unchanged_original(self):
        translations = {"entries": [{"source_excerpt": "Easy to clean.", "translation_zh": "清洗很方便。"}]}
        rows = [self.row()]
        before = copy.deepcopy((rows, translations))
        html = render_demand(rows, translations)
        self.assertIn('<p class="review-original">Easy to clean.</p>', html)
        self.assertIn('<p class="review-translation" lang="zh-CN">', html)
        self.assertLess(html.index("Easy to clean."), html.index("清洗很方便。"))
        self.assertLess(html.index("清洗很方便。"), html.index("同主题涉及 ASIN"))
        self.assertNotIn("待补充", html)
        self.assertEqual((rows, translations), before)

    def test_spanish_excerpts_are_translated_too(self):
        source = "Me gustó para hacer mis pancakes fácil de utilizar"
        translation = "我喜欢用它做煎饼，使用起来很方便。"
        html = render_demand([self.row(evidence_excerpt=source)], {"entries": [
            {"source_excerpt": source, "translation_zh": translation},
        ]})
        self.assertIn(source, html)
        self.assertIn(translation, html)

    def test_legacy_translations_are_retained_without_duplicating_into_pain(self):
        rows = [self.row(sentiment="negative"), self.row(sentiment="negative", theme="durability")]
        translations = {"entries": [{"source_excerpt": "Easy to clean.", "translation_zh": "清洗很方便。"}]}
        html = render_review_evidence(rows, translations)
        self.assertEqual(html.count("清洗很方便。"), 2)
        self.assertEqual(html.count("Easy to clean."), 2)

    def test_changed_excerpt_cannot_reuse_an_obsolete_translation(self):
        translations = {"entries": [{"source_excerpt": "Easy to clean.", "translation_zh": "清洗很方便。"}]}
        html = render_demand([self.row(evidence_excerpt="Not easy to clean.")], translations)
        self.assertNotIn("清洗很方便。", html)
        self.assertIn("中文翻译待补充", html)

    def test_missing_excerpt_or_translation_does_not_invent_evidence(self):
        html = render_demand([self.row(evidence_excerpt="")])
        self.assertIn("未保存原文摘录", html)
        self.assertNotIn("review-translation", html)
        html = render_demand([self.row()], {"entries": [{"source_excerpt": "Easy to clean."}]})
        self.assertIn("中文翻译待补充", html)

    def test_translation_text_is_escaped(self):
        html = render_demand([self.row()], {"entries": [{
            "source_excerpt": "Easy to clean.", "translation_zh": '<script>alert("x")</script>',
        }]})
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_truncated_source_label_is_explicit(self):
        entry = {"source_excerpt": "Easy to clean.", "translation_zh": "清洗很方便。"}
        html = render_demand([self.row()], {"entries": [entry]})
        self.assertNotIn("原文截断", html)
        entry["source_truncated"] = True
        html = render_demand([self.row()], {"entries": [entry]})
        self.assertIn("中文翻译（原文截断）", html)


if __name__ == "__main__":
    unittest.main()
