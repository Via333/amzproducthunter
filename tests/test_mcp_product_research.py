import unittest

from mcp_product_research import direct_supplier_matches, pack_count, product_form


class McpProductResearchTest(unittest.TestCase):
    def test_classifies_flexible_magnetic_tool_mat_as_direct(self) -> None:
        title = "3Pcs Magnetic Tool Mat with Telescoping Magnetic Pickup Tool"

        self.assertEqual(product_form(title), "flexible magnetic tool mat set")
        self.assertEqual(pack_count(title), 3)

    def test_excludes_electronics_project_mat_from_direct_form(self) -> None:
        title = "Magnetic Project Mat for Phone Repair and Electronics Repair"

        self.assertEqual(product_form(title), "adjacent/noise")

    def test_keeps_only_1688_magnetic_tool_mat_matches(self) -> None:
        rows = [
            {"title": "汽修磁吸工具垫三件套", "price": 18.8},
            {"title": "不锈钢磁性零件碗", "price": 7.5},
            {"title": "硅胶维修工具垫", "price": 9.9},
        ]

        self.assertEqual(direct_supplier_matches(rows), [rows[0]])


if __name__ == "__main__":
    unittest.main()
