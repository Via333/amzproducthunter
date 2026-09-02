import json
import unittest
from pathlib import Path

from build_category_research_page import build_html


ROOT = Path(__file__).resolve().parents[1]


class CategoryResearchPageTest(unittest.TestCase):
    def test_shape_gallery_has_filter_and_local_images(self):
        data = json.loads((ROOT / "research/category_553582_analysis.json").read_text(encoding="utf-8"))
        gallery = data["shape_gallery"]
        html = build_html(data)

        self.assertGreaterEqual(len({row["form"] for row in gallery}), 5)
        self.assertIn('id="shapeFilter"', html)
        self.assertIn("磁吸腰带工具夹", html)
        self.assertIn('id="validation-tracks"', html)
        self.assertIn("阶段 1 已通过；阶段 2 待完成", html)
        self.assertIn("../research/B0FXG8J58Q.html", html)
        for row in gallery:
            self.assertTrue((ROOT / "web/category" / row["image"]).is_file())
            self.assertIn(row["image"], html)


if __name__ == "__main__":
    unittest.main()
