import copy
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from build_product_research_pages import build_page, render_required_checks
from mcp_product_opportunity_research import enrich_row, research_profile, supplier_matches
from research_required_checks import (
    apply_research_gate, brand_concentration, build_required_checks, cycle_metrics,
    monthly_series, reviewed_check, seasonality_check,
)


TODAY = date(2026, 9, 5)


def trend():
    values = [20, 20, 30, 50, 90, 120, 130, 110, 70, 45, 30, 25]
    return [{"month": f"{2024 + (8 + i) // 12}-{(8 + i) % 12 + 1:02d}",
             "value": values[(8 + i) % 12] * (1 if i < 12 else 1.2)} for i in range(24)]


class RequiredChecksTest(unittest.TestCase):
    def setUp(self):
        self.sources = {"retrieved_on": TODAY.isoformat(), "site": "US", "detail": {"asin": "SEED"},
                        "category_sales_trend": trend(), "product_sales_trend": trend()[-7:]}
        self.summary = {"product": {"asin": "SEED"}, "exact_subform": {"name": "cast fishing net"},
                        "decision": "进入供应商验证", "decision_reason": "market signal"}
        check = {"review_status": "reviewed", "clearance": "pending", "summary": "初步证据",
                 "reviewed_on": TODAY.isoformat(), "sources": [{"title": "Official", "url": "https://www.cpsc.gov/"}]}
        self.review = {"asin": "SEED", "site": "US", "patent": check, "compliance": copy.deepcopy(check)}

    def test_completed_months_only_and_no_imputed_zero(self):
        result = monthly_series(["2026年08月=1,000", "2026-09=10", "2026-10=20", "2026-07=NaN",
                                 "2026-06=bad", "2026-05=-1", "2026-04=0", "2026-13=1"], TODAY)
        self.assertEqual(result, [{"month": "2026-04", "value": 0}, {"month": "2026-08", "value": 1000}])

    def test_two_continuous_cycles_required(self):
        self.assertFalse(cycle_metrics(trend()[:-1]))
        rows = trend()
        rows[8]["month"] = "2024-08"
        self.assertFalse(cycle_metrics(rows))

    def test_repeated_seasonality_and_monotonic_growth_are_different(self):
        self.assertEqual(cycle_metrics(trend())["finding"], "strong")
        rows = trend()
        for i, row in enumerate(rows):
            row["value"] = 2 ** (i / 3)
        self.assertEqual(cycle_metrics(rows)["finding"], "uncertain")

    def test_new_product_uses_category_proxy_not_launch_clearance(self):
        check = seasonality_check(self.summary, self.sources, TODAY)
        self.assertEqual(check["review_status"], "reviewed")
        self.assertEqual(check["scope"], "类目 Top100")
        self.assertEqual(check["product_months"], 7)
        self.assertEqual(check["risk"], "high")
        self.assertEqual(check["clearance"], "pending")
        self.assertIn("不能确认该新品", check["summary"])

    def test_mature_stable_product_can_clear_seasonality(self):
        self.sources["product_sales_trend"] = [{**row, "value": 100} for row in trend()]
        self.assertEqual(seasonality_check(self.summary, self.sources, TODAY)["clearance"], "clear")

    def test_stale_data_and_wrong_asin_cannot_pass(self):
        self.sources["retrieved_on"] = "2025-01-01"
        self.assertEqual(seasonality_check(self.summary, self.sources, TODAY)["review_status"], "pending")
        self.sources["retrieved_on"] = TODAY.isoformat()
        self.sources["detail"]["asin"] = "OTHER"
        required = build_required_checks(self.summary, [], self.sources, self.review, TODAY)
        self.assertEqual(required["checks"]["seasonality"]["review_status"], "pending")

    def test_manual_evidence_needs_scope_date_and_source(self):
        for field, value in [("asin", "OTHER"), ("site", "DE")]:
            review = {**self.review, field: value}
            self.assertEqual(reviewed_check(review, "patent", "SEED", "US", TODAY)["review_status"], "pending")
        for updates in [{"sources": []}, {"reviewed_on": "2027-01-01"}, {"reviewed_on": "2025-01-01"},
                        {"sources": [{"title": "Bad", "url": "javascript:alert(1)"}]}]:
            review = {**self.review, "patent": {**self.review["patent"], **updates}}
            self.assertEqual(reviewed_check(review, "patent", "SEED", "US", TODAY)["review_status"], "pending")

    def test_initial_review_is_not_launch_clearance_or_profit(self):
        required = build_required_checks(self.summary, [], self.sources, self.review, TODAY)
        self.assertTrue(required["review_complete"])
        self.assertFalse(required["launch_clearance"])
        gated = apply_research_gate(self.summary, required, {"profitability_verified": False})
        self.assertIn("暂缓立项", gated["decision"])
        self.assertIn("利润未验证", gated["decision_reason"])
        self.assertEqual(gated["market_signal_decision"], self.summary["decision"])
        again = apply_research_gate(gated, required, {"profitability_verified": False})
        self.assertEqual(again["market_signal_decision"], self.summary["decision"])

    def test_missing_checks_gate_even_when_costs_verified(self):
        required = build_required_checks(self.summary, [], {}, {}, TODAY)
        gated = apply_research_gate(self.summary, required, {"profitability_verified": True})
        self.assertIn("研究待补齐", gated["decision"])

    def test_clear_checks_do_not_bypass_missing_quotes(self):
        required = build_required_checks(self.summary, [], self.sources, self.review, TODAY)
        for check in required["checks"].values():
            check["clearance"] = "clear"
        required["launch_clearance"] = True
        self.assertIn("暂缓立项", apply_research_gate(self.summary, required, {})["decision"])
        self.assertIn("暂缓立项", apply_research_gate(self.summary, required, {"profitability_verified": True})["decision"])

    def test_patent_screening_cannot_grant_legal_clearance(self):
        self.review["patent"].update(clearance="clear", risk="low")
        result = reviewed_check(self.review, "patent", "SEED", "US", TODAY)
        self.assertEqual(result["review_status"], "reviewed")
        self.assertEqual(result["clearance"], "pending")
        self.assertIn("不作为知识产权放行", result["next_steps"][-1])

    def test_high_ip_risk_takes_priority_over_profit(self):
        self.review["patent"].update(clearance="clear", risk="high", summary="命中相关外观专利，待实物比对")
        required = build_required_checks(self.summary, [], self.sources, self.review, TODAY)
        self.assertEqual(required["checks"]["patent"]["clearance"], "blocked")
        self.assertFalse(required["launch_clearance"])
        for verified in (False, True):
            gated = apply_research_gate(self.summary, required, {"profitability_verified": verified})
            self.assertEqual(gated["decision"], "知识产权高风险，暂缓立项与上架")
            self.assertIn("外观专利", gated["decision_reason"])
            self.assertEqual(gated["market_signal_decision"], self.summary["decision"])

    def test_explicit_ip_block_is_not_lost_with_unknown_risk(self):
        self.review["patent"].update(clearance="blocked", risk="unknown")
        required = build_required_checks(self.summary, [], self.sources, self.review, TODAY)
        self.assertEqual(required["checks"]["patent"]["clearance"], "blocked")
        self.assertIn("知识产权高风险", apply_research_gate(self.summary, required, {})["decision"])

    def test_stale_ip_risk_stays_blocked_only_for_matching_scope(self):
        self.review["patent"].update(clearance="blocked", risk="high", reviewed_on="2020-01-01")
        result = reviewed_check(self.review, "patent", "SEED", "US", TODAY)
        self.assertEqual(result["review_status"], "pending")
        self.assertEqual(result["clearance"], "blocked")
        self.assertEqual(reviewed_check(self.review, "patent", "OTHER", "US", TODAY)["risk"], "unknown")

    def test_new_entrant_gate_cannot_hide_ip_hold(self):
        from new_entrant_research import apply_new_entrant_gate
        self.review["patent"].update(risk="high")
        required = build_required_checks(self.summary, [], self.sources, self.review, TODAY)
        gated = apply_research_gate(self.summary, required, {})
        result = apply_new_entrant_gate(gated, {"forms": [{"is_target": True, "independent_supported_count": 0}]})
        self.assertEqual(result["decision"], gated["decision"])

    def test_brand_concentration_deduplicates_parents_and_tracks_unknown(self):
        rows = [
            {"asin": "A", "parent_asin": "P", "brand": "Brand", "monthly_sales": 100, "product_form": "net"},
            {"asin": "B", "parent_asin": "P", "brand": "Brand", "monthly_sales": 80, "product_form": "net"},
            {"asin": "C", "brand": "BRAND", "monthly_sales": 20, "product_form": "net"},
            {"asin": "D", "brand": "", "monthly_sales": 30, "product_form": "net"},
            {"asin": "E", "brand": "Else", "monthly_sales": 999, "product_form": "other"},
        ]
        data = brand_concentration(rows, "net")
        self.assertEqual(data["listing_count"], 3)
        self.assertEqual(data["top1_share"], 0.8)
        self.assertEqual(data["unknown_share"], 0.2)
        self.assertIn("不能证明专利垄断", data["summary"])
        self.assertIn("不代表全市场", data["scope"])
        self.assertNotIn("Top100", data["scope"])

    def test_render_all_checks_for_legacy_page_and_escape_evidence(self):
        required = build_required_checks(self.summary, [], {}, {}, TODAY)
        required["checks"]["patent"]["summary"] = "<script>unsafe</script>"
        rendered = render_required_checks(required)
        self.assertEqual(rendered.count('class="required-check"'), 3)
        self.assertEqual(rendered.count("待补检"), 3)
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "research" / "OLD").mkdir(parents=True)
            with patch("build_product_research_pages.ROOT", root), patch("build_product_research_pages.prepare_web_assets", return_value=""):
                page = build_page({"asin": "OLD"})
            self.assertIn('id="check-seasonality"', page)
            self.assertIn('id="check-patent"', page)
            self.assertIn('id="check-compliance"', page)

    def test_fishing_supplier_leads_are_profile_specific(self):
        seed = {"title": "Fishing Net", "product_form": "landing fishing net"}
        self.assertEqual(research_profile(seed), "landing_fishing_net")
        rows = [{"title": "折叠抄网"}, {"title": "乒乓球捞鱼网"}, {"title": "无环手抛网"}, {"title": "铅坠手抛网"}]
        self.assertEqual(supplier_matches(rows, seed)[0], rows[:1])
        self.assertEqual(supplier_matches(rows, {"product_form": "cast fishing net"})[0], [rows[2]])

    def test_report_copy_stays_inside_web_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "reports" / "product_opportunity_research_OLD.md"
            source.parent.mkdir()
            source.write_text("Research evidence", encoding="utf-8")
            web = root / "web" / "research"
            with patch("build_product_research_pages.ROOT", root), patch("build_product_research_pages.WEB_RESEARCH_DIR", web), patch("build_product_research_pages.prepare_web_assets", return_value=""):
                page = build_page({"asin": "OLD"})
            self.assertIn('href="reports/OLD.md"', page)
            self.assertEqual((web / "reports" / "OLD.md").read_text(), source.read_text())

    def test_detail_title_uses_exact_child_sku(self):
        seed = {"asin": "SEED", "title": "3ft-8ft Cast Net"}
        enrich_row(seed, {"asin": "SEED", "title": "4ft Radius Cast Net"})
        self.assertEqual(seed["title"], "4ft Radius Cast Net")


if __name__ == "__main__":
    unittest.main()
