import re
import shutil
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import build_dashboard  # noqa: E402

REPO = Path(__file__).resolve().parents[1]

# Elements the static scripts look up by id; the page must keep them.
SCRIPT_CONTRACT = [
    "shapeValidationList", "shapeSearch", "shapeRecommendation", "visibleShapeOpportunityCount",
    "workbenchConnection", "workbenchBody", "workbenchNotice", "favoriteCount", "jobCount", "dismissalCount",
    "keywordSearchForm", "keywordSearchInput", "keywordSearchSubmit", "keywordSearchStatus", "keywordSearchJobs",
    "archiveBody", "archiveSearch", "archiveStatus", "productResearchArchiveBody", "categoryHealthBody",
]
# Anchors that research pages and scripts link to.
LINKED_ANCHORS = ["overview", "shape-validation", "archive", "research-archive", "category-research-archive",
                  "research-workbench", "keyword-search", "discovery", "logic"]


class DashboardLayoutTest(unittest.TestCase):
    def build_empty_page(self) -> str:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            shutil.copytree(REPO / "config", root / "config")
            (root / "web").mkdir()
            for asset in (REPO / "web").glob("*.js"):
                shutil.copy2(asset, root / "web" / asset.name)
            with patch.object(build_dashboard, "ROOT", root), patch.object(build_dashboard, "WEB_DIR", root / "web"), \
                    patch.object(build_dashboard, "SOURCE_ROOT", root):
                return build_dashboard.build_html()

    def test_page_keeps_script_contract_and_anchors(self):
        html = self.build_empty_page()
        ids = set(re.findall(r'id="([^"]+)"', html))
        for element_id in SCRIPT_CONTRACT + LINKED_ANCHORS:
            self.assertIn(element_id, ids, element_id)
        self.assertIn('src="labels.js?v=', html)
        self.assertLess(html.index('src="labels.js'), html.index('src="workbench.js'))

    def test_sections_follow_decision_order(self):
        html = self.build_empty_page()
        order = [html.index(f'<section id="{name}"') for name in
                 ["shape-validation", "archive", "research-archive", "keyword-search", "discovery", "logic"]]
        self.assertEqual(order, sorted(order))

    def test_verdict_filter_shows_chinese_labels_in_decision_order(self):
        options = build_dashboard.render_verdict_options(["Reject category/form", "Watch shape", "Shape opportunity"])
        values = re.findall(r'<option value="([^"]*)">([^<]*)</option>', options)
        self.assertEqual(values, [("", "全部结论"), ("Shape opportunity", "可以研究"),
                                  ("Watch shape", "待核对"), ("Reject category/form", "已排除")])


if __name__ == "__main__":
    unittest.main()
