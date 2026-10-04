"""Complete research gallery images without rerunning market research."""

import argparse
import csv
import io
import json
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse, urlunparse

from product_opportunity_research import pack_count
from research_landscape import build_landscape
from sorftime_mcp_client import SorftimeMcpClient

ROOT = Path(__file__).resolve().parent


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def local_image(root, value):
    if not value:
        return None
    path = (root / value).resolve()
    return path if path.is_relative_to(root.resolve()) and path.is_file() and path.stat().st_size else None


def local_images_by_asin(root):
    images = {}
    for path in (root / "research").glob("*/images/*"):
        match = re.search(r"(B[A-Z0-9]{9})(?:\.[A-Za-z]+)$", path.name)
        if match and local_image(root, str(path)):
            images.setdefault(match[1], path.resolve())
    return images


def gallery_products(products, landscape, manifest=None, root=ROOT, *, scope="current"):
    """Keep snapshot and current grouping distinct; image collection defaults to current."""
    if scope not in {"current", "snapshot"}:
        raise ValueError("unknown gallery scope")
    originals = {p["asin"]: p for p in products if p.get("asin")}
    result = []
    seen = set()
    for form in landscape.get("forms", []) if scope == "current" else []:
        for product in form["products"]:
            asin = product["asin"]
            if asin in seen:
                continue
            seen.add(asin)
            row = {**product, **originals.get(asin, {})}
            row.update(product_form=form["form"], product_form_label=form["label"],
                       listing_url=f"https://www.amazon.com/dp/{asin}")
            row.setdefault("pack_count", pack_count(row.get("title", "")))
            result.append(row)
    if not result:
        result = [dict(row) for row in originals.values()]
    manifest = manifest or {}
    local_images = local_images_by_asin(root)
    for row in result:
        row.setdefault("listing_url", f"https://www.amazon.com/dp/{row['asin']}")
        saved = manifest.get("items", {}).get(row["asin"], {})
        if local_image(root, saved.get("image_file")):
            row["image_file"] = saved["image_file"]
        elif not local_image(root, row.get("image_file")):
            shared = local_images.get(row["asin"])
            row["image_file"] = str(shared.relative_to(root.resolve())) if shared else ""
        row["image_status"] = "ready" if row.get("image_file") else saved.get("status", "not_collected")
        if saved.get("main_image_url"):
            row["main_image_url"] = saved["main_image_url"]
    seed = landscape.get("seed_asin")
    result.sort(key=lambda row: (row["asin"] != seed, -float(row.get("monthly_sales") or 0)))
    return result


def image_urls(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = [value]
    if not isinstance(value, list):
        return []
    urls = []
    for url in value:
        if isinstance(url, str) and allowed_image_url(url):
            parsed = urlparse(url)
            urls.append(urlunparse(parsed._replace(path=quote(parsed.path, safe="/%+"), fragment="")))
    return list(dict.fromkeys(urls))


def representative_products(products):
    """Choose one existing member per exact form, preferring available images."""
    def order(row):
        local = bool(row.get("web_image_url") or row.get("image_file"))
        remote = bool(image_urls(row.get("main_image_url")))
        try:
            sales = float(row.get("monthly_sales") or 0)
        except (TypeError, ValueError):
            sales = 0
        return (0 if local else 1 if remote else 2,
                {"seed": 0, "direct": 1}.get(row.get("competitor_type"), 2),
                -sales, str(row.get("asin", "")))

    examples = {}
    for row in sorted(products, key=order):
        form = str(row.get("product_form") or "").strip()
        if form and row.get("asin"):
            examples.setdefault(form, row)
    return examples


def allowed_image_url(url):
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return parsed.scheme == "https" and (host == "media-amazon.com" or host.endswith(".media-amazon.com")
                                         or host == "images-amazon.com" or host.endswith(".images-amazon.com")
                                         or host == "ssl-images-amazon.com" or host.endswith(".ssl-images-amazon.com"))


def download_image(url, destination):
    if not allowed_image_url(url):
        raise ValueError("unsupported_image_host")
    request = urllib.request.Request(url, headers={"User-Agent": "amz-product-hunter/1.0"})
    with urllib.request.urlopen(request, timeout=25) as response:
        if not allowed_image_url(response.url):
            raise ValueError("unsupported_redirect")
        data = response.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise ValueError("image_too_large")
    # Pillow validates bytes before persisting; never save HTML as an image.
    from PIL import Image
    with Image.open(io.BytesIO(data)) as image:
        image.verify()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)


def detail_records(value):
    if isinstance(value, dict):
        if value.get("asin") and (value.get("main_image") or value.get("main_image_url")):
            yield value
        else:
            for child in value.values():
                yield from detail_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from detail_records(child)


def complete_images(root, research_dir, *, site="US", max_calls=100, retry_failed=False,
                    scope="current", representatives_only=False, client=None, downloader=download_image):
    root, research_dir = root.resolve(), research_dir.resolve()
    with (research_dir / "top_products.csv").open(encoding="utf-8-sig", newline="") as handle:
        products = list(csv.DictReader(handle))
    manifest_path = research_dir / "image_manifest.json"
    manifest = read_json(manifest_path)
    manifest.setdefault("items", {})
    landscape = build_landscape(root, research_dir) if scope == "current" else {}
    rows = gallery_products(products, landscape, manifest, root, scope=scope)
    if representatives_only:
        rows = list(representative_products(rows).values())
    image_cache = local_images_by_asin(root)
    detail_cache = {}
    for pattern in ("*/research_sources.json", "*/detail_*.json"):
        for path in (root / "research").glob(pattern):
            for detail in detail_records(read_json(path)):
                detail_cache.setdefault(detail["asin"], detail)
    call_count = reused = downloads = failures = consecutive_errors = 0
    if manifest.get("last_run"):
        manifest.setdefault("previous_runs", []).append(manifest["last_run"])
    manifest["last_run"] = {"started_at": datetime.now().isoformat(timespec="seconds"), "site": site,
                            "max_calls": max_calls, "tool_calls": 0, "scope": scope,
                            "representatives_only": representatives_only}
    for index, row in enumerate(rows, 1):
        asin = row["asin"]
        if not re.fullmatch(r"B[A-Z0-9]{9}", asin):
            continue
        old = manifest["items"].get(asin, {})
        path = local_image(root, row.get("image_file")) or image_cache.get(asin)
        item = {**old, "checked_at": datetime.now().isoformat(timespec="seconds")}
        if path:
            item.update(status="ready", image_file=str(path.relative_to(root)), source="local_cache")
            reused += 1
        elif old.get("status") in {"no_image", "detail_failed", "download_failed"} and not retry_failed:
            item = old
        else:
            cache_path = root / ".local" / "sorftime_image_details" / site / f"{asin}.json"
            cached = read_json(cache_path)
            detail = cached.get("data") or detail_cache.get(asin, {})
            urls = image_urls(old.get("main_image_url") or row.get("main_image_url"))
            urls = list(dict.fromkeys(urls + image_urls(detail.get("main_image") or detail.get("main_image_url"))))
            if not urls and (not cached or retry_failed) and call_count < max_calls:
                client = client or SorftimeMcpClient(timeout=30, max_attempts=1)
                call_count += 1
                try:
                    raw = client.call_tool("product_detail", {"asin": asin, "amz_site": site})
                    detail = raw.get("data", raw) if isinstance(raw, dict) else {}
                    if not isinstance(detail, dict) or detail.get("asin") != asin:
                        raise ValueError("detail_asin_mismatch")
                    cached = {"data": detail, "retrieved_at": item["checked_at"]}
                    save_json(cache_path, cached)
                    urls = image_urls(detail.get("main_image") or detail.get("main_image_url"))
                    consecutive_errors = 0
                except Exception as exc:
                    # Do not persist raw MCP exceptions: transport URLs may include account keys.
                    item.update(status="detail_failed", error_type=type(exc).__name__)
                    consecutive_errors += 1
                time.sleep(0.35)
            if urls:
                destination = research_dir / "images" / f"{asin}.jpg"
                item.update(main_image_url=urls[0], source="sorftime_product_detail")
                for url in urls[:3]:
                    try:
                        downloader(url, destination)
                        item.update(status="ready", image_file=str(destination.relative_to(root)), main_image_url=url)
                        item.pop("error_type", None)
                        downloads += 1
                        break
                    except Exception as exc:
                        item.update(status="download_failed", error_type=type(exc).__name__)
            elif item.get("status") != "detail_failed":
                item["status"] = "budget_deferred" if call_count >= max_calls and not cached else "no_image"
        failures += item.get("status") != "ready"
        manifest["items"][asin] = item
        manifest["last_run"].update(tool_calls=call_count, reused=reused, downloaded=downloads,
                                    unresolved=failures, processed=index, total=len(rows))
        save_json(manifest_path, manifest)
        print(f"[{index}/{len(rows)}] {asin}: {item['status']} (MCP {call_count}, reused {reused}, downloaded {downloads})", flush=True)
        if consecutive_errors >= 3:
            print("Stopped after 3 consecutive MCP errors; rerun explicitly to retry.", flush=True)
            break
    manifest["last_run"]["completed_at"] = datetime.now().isoformat(timespec="seconds")
    save_json(manifest_path, manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asin", required=True)
    parser.add_argument("--site", default="US", choices=["US"])
    parser.add_argument("--max-calls", type=int, default=100)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--scope", choices=["current", "snapshot"], default="current")
    parser.add_argument("--representatives-only", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"B[A-Z0-9]{9}", args.asin) or args.max_calls < 0:
        parser.error("Expected an ASIN and nonnegative call budget")
    try:
        from PIL import Image
    except ImportError:
        parser.error("Pillow is required. Use the configured image Python runtime; no MCP calls were made.")
    result = complete_images(ROOT, ROOT / "research" / args.asin, site=args.site,
                             max_calls=args.max_calls, retry_failed=args.retry_failed,
                             scope=args.scope, representatives_only=args.representatives_only)
    print(json.dumps(result["last_run"], ensure_ascii=False))


if __name__ == "__main__":
    main()
