#!/usr/bin/env python3
"""Repair legacy rotation states without refreshing their validation evidence."""
import argparse
from collections import Counter
from datetime import datetime
from pathlib import Path
import shutil

from category_shape_validation import read_csv, update_shape_archive
from workflow_storage import atomic_write_json, atomic_write_text, current_root, workflow_lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply after preserving the original file; default is a dry run.")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    with workflow_lock(root):
        live = current_root(root) / "archive/shape_opportunity_library.csv"
        before = read_csv(live)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        output = root / ".local/archive_repairs" / stamp
        output.mkdir(parents=True)
        shutil.copy2(live, output / "original.csv")
        shutil.copy2(live, output / "shape_opportunity_library.csv")
        update_shape_archive([], output, "rotation_repair_" + stamp)
        after = read_csv(output / "shape_opportunity_library.csv")
        original = {row["shape_archive_key"]: row for row in before}
        assert set(original) == {r["shape_archive_key"] for r in after}, "Repair must preserve every archive row"
        for row in after:
            old = original[row["shape_archive_key"]]
            assert row.get("archive_last_seen") == old.get("archive_last_seen"), "Repair cannot refresh evidence dates"
        report = {"applied": args.apply, "before": dict(Counter(r["archive_status"] for r in before)),
                  "after": dict(Counter(r["archive_status"] for r in after)), "backup":str(output / "original.csv")}
        if args.apply:
            atomic_write_text(live, (output / "shape_opportunity_library.csv").read_text())
        atomic_write_json(output / "repair.json", report)
        print(report)


if __name__ == "__main__":
    main()
