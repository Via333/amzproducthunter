import csv
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from supplier_screening import (SOURCE_FILE, build_screening, collect_supplier_screening,
                                match_title, number, render_supplier_screening, title_quantity)


SEED = {"title": "SPANSEE 2 PCS Cake Arch Guide Tool"}


def candidate(offer="123", **changes):
    row = {"product_id": offer, "title": "2 PCS Cake Arch Guide Tool 蛋糕拱形导向工具",
           "price": 3.5, "service_score": 4.5, "repurchase_rate": 40,
           "min_order_quantity": 2, "sales_of_30d": 100,
           "service_score_detail": [{"title": "品质体验", "score": 4.3}],
           "seller_identities": ["tp_member"]}
    return dict(row, **changes)


class ScreeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.save("research_sources.json", {"detail": SEED, "retrieved_on": date.today().isoformat(),
                                            "supplier_candidates": [candidate()]})

    def save(self, filename, data):
        (self.root / filename).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_title_match_not_category_or_pack_count(self):
        for title in ("Hushee 6 Pcs Cake Arch Guide Tool", "蛋糕弧形裱花辅助器", "蛋糕拱形导向工具"):
            self.assertEqual(match_title(SEED, title)[0], "matched")
        for title in ("汽车扶手挂钩", "美甲练习架", "发量多鲨鱼夹", "洗头刷", "蛋糕打桩支架", "花洒清洁工具"):
            self.assertEqual(match_title(SEED, title)[0], "excluded")
        self.assertEqual(match_title(SEED, "通用厨房新品")[0], "pending")
        self.assertEqual(match_title({"title": "Cocktail Jigger"}, "双头量酒杯")[0], "matched")
        self.assertEqual(match_title({"title": "Rolling Pin"}, "木质擀面杖")[0], "matched")

    def test_quantity_is_title_claim_not_sku_price(self):
        self.assertEqual(title_quantity("6件套蛋糕拱形导向工具"), 6)
        self.assertEqual(title_quantity("2 PCS Cake Arch Guide Tool"), 2)
        self.assertEqual(title_quantity("热销2 PCS Cake Arch Guide Tool"), 2)
        self.assertIsNone(title_quantity("6寸蛋糕工具"))

    def test_numbers_missing_zero_invalid(self):
        self.assertEqual(number(0), 0)
        for value in (None, "", "nan", "inf", -1, True):
            self.assertIsNone(number(value))
        self.assertIsNone(number(0, positive=True))
        self.assertIsNone(number(101, maximum=100))

    def test_shell_detail_does_not_erase_metrics(self):
        shell = candidate(price=0, service_score=0, repurchase_rate=0, min_order_quantity=0,
                          sales_of_30d=0, service_score_detail=[], store_name=None, sku_count=0)
        self.save(SOURCE_FILE, {"calls": [{"tool": "ali1688_product_request", "retrieved_on": date.today().isoformat(), "result": {"data": shell}}]})
        c = build_screening(self.root)["candidates"][0]
        self.assertEqual(c["price"], 3.5)
        self.assertEqual(c["repurchase"], 40)
        self.assertTrue(c["empty_details"])
        self.assertIsNone(c["rating"])

    def test_shell_only_has_missing_not_zero_price(self):
        self.save("research_sources.json", {"detail": SEED, "supplier_candidates": [
            candidate(price=0, service_score=0, repurchase_rate=0, min_order_quantity=0, sales_of_30d=0)]})
        c = build_screening(self.root)["candidates"][0]
        self.assertIsNone(c["price"])
        self.assertIsNone(c["repurchase"])
        self.assertNotEqual(c["priority"], 0)

    def test_repurchased_zero_preserved_and_ratings_distinct(self):
        self.save("research_sources.json", {"detail": SEED, "supplier_candidates": [candidate(repurchase_rate=0)]})
        c = build_screening(self.root)["candidates"][0]
        self.assertEqual(c["repurchase"], 0)
        self.assertEqual(c["service"], 4.5)
        self.assertIsNone(c["rating"])

    def test_factory_claims_not_from_names_or_membership(self):
        self.save("research_sources.json", {"detail": SEED, "supplier_candidates": [
            candidate(store_name="超级工厂", seller_identities=["powerful_merchants", "tp_member"]),
            candidate("456", seller_identities=["super_factory"])]})
        by_id = {c["id"]: c for c in build_screening(self.root)["candidates"]}
        self.assertFalse(by_id["123"]["factory"])
        self.assertTrue(by_id["456"]["factory"])

    def test_risk_beats_price_factory_and_repurchase(self):
        self.save("research_sources.json", {"detail": SEED, "retrieved_on": date.today().isoformat(), "supplier_candidates": [
            candidate("1", price=0.1, repurchase_rate=99, seller_identities=["super_factory"], service_score_detail=[{"title": "品质体验", "score": 2}]),
            candidate("2")]})
        self.assertEqual(build_screening(self.root)["candidates"][0]["id"], "2")

    def test_dedup_offer_not_unknown_store(self):
        self.save(SOURCE_FILE, {"calls": [{"retrieved_on": date.today().isoformat(), "result": [candidate(), candidate("456")]}]})
        self.assertEqual(len(build_screening(self.root)["candidates"]), 2)

    def test_saved_mismatch_is_not_promoted(self):
        with (self.root / "supplier_quotes.csv").open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=["source_product_id", "match_status", "match_reason"])
            writer.writeheader()
            writer.writerow({"source_product_id": "123", "match_status": "material_mismatch", "match_reason": "Wrong material"})
        self.assertEqual(build_screening(self.root)["candidates"][0]["match"], "excluded")

    def test_escape_and_evidence_labels(self):
        self.save("research_sources.json", {"detail": SEED, "supplier_candidates": [candidate(
            title="Cake Arch Guide Tool <script>alert(1)</script>", photo="javascript:alert(1)")]})
        html = render_supplier_screening(build_screening(self.root), "B0TEST1234")
        self.assertNotIn("<script>alert", html)
        self.assertNotIn('src="javascript:', html)
        for text in ("商品展示价", "回头率", "好评率：未提供", "自有工厂：无已核验证据", "detail.1688.com/offer/123.html", "标题件数", "仅看已选"):
            self.assertIn(text, html)

    def test_collection_bounded_cached_and_quotes_untouched(self):
        class Client:
            def __init__(self): self.calls = []
            def call_tool(self, tool, args):
                self.calls.append((tool, args))
                return {"data": [candidate("123"), candidate("456"), candidate("789")]}
        client = Client()
        quotes = self.root / "supplier_quotes.csv"
        quotes.write_text("source_product_id,match_status\n", encoding="utf-8")
        before = quotes.read_bytes()
        collect_supplier_screening(self.root, client)
        self.assertEqual(len(client.calls), 3)
        collect_supplier_screening(self.root, client)
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(quotes.read_bytes(), before)

    def test_failures_preserve_leads_without_leaking_error(self):
        class Client:
            def call_tool(self, *args): raise RuntimeError("private credential URL")
        result = collect_supplier_screening(self.root, Client())
        self.assertEqual(result["candidates"][0]["price"], 3.5)
        self.assertNotIn("private credential", (self.root / SOURCE_FILE).read_text())


if __name__ == "__main__":
    unittest.main()
