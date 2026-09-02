import csv
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CategoryResearchProfitabilityTest(unittest.TestCase):
    def test_every_modeled_form_has_a_margin_range(self):
        payload = json.loads(
            (ROOT / "research/category_3397571_analysis.json").read_text(encoding="utf-8")
        )
        estimates = payload["profitability_estimates"]

        self.assertEqual(len(estimates), 9)
        for estimate in estimates:
            margin_keys = [key for key in estimate if key.startswith("net_margin_range")]
            self.assertTrue(margin_keys, estimate["form"])
            for key in margin_keys:
                self.assertEqual(len(estimate[key]), 2)
                self.assertLessEqual(estimate[key][0], estimate[key][1])

    def test_1688_quotes_are_traceable(self):
        with (ROOT / "research/category_3397571_1688_quotes.csv").open(
            encoding="utf-8", newline=""
        ) as handle:
            rows = list(csv.DictReader(handle))

        selected = [row for row in rows if row["selected_for_benchmark"] == "true"]
        exact = [row for row in selected if row["match_quality"] == "exact"]
        self.assertGreaterEqual(len(selected), 20)
        self.assertGreaterEqual(len(exact), 10)
        self.assertTrue(all(row["url"].startswith("https://detail.1688.com/") for row in selected))

    def test_page_and_report_expose_profitability(self):
        page = (ROOT / "web/category/3397571.html").read_text(encoding="utf-8")
        report = (ROOT / "reports/category_deep_research_3397571.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("1688 成本与利润率估算", page)
        self.assertIn("category_3397571_1688_quotes.csv", page)
        self.assertIn("1688 成本与利润率估算", report)
        self.assertIn("净利润率公式", report)


if __name__ == "__main__":
    unittest.main()
