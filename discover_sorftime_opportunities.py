#!/usr/bin/env python3
import argparse
import csv
import json
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


def parse_args():
    parser = argparse.ArgumentParser(description="Discover Amazon product opportunities from Sorftime categories.")
    parser.add_argument("--rules", default="config/autodiscovery_rules.json", help="Discovery rules JSON.")
    parser.add_argument("--defaults", default="config/import_defaults.json", help="Candidate import defaults JSON.")
    parser.add_argument("--output", default="data/discovered_candidates.csv", help="Output candidates CSV.")
    parser.add_argument("--category-report", default="reports/discovered_categories.csv", help="Scanned category report.")
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
    excluded = filters.get("exclude_name_contains", [])
    if any(term.lower() in name for term in excluded):
        return None
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


def select_categories(category_tree, rules):
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
            }
            for seed in seeds
        ][: int(rules["max_categories"])]

    roots = find_category_roots(category_tree)
    categories = flatten_categories_from_any_shape(roots)
    selected = []
    filters = rules["category_filters"]
    for category in categories:
        score = category_preference_score(category, filters)
        if score is None:
            continue
        selected.append({**category, "selection_score": score})
    selected.sort(key=lambda item: item["selection_score"], reverse=True)
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
    pages = int(product_cfg.get("pages", 1))
    for page in range(1, pages + 1):
        payload = replace_placeholders(product_cfg["payload_template"], category, page)
        response = call_sorftime(product_cfg["method"], json.dumps(payload, ensure_ascii=False), domain)
        records = find_product_records(response)
        for item in records[: int(rules["products_per_category"])]:
            product_category = pick(item, "category", category["path"])
            candidate = build_candidate(item, defaults, product_category)
            candidate["source_strategy"] = f"category:{category['name']}"
            if product_passes_filters(candidate, rules["product_filters"]):
                products.append(candidate)
    return products


def write_category_report(categories, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["category_id", "name", "path", "depth", "is_leaf", "selection_score"]
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for category in categories:
            writer.writerow({field: category.get(field, "") for field in fields})


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
        if args.category_tree_json:
            category_tree = load_json(args.category_tree_json)
        else:
            tree_cfg = rules["category_tree"]
            category_tree = call_sorftime(tree_cfg["method"], json.dumps(tree_cfg["payload"], ensure_ascii=False), domain)

        categories = select_categories(category_tree, rules)
        write_category_report(categories, args.category_report)
        print(f"Selected {len(categories)} categories. Category report: {args.category_report}")

        if args.dry_run:
            return

        for category in categories:
            category_candidates = collect_products_for_category(category, rules, defaults, domain)
            candidates.extend(category_candidates)
            print(f"{category['path']}: {len(category_candidates)} candidates")

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
