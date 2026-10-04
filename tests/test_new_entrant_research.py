import copy
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from build_product_research_pages import render_new_entrant_analysis
from new_entrant_research import (
    TOOLS, analyze_new_entrants, apply_new_entrant_gate, collect_evidence,
    recent_candidates, refresh_new_entrants, six_months_before, write_json,
)


TODAY = date(2026, 9, 6)
ASIN = "B000000001"


class NewEntrantResearchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.study = self.root / "research" / ASIN
        self.study.mkdir(parents=True)
        self.product = {
            "asin": ASIN, "title": "Drain cover", "online_date": "2026-03-18", "monthly_sales": 1000,
            "price": 4, "pack_count": 2, "screening_status": "eligible",
            "metric_sources": {"monthly_sales": {"retrieved_on": "2026-09-05"}},
        }
        self.landscape = {"forms": [{"form": "cover", "label": "Cover", "is_target": True, "products": [self.product]}]}

    def raw(self, kind, data=None, **fields):
        defaults = {
            "detail": {"asin": ASIN, "brand": "Example", "parent_asin": "B000000009", "fba_fee": 2.5},
            "variations": [{"Asin": ASIN, "SalesAmount": 600, "ItemTotal": 1}],
            "price": ["2026年03月=10", "2026年08月=4", "2026年09月=3"],
            "sales": ["2026年03月=0", "2026年08月=800", "2026年09月=9999"],
            "reviews": [{"review_date": "20250101", "content": "Old variant"}, {"review_date": "20260401"}],
        }
        value = {"tool": TOOLS[kind][0], "arguments": {"asin": ASIN, "amz_site": "US", **TOOLS[kind][1]},
                 "retrieved_on": "2026-09-06", "result": {"data": defaults[kind] if data is None else data}, **fields}
        write_json(self.study / "new_entrant_evidence" / f"{ASIN}_{kind}.json", value)
        return value["result"]

    def prepare(self):
        for kind in TOOLS:
            self.raw(kind)

    def analysis(self):
        return analyze_new_entrants(self.root, self.study, self.landscape, TODAY)

    def reviewed(self, gate="price_pressure", **fields):
        p = self.analysis()["products"][0]
        note = {"reviewed_on": "2026-09-06", "evidence_digest": p["evidence_digest"],
                "snapshot": {key: self.product[key] for key in ("online_date", "monthly_sales", "price")},
                "sources": [{"title": "Sorftime"}], "entry_gate": gate, "conclusion": "Needs margin check", **fields}
        write_json(self.study / "new_entrant_reviews.json", {"products": {ASIN: note}})

    def test_calendar_window_not_180_days(self):
        self.assertEqual(six_months_before(TODAY), date(2026, 3, 6))
        self.assertEqual(six_months_before(date(2024, 8, 31)), date(2024, 2, 29))
        self.assertEqual(six_months_before(date(2025, 8, 31)), date(2025, 2, 28))
        for listed, sales, expected in [("2026-03-06", 301, 1), ("2026-03-05", 999, 0),
                                         ("2026-09-07", 999, 0), ("", 999, 0),
                                         ("2026-09-06", 300, 0), ("2026-09-06", None, 0)]:
            self.product.update(online_date=listed, monthly_sales=sales)
            self.assertEqual(len(recent_candidates(self.landscape, TODAY)), expected)

    def test_asin_deduplication(self):
        self.landscape["forms"].append(copy.deepcopy(self.landscape["forms"][0]))
        self.assertEqual(len(recent_candidates(self.landscape, TODAY)), 1)

    def test_complete_evidence_does_not_auto_infer_cause(self):
        self.prepare()
        p = self.analysis()["products"][0]
        self.assertTrue(p["evidence_complete"])
        self.assertEqual(p["entry_gate"], "needs_attribution")
        self.assertEqual(p["reviews_before_launch"], 1)
        self.assertEqual(p["price_change"], -0.6)
        self.assertEqual(p["sales_series"][0]["value"], 0)
        self.assertEqual(p["sales_series"][-1]["month"], "2026-08")
        self.assertIsNone(p["same_pack_peer_price"])

    def test_child_scope_and_unknown_badge_cannot_pass(self):
        self.prepare()
        for badge in (200, 300, -1, None):
            self.raw("variations", [{"Asin": ASIN, "SalesAmount": badge}])
            self.reviewed()
            p = self.analysis()["products"][0]
            self.assertEqual(p["entry_gate"], "sales_scope_unverified")
            if badge == -1:
                self.assertIsNone(p["child_sales_badge"])

    def test_reviews_bound_to_snapshot_and_evidence(self):
        self.prepare()
        self.reviewed()
        self.assertTrue(self.analysis()["products"][0]["reviewed"])
        self.product["price"] = 6
        self.assertFalse(self.analysis()["products"][0]["reviewed"])
        self.product["price"] = 4
        self.raw("reviews", [{"review_date": "20260801"}])
        self.assertFalse(self.analysis()["products"][0]["reviewed"])

    def test_stale_snapshot_and_screening_still_block(self):
        self.prepare()
        self.reviewed()
        self.product["metric_sources"]["monthly_sales"]["retrieved_on"] = "2026-05-01"
        self.assertEqual(self.analysis()["products"][0]["entry_gate"], "needs_refresh")
        self.product["screening_status"] = "needs_evidence"
        self.assertEqual(self.analysis()["products"][0]["entry_gate"], "screening_pending")

    def test_invalid_empty_or_stale_evidence_is_not_complete(self):
        self.prepare()
        for data, fields in [({"asin": "B000000002"}, {}), ({"asin": ASIN}, {"retrieved_on": "2026-05-01"})]:
            self.raw("detail", data, **fields)
            self.assertFalse(self.analysis()["products"][0]["evidence_complete"])
        self.raw("detail")
        self.raw("reviews", [])
        self.assertFalse(self.analysis()["products"][0]["evidence_complete"])

    def test_malformed_variations_do_not_break_page_build(self):
        self.prepare()
        self.raw("variations", ["invalid response"])
        self.assertFalse(self.analysis()["products"][0]["evidence_complete"])
        self.raw("variations", [{"Asin": ASIN, "SalesAmount": 500, "ItemTotal": "unknown"}])
        self.assertEqual(self.analysis()["products"][0]["variation_total"], 0)

    def test_positive_requires_reviewed_replicability_evidence(self):
        self.prepare()
        self.reviewed("independent_entry_supported")
        self.assertEqual(self.analysis()["products"][0]["entry_gate"], "needs_attribution")
        self.reviewed("independent_entry_supported", replicability_evidence=["Verified independent launch and viable pricing"])
        analysis = self.analysis()
        self.assertEqual(analysis["forms"][0]["independent_supported_count"], 1)
        original = {"decision": "Risk still pending"}
        self.assertEqual(apply_new_entrant_gate(original, analysis)["decision"], original["decision"])

    def test_gate_respects_target_and_hard_rejections(self):
        self.prepare()
        analysis = self.analysis()
        original = {"decision": "Consider", "decision_reason": "Profit pending."}
        result = apply_new_entrant_gate(original, analysis)
        self.assertIn("暂缓", result["decision"])
        self.assertIn("Profit pending", result["decision_reason"])
        rejected = {**original, "archive_status": "rejected", "decision": "Rejected"}
        self.assertEqual(apply_new_entrant_gate(rejected, analysis)["decision"], "Rejected")
        analysis["forms"][0]["is_target"] = False
        self.assertEqual(apply_new_entrant_gate(original, analysis)["decision"], "Consider")

    def test_collection_budget_reuse_and_fail_fast(self):
        client = Mock()
        client.call_tool.side_effect = lambda name, args: {"data": {"asin": ASIN}} if name == "product_detail" else {"data": [{"Asin": ASIN}]}
        manifest = collect_evidence(self.root, self.study, self.landscape, max_calls=1, client=client, as_of=TODAY)
        self.assertEqual(manifest["tool_calls"], 1)
        client.call_tool.side_effect = RuntimeError("secret URL not to log")
        manifest = collect_evidence(self.root, self.study, self.landscape, max_calls=5, client=client, as_of=TODAY)
        self.assertEqual(manifest["cache_reuses"], 1)
        self.assertEqual(manifest["tool_calls"], 1)
        self.assertNotIn("secret", json.dumps(manifest))
        manifest = collect_evidence(self.root, self.study, self.landscape, max_calls=0, client=client, as_of=TODAY)
        self.assertEqual(manifest["tool_calls"], 0)

    def test_register_existing_never_collects(self):
        with patch("new_entrant_research.build_landscape", return_value=self.landscape), patch("new_entrant_research.collect_evidence") as collect:
            refresh_new_entrants(self.root, self.study, collect=False)
        collect.assert_not_called()

    def test_render_images_escaping_and_zero_not_missing(self):
        self.prepare()
        self.product["title"] = '<script>alert("x")</script>'
        self.reviewed(factors=[{"label": "Price", "finding": "<bad>"}])
        html = render_new_entrant_analysis(self.analysis(), [{"asin": ASIN, "web_image_url": "assets/test.jpg"}])
        self.assertIn('src="assets/test.jpg"', html)
        self.assertNotIn('<script>alert', html)
        self.assertIn("&lt;bad&gt;", html)
        self.assertIn("<td>0</td>", html)
        self.assertIn("目标子款公开销量档位", html)
        self.landscape["forms"] = []
        self.assertIn("不代表该形态没有机会", render_new_entrant_analysis(self.analysis()))


if __name__ == "__main__":
    unittest.main()
