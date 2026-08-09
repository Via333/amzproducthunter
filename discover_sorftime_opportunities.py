#!/usr/bin/env python3
import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

from import_sorftime_candidates import (
    build_candidate,
    call_sorftime,
    dedupe_candidates,
    find_product_records,
    load_json,
    pick,
    to_float,
    write_candidates,
    run_scoring,
)


CATEGORY_ID_ALIASES = [
    "NodeId",
    "nodeId",
    "node_id",
    "category_id",
    "categoryId",
    "browse_node_id",
    "browseNodeId",
    "Id",
    "id",
]

CATEGORY_NAME_ALIASES = [
    "name",
    "Name",
    "CNName",
    "category_name",
    "categoryName",
    "title",
    "label",
]

CHILDREN_ALIASES = [
    "children",
    "child",
    "nodes",
    "items",
    "list",
    "subCategories",
    "sub_categories",
]

CATEGORY_STATE_FIELDS = [
    "category_id",
    "name",
    "path",
    "first_scanned_at",
    "last_scanned_at",
    "scan_count",
    "last_products_examined",
    "last_candidate_count",
    "lifetime_products_examined",
    "lifetime_candidate_count",
    "status",
]

CATEGORY_REPORT_FIELDS = [
    "category_id",
    "name",
    "path",
    "depth",
    "is_leaf",
    "selection_score",
    "rotation_bucket",
    "previous_last_scanned_at",
    "previous_scan_count",
    "products_examined",
    "candidate_count",
    "scan_completed_at",
]


def parse_args():
    parser = argparse.ArgumentParser(description="Discover Amazon product opportunities from Sorftime categories.")
    parser.add_argument("--rules", default="config/autodiscovery_rules.json", help="Discovery rules JSON.")
    parser.add_argument("--defaults", default="config/import_defaults.json", help="Candidate import defaults JSON.")
    parser.add_argument("--output", default="data/discovered_candidates.csv", help="Output candidates CSV.")
    parser.add_argument("--category-report", default="reports/discovered_categories.csv", help="Scanned category report.")
    parser.add_argument("--scan-state", default="archive/category_scan_state.csv", help="Persistent category rotation state.")
    parser.add_argument(
        "--category-exclusions",
        default="config/category_exclusions.json",
        help="Permanent category exclusion rules and reasons.",
    )
    parser.add_argument("--domain", help="Override Sorftime domain id.")
    parser.add_argument("--strategy", choices=["search", "category"], help="Discovery strategy.")
    parser.add_argument("--category-tree-json", help="Use a saved category tree JSON instead of calling Sorftime.")
    parser.add_argument("--products-json", help="Use one saved products JSON for offline mapping tests.")
    parser.add_argument("--score", action="store_true", help="Run product_selection.py after discovery.")
    parser.add_argument("--dry-run", action="store_true", help="List categories that would be scanned, without products.")
    return parser.parse_args()


def get_first(item, aliases, default=None):
    for alias in aliases:
        if alias in item and item[alias] not in (None, ""):
            return item[alias]
    lowered = {str(key).lower(): value for key, value in item.items()}
    for alias in aliases:
        value = lowered.get(alias.lower())
        if value not in (None, ""):
            return value
    return default


def get_children(item):
    for alias in CHILDREN_ALIASES:
        children = item.get(alias)
        if isinstance(children, list):
            return children
    return []


def find_category_roots(value):
    if isinstance(value, list):
        dict_items = [item for item in value if isinstance(item, dict)]
        if dict_items and any(get_first(item, CATEGORY_ID_ALIASES) and get_first(item, CATEGORY_NAME_ALIASES) for item in dict_items):
            return dict_items
        for item in value:
            found = find_category_roots(item)
            if found:
                return found
    elif isinstance(value, dict):
        for item in value.values():
            found = find_category_roots(item)
            if found:
                return found
    return []


def flatten_categories(nodes, parent_path="", depth=0):
    categories = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        category_id = get_first(node, CATEGORY_ID_ALIASES)
        name = get_first(node, CATEGORY_NAME_ALIASES)
        if not category_id or not name:
            continue
        path = " > ".join(part for part in [parent_path, str(name)] if part)
        children = get_children(node)
        categories.append(
            {
                "category_id": str(category_id),
                "name": str(name),
                "path": path,
                "depth": depth,
                "is_leaf": not children,
            }
        )
        categories.extend(flatten_categories(children, path, depth + 1))
    return categories


def category_preference_score(category, filters):
    name = category["path"].lower()
    min_depth = int(filters.get("min_depth", 0))
    max_depth = int(filters.get("max_depth", 99))
    if category["depth"] < min_depth or category["depth"] > max_depth:
        return None
    if filters.get("leaf_only") and not category["is_leaf"]:
        return None
    preferred = filters.get("prefer_name_contains", [])
    score = 0
    for term in preferred:
        if term.lower() in name:
            score += 10
    if category["is_leaf"]:
        score += 5
    score += category["depth"]
    return score


def read_category_scan_state(path):
    path = Path(path)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {row["category_id"]: row for row in csv.DictReader(handle) if row.get("category_id")}


def write_category_scan_state(scan_state, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(scan_state.values(), key=lambda row: (row.get("path", ""), row.get("category_id", "")))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CATEGORY_STATE_FIELDS, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in CATEGORY_STATE_FIELDS})


def as_int(value, default=0):
    try:
        return int(float(str(value or default).replace(",", "")))
    except (TypeError, ValueError):
        return default


def load_category_exclusions(path):
    path = Path(path)
    if not path.exists():
        return {}
    return load_json(path)


def category_exclusion_reason(category, filters, exclusions):
    path = category["path"].lower()
    for term in filters.get("exclude_name_contains", []):
        if str(term).lower() in path:
            return f"基础排除：路径包含 {term}"

    category_id = str(category["category_id"])
    for item in exclusions.get("category_ids", []):
        item = {"category_id": item, "reason": "手动排除"} if isinstance(item, str) else item
        if str(item.get("category_id", "")) == category_id:
            return str(item.get("reason") or "手动排除")

    for item in exclusions.get("path_contains", []):
        item = {"term": item, "reason": "永久排除"} if isinstance(item, str) else item
        term = str(item.get("term", "")).strip().lower()
        if term and term in path:
            reason = str(item.get("reason") or "永久排除")
            category_type = str(item.get("type") or "规则")
            return f"{category_type}：{reason}（匹配 {item.get('term')}）"
    return ""


def bootstrap_scan_state(scan_state, category_report_path):
    if scan_state:
        return scan_state
    report_path = Path(category_report_path)
    if not report_path.exists():
        return scan_state
    scanned_at = datetime.fromtimestamp(report_path.stat().st_mtime).isoformat(timespec="seconds")
    with report_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            category_id = str(row.get("category_id", "")).strip()
            if not category_id:
                continue
            scan_state[category_id] = {
                "category_id": category_id,
                "name": row.get("name", ""),
                "path": row.get("path", ""),
                "first_scanned_at": row.get("scan_completed_at") or scanned_at,
                "last_scanned_at": row.get("scan_completed_at") or scanned_at,
                "scan_count": max(1, as_int(row.get("previous_scan_count"), 0) + 1),
                "last_products_examined": row.get("products_examined", ""),
                "last_candidate_count": row.get("candidate_count", ""),
                "lifetime_products_examined": row.get("products_examined", ""),
                "lifetime_candidate_count": row.get("candidate_count", ""),
                "status": "scanned",
            }
    return scan_state


def select_categories(category_tree, rules, scan_state=None, exclusions=None):
    scan_state = scan_state or {}
    exclusions = exclusions or {}
    seeds = rules.get("category_seeds", [])
    if seeds:
        return [
            {
                "category_id": str(seed["category_id"]),
                "name": seed.get("name", str(seed["category_id"])),
                "path": seed.get("path", seed.get("name", str(seed["category_id"]))),
                "depth": int(seed.get("depth", 0)),
                "is_leaf": True,
                "selection_score": 100,
                "rotation_bucket": "manual_seed",
                "previous_last_scanned_at": scan_state.get(str(seed["category_id"]), {}).get("last_scanned_at", ""),
                "previous_scan_count": as_int(scan_state.get(str(seed["category_id"]), {}).get("scan_count")),
            }
            for seed in seeds
        ][: int(rules["max_categories"])]

    roots = find_category_roots(category_tree)
    categories = flatten_categories_from_any_shape(roots)
    selected_by_id = {}
    filters = rules["category_filters"]
    for category in categories:
        if category_exclusion_reason(category, filters, exclusions):
            continue
        score = category_preference_score(category, filters)
        if score is None:
            continue
        previous = scan_state.get(category["category_id"], {})
        previous_scan_count = as_int(previous.get("scan_count"))
        candidate = {
            **category,
            "selection_score": score,
            "rotation_bucket": "never_scanned" if previous_scan_count == 0 else "oldest_rescan",
            "previous_last_scanned_at": previous.get("last_scanned_at", ""),
            "previous_scan_count": previous_scan_count,
        }
        existing = selected_by_id.get(category["category_id"])
        if existing is None or (candidate["selection_score"], candidate["depth"]) > (
            existing["selection_score"],
            existing["depth"],
        ):
            selected_by_id[category["category_id"]] = candidate
    selected = list(selected_by_id.values())
    selected.sort(
        key=lambda item: (
            0 if item["previous_scan_count"] == 0 else 1,
            item["previous_last_scanned_at"] or "",
            -item["selection_score"],
            item["path"],
        )
    )
    return selected[: int(rules["max_categories"])]


def flatten_categories_from_any_shape(roots):
    if roots and any("ParentId" in item or "parentId" in item for item in roots):
        return flatten_flat_categories(roots)
    return flatten_categories(roots)


def flatten_flat_categories(items):
    by_id = {}
    for item in items:
        item_id = get_first(item, ["Id", "id"])
        node_id = get_first(item, CATEGORY_ID_ALIASES)
        name = get_first(item, CATEGORY_NAME_ALIASES)
        if item_id is None or not node_id or not name:
            continue
        by_id[str(item_id)] = {
            "item_id": str(item_id),
            "parent_id": str(get_first(item, ["ParentId", "parentId"], "0")),
            "category_id": str(node_id),
            "name": str(name),
        }

    def build_path(item, seen=None):
        seen = seen or set()
        if item["item_id"] in seen:
            return item["name"], 0
        seen.add(item["item_id"])
        parent = by_id.get(item["parent_id"])
        if not parent:
            return item["name"], 0
        parent_path, parent_depth = build_path(parent, seen)
        return f"{parent_path} > {item['name']}", parent_depth + 1

    parent_ids = {item["parent_id"] for item in by_id.values()}
    categories = []
    for item in by_id.values():
        path, depth = build_path(item)
        categories.append(
            {
                "category_id": item["category_id"],
                "name": item["name"],
                "path": path,
                "depth": depth,
                "is_leaf": item["item_id"] not in parent_ids,
            }
        )
    return categories


def replace_placeholders(value, category, page):
    if isinstance(value, dict):
        return {key: replace_placeholders(item, category, page) for key, item in value.items()}
    if isinstance(value, list):
        return [replace_placeholders(item, category, page) for item in value]
    if isinstance(value, str):
        if value == "{page}":
            return page
        if value == "{category_id}":
            return category["category_id"]
        return (
            value.replace("{category_id}", category["category_id"])
            .replace("{category_name}", category["name"])
            .replace("{page}", str(page))
        )
    return value


def product_passes_filters(candidate, product_filters):
    title = str(candidate["product_name"]).lower()
    category = str(candidate["category"]).lower()
    brand = candidate_brand(candidate)
    if any(term.lower() in title for term in product_filters.get("exclude_title_contains", [])):
        return False
    if any(term.lower() in brand for term in product_filters.get("exclude_brand_contains", [])):
        return False
    if any(term.lower() in category for term in product_filters.get("exclude_category_contains", [])):
        return False
    price = to_float(candidate["target_price"])
    sales = to_float(candidate["est_monthly_sales"])
    reviews = to_float(candidate["avg_review_count"])
    rating = to_float(candidate["avg_rating"])
    if price < product_filters["min_price"] or price > product_filters["max_price"]:
        return False
    if sales < product_filters["min_monthly_sales"] or sales > product_filters["max_monthly_sales"]:
        return False
    if reviews > product_filters["max_review_count"]:
        return False
    if rating and (rating < product_filters["min_rating"] or rating > product_filters["max_rating"]):
        return False
    return True


def candidate_brand(candidate):
    notes = str(candidate.get("notes", "") or "")
    lowered = notes.lower()
    marker = "brand "
    if marker not in lowered:
        return ""
    after = notes[lowered.index(marker) + len(marker) :]
    return after.split(";")[0].strip().lower()


def merge_product_filters(base_filters, strategy_filters=None):
    filters = {key: value for key, value in base_filters.items()}
    for key, value in (strategy_filters or {}).items():
        if isinstance(value, list) and key.startswith("exclude_"):
            filters[key] = filters.get(key, []) + value
        else:
            filters[key] = value
    return filters


def iter_search_strategies(rules):
    base_cfg = rules.get("product_search", {})
    configured = rules.get("product_search_strategies") or []
    if not configured:
        configured = [{"name": base_cfg.get("name", "default")}]
    for index, strategy in enumerate(configured, start=1):
        cfg = {key: value for key, value in base_cfg.items()}
        cfg.update(strategy)
        cfg.setdefault("name", f"strategy_{index}")
        cfg.setdefault("method", base_cfg.get("method", "ProductSearch"))
        cfg.setdefault("pages", base_cfg.get("pages", 1))
        cfg.setdefault("payload_template", base_cfg.get("payload_template", {}))
        yield cfg


def collect_products_by_search(rules, defaults, domain):
    candidates = []
    for search_cfg in iter_search_strategies(rules):
        strategy_name = search_cfg["name"]
        product_filters = merge_product_filters(rules["product_filters"], search_cfg.get("product_filters"))
        pages = int(search_cfg.get("pages", 1))
        for page in range(1, pages + 1):
            payload = replace_placeholders(search_cfg["payload_template"], {"category_id": "", "name": "", "path": ""}, page)
            response = call_sorftime(search_cfg["method"], json.dumps(payload, ensure_ascii=False), domain)
            records = find_product_records(response)
            page_candidates = []
            for item in records:
                candidate = build_candidate(item, defaults)
                candidate["source_strategy"] = strategy_name
                if product_passes_filters(candidate, product_filters):
                    page_candidates.append(candidate)
            candidates.extend(page_candidates)
            print(f"{strategy_name} page {page}: {len(page_candidates)} candidates")
    return candidates


def collect_products_for_category(category, rules, defaults, domain):
    product_cfg = rules["category_products"]
    products = []
    examined = 0
    max_products = int(rules["products_per_category"])
    pages = int(product_cfg.get("pages", 1))
    for page in range(1, pages + 1):
        payload = replace_placeholders(product_cfg["payload_template"], category, page)
        response = call_sorftime(product_cfg["method"], json.dumps(payload, ensure_ascii=False), domain)
        records = find_product_records(response)
        remaining = max_products - examined
        if remaining <= 0:
            break
        page_records = records[:remaining]
        examined += len(page_records)
        for item in page_records:
            product_category = pick(item, "category", category["path"])
            candidate = build_candidate(item, defaults, product_category)
            candidate["source_strategy"] = f"category:{category['name']}"
            if product_passes_filters(candidate, rules["product_filters"]):
                products.append(candidate)
        if not page_records:
            break
    return products, examined


def write_category_report(categories, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CATEGORY_REPORT_FIELDS, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for category in categories:
            writer.writerow({field: category.get(field, "") for field in CATEGORY_REPORT_FIELDS})


def mark_category_scanned(scan_state, category, products_examined, candidate_count, scanned_at):
    existing = scan_state.get(category["category_id"], {})
    scan_state[category["category_id"]] = {
        "category_id": category["category_id"],
        "name": category["name"],
        "path": category["path"],
        "first_scanned_at": existing.get("first_scanned_at") or scanned_at,
        "last_scanned_at": scanned_at,
        "scan_count": as_int(existing.get("scan_count")) + 1,
        "last_products_examined": products_examined,
        "last_candidate_count": candidate_count,
        "lifetime_products_examined": as_int(existing.get("lifetime_products_examined")) + products_examined,
        "lifetime_candidate_count": as_int(existing.get("lifetime_candidate_count")) + candidate_count,
        "status": "scanned",
    }


def main():
    args = parse_args()
    rules = load_json(args.rules)
    defaults = load_json(args.defaults)
    domain = args.domain or rules["domain"]
    strategy = args.strategy or rules.get("strategy", "search")
    defaults["marketplace"] = rules.get("marketplace", defaults["marketplace"])

    candidates = []
    if args.products_json:
        records = find_product_records(load_json(args.products_json))
        for item in records:
            candidate = build_candidate(item, defaults)
            if product_passes_filters(candidate, rules["product_filters"]):
                candidates.append(candidate)
    elif strategy == "search":
        if args.dry_run:
            print("ProductSearch strategy does not need category discovery.")
            return
        candidates = collect_products_by_search(rules, defaults, domain)
    else:
        scan_state = read_category_scan_state(args.scan_state)
        scan_state = bootstrap_scan_state(scan_state, args.category_report)
        exclusions = load_category_exclusions(args.category_exclusions)
        if args.category_tree_json:
            category_tree = load_json(args.category_tree_json)
        else:
            tree_cfg = rules["category_tree"]
            category_tree = call_sorftime(tree_cfg["method"], json.dumps(tree_cfg["payload"], ensure_ascii=False), domain)

        categories = select_categories(category_tree, rules, scan_state, exclusions)
        write_category_report(categories, args.category_report)
        print(f"Selected {len(categories)} categories. Category report: {args.category_report}")

        if args.dry_run:
            return

        for category in categories:
            category_candidates, products_examined = collect_products_for_category(category, rules, defaults, domain)
            candidates.extend(category_candidates)
            scanned_at = datetime.now().isoformat(timespec="seconds")
            category["products_examined"] = products_examined
            category["candidate_count"] = len(category_candidates)
            category["scan_completed_at"] = scanned_at
            mark_category_scanned(scan_state, category, products_examined, len(category_candidates), scanned_at)
            write_category_scan_state(scan_state, args.scan_state)
            print(f"{category['path']}: examined {products_examined}, {len(category_candidates)} candidates")
        write_category_report(categories, args.category_report)
        print(f"Category rotation state: {args.scan_state} ({len(scan_state)} scanned categories)")

    candidates = dedupe_candidates(candidates)[: int(rules["max_candidates"])]
    if not candidates:
        raise SystemExit("No candidates passed filters. Loosen product filters or check Sorftime response shape.")

    output_path = Path(args.output)
    write_candidates(candidates, output_path)
    print(f"Discovered {len(candidates)} candidates into {output_path}")
    if args.score:
        run_scoring(output_path)


if __name__ == "__main__":
    main()
