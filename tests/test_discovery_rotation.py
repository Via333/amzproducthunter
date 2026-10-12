#!/usr/bin/env python3
"""Offline checks for category rotation, permanent exclusions, and keyword search."""

from __future__ import annotations

import csv
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from discover_sorftime_opportunities import (balanced_candidate_sample, mark_category_scanned, rescan_priorities,
                                             select_categories)


ROOT = Path(__file__).resolve().parents[1]


def category_tree() -> dict:
    return {
        "Data": [
            {
                "NodeId": "root",
                "name": "Home & Kitchen",
                "children": [
                    {"NodeId": "a", "name": "Alpha Storage", "children": []},
                    {"NodeId": "b", "name": "Beta Storage", "children": []},
                    {"NodeId": "c", "name": "Gamma Storage", "children": []},
                    {"NodeId": "d", "name": "Delta Storage", "children": []},
                    {"NodeId": "x", "name": "Large Furniture", "children": []},
                ],
            }
        ]
    }


def rules() -> dict:
    return {
        "max_categories": 2,
        "category_seeds": [],
        "category_filters": {
            "min_depth": 1,
            "max_depth": 4,
            "leaf_only": True,
            "exclude_name_contains": [],
            "prefer_name_contains": ["home", "storage"],
        },
    }


class CategoryRotationTest(unittest.TestCase):
    def test_candidate_sample_round_robins_across_categories(self) -> None:
        candidates = [
            {"source_asin": "A1", "source_strategy": "category:Storage", "source_category_id": "A", "product_name": "A1", "target_price": 20},
            {"source_asin": "A2", "source_strategy": "category:Storage", "source_category_id": "A", "product_name": "A2", "target_price": 20},
            {"source_asin": "A3", "source_strategy": "category:Storage", "source_category_id": "A", "product_name": "A3", "target_price": 20},
            {"source_asin": "B1", "source_strategy": "category:Storage", "source_category_id": "B", "product_name": "B1", "target_price": 20},
            {"source_asin": "B2", "source_strategy": "category:Storage", "source_category_id": "B", "product_name": "B2", "target_price": 20},
            {"source_asin": "C1", "source_strategy": "category:Storage", "source_category_id": "C", "product_name": "C1", "target_price": 20},
        ]
        selected = balanced_candidate_sample(candidates, 4)
        self.assertEqual([row["source_asin"] for row in selected], ["A1", "B1", "C1", "A2"])
        self.assertEqual(len({row["source_category_id"] for row in selected}), 3)

    def test_duplicate_category_ids_use_one_scan_slot(self) -> None:
        tree = category_tree()
        tree["Data"][0]["children"].append({"NodeId": "a", "name": "Alpha Storage Duplicate", "children": []})
        selected = select_categories(tree, {**rules(), "max_categories": 10}, {}, {"path_contains": [{"term": "furniture"}]})
        selected_ids = [row["category_id"] for row in selected]
        self.assertEqual(len(selected_ids), len(set(selected_ids)))
        self.assertEqual(selected_ids.count("a"), 1)

    def test_never_scanned_categories_precede_rescans(self) -> None:
        scan_state: dict[str, dict[str, object]] = {}
        exclusions = {
            "path_contains": [
                {"term": "furniture", "type": "大件", "reason": "不适合个人卖家"},
            ]
        }

        first = select_categories(category_tree(), rules(), scan_state, exclusions)
        self.assertEqual([row["category_id"] for row in first], ["a", "b"])
        for row in first:
            mark_category_scanned(scan_state, row, 20, 2, "2026-08-01T08:30:00")

        second = select_categories(category_tree(), rules(), scan_state, exclusions)
        self.assertEqual([row["category_id"] for row in second], ["d", "c"])
        self.assertTrue(all(row["rotation_bucket"] == "never_scanned" for row in second))
        self.assertNotIn("x", [row["category_id"] for row in first + second])

    def test_oldest_scanned_category_is_selected_first(self) -> None:
        scan_state = {
            "a": {"scan_count": "2", "last_scanned_at": "2026-08-08T08:30:00"},
            "b": {"scan_count": "1", "last_scanned_at": "2026-07-01T08:30:00"},
            "c": {"scan_count": "1", "last_scanned_at": "2026-07-15T08:30:00"},
            "d": {"scan_count": "1", "last_scanned_at": "2026-08-01T08:30:00"},
        }
        selected = select_categories(category_tree(), rules(), scan_state, {"path_contains": [{"term": "furniture"}]})
        self.assertEqual([row["category_id"] for row in selected], ["b", "c"])

    def test_stale_pool_entries_are_rescanned_without_waiting_for_age(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            pool = Path(temp) / "shape_opportunity_library.csv"
            shapes = Path(temp) / "category_shape_validation.csv"
            with pool.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["source_category_id", "archive_status", "last_validated_at"])
                writer.writeheader()
                writer.writerows([
                    {"source_category_id": "c", "archive_status": "stale_needs_rescan", "last_validated_at": "2026-09-01 22:36:37"},
                    {"source_category_id": "d", "archive_status": "active", "last_validated_at": "2026-09-01 22:36:37"},
                    {"source_category_id": "a", "archive_status": "invalidated_by_rule", "last_validated_at": "2026-06-01 08:00:00"},
                ])
            with shapes.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["source_category_id", "shape_recommendation"])
                writer.writeheader()
                writer.writerow({"source_category_id": "b", "shape_recommendation": "Watch shape"})
            priorities = rescan_priorities(pool, shapes, 60, now=datetime(2026, 10, 12))
            # An active entry still waits for the rescan age; a stale one does not.
            self.assertEqual(rescan_priorities(pool, shapes, 60, now=datetime(2026, 11, 15)), {"c": 0, "d": 0, "b": 1})
        self.assertEqual(priorities, {"c": 0, "b": 1})
        scan_state = {key: {"scan_count": "1", "last_scanned_at": f"2026-09-0{index + 1}T08:30:00"}
                      for index, key in enumerate("abcd")}
        selected = select_categories(
            category_tree(),
            {**rules(), "rotation": {"new_category_quota": 0, "rescan_quota": 100}, "_rescan_priorities": priorities},
            scan_state, {"path_contains": [{"term": "furniture"}]})
        self.assertEqual([row["category_id"] for row in selected], ["c", "b"])


class KeywordSearchTest(unittest.TestCase):
    def test_saved_response_is_scored_and_archived(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)
            archive_root = output_root / "runs"
            index_path = output_root / "index.csv"
            completed = subprocess.run(
                [
                    "python3",
                    "keyword_opportunity_search.py",
                    "--keyword",
                    "desk cable management tray",
                    "--from-json",
                    "data/sorftime_response.example.json",
                    "--archive-root",
                    str(archive_root),
                    "--index",
                    str(index_path),
                    "--publish-root",
                    str(output_root / "published"),
                    "--no-dashboard",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            with index_path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["raw_result_count"], "2")
            self.assertEqual(rows[0]["eligible_candidate_count"], "2")
            ranked = list(archive_root.glob("desk-cable-management-tray/*/selection_ranked.csv"))
            self.assertEqual(len(ranked), 1)


if __name__ == "__main__":
    unittest.main()
