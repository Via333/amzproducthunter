"""Evidence-based attribution of recent launch traction, never causal proof."""

import argparse
import calendar
import hashlib
import json
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from statistics import median

from research_landscape import build_landscape, listing_date, optional_number, read_json

ROOT = Path(__file__).resolve().parent
MAX_EVIDENCE_AGE = 14
TOOLS = {
    "detail": ("product_detail", {}),
    "variations": ("product_variations", {"page": 1}),
    "price": ("product_trend", {"product_trend_type": "Price"}),
    "sales": ("product_trend", {"product_trend_type": "SalesVolume"}),
    "reviews": ("product_reviews", {"review_type": "Both"}),
}


def six_months_before(day):
    month = day.year * 12 + day.month - 1 - 6
    year, month = divmod(month, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def recent_candidates(landscape, as_of):
    start = six_months_before(as_of).isoformat()
    selected = {}
    for form in landscape.get("forms", []):
        for p in form.get("products", []):
            listed = listing_date(p.get("online_date"))
            sales = optional_number(p.get("monthly_sales"))
            if (re.fullmatch(r"B[A-Z0-9]{9}", str(p.get("asin", ""))) and listed
                    and start <= listed <= as_of.isoformat() and sales is not None and sales > 300):
                selected.setdefault(p["asin"], {**p, "monthly_sales": sales, "form": form["form"], "form_label": form["label"]})
    return sorted(selected.values(), key=lambda p: (-p["monthly_sales"], p["asin"]))


def fresh(day, as_of):
    parsed = listing_date(day)
    return bool(parsed and 0 <= (as_of - date.fromisoformat(parsed)).days <= MAX_EVIDENCE_AGE)


def evidence(root, study, asin, kind, as_of):
    path = study / "new_entrant_evidence" / f"{asin}_{kind}.json"
    payload = read_json(path)
    if payload:
        if (payload.get("tool") != TOOLS[kind][0] or payload.get("arguments", {}).get("asin") != asin
                or payload.get("arguments", {}).get("amz_site", "US") != "US"):
            return None
        if kind in {"sales", "price"} and payload["arguments"].get("product_trend_type") != TOOLS[kind][1]["product_trend_type"]:
            return None
        if not fresh(payload.get("retrieved_on"), as_of):
            return None
        if not valid_result(payload.get("result"), kind, asin):
            return None
        return {"payload": payload.get("result", {}), "path": str(path.relative_to(root)),
                "retrieved_on": payload["retrieved_on"]}
    if kind == "detail":
        path = root / ".local/sorftime_image_details/US" / f"{asin}.json"
        cache = read_json(path)
        if cache.get("data", {}).get("asin") == asin and fresh(cache.get("retrieved_at"), as_of):
            return {"payload": {"data": cache["data"]}, "path": str(path.relative_to(root)),
                    "retrieved_on": listing_date(cache["retrieved_at"])}
    return None


def valid_result(result, kind, asin):
    if not isinstance(result, dict):
        return False
    data = result.get("data")
    if kind == "detail":
        return isinstance(data, dict) and data.get("asin") == asin
    if not isinstance(data, list) or not data:
        return False
    if kind in {"variations", "reviews"}:
        return all(isinstance(row, dict) for row in data)
    return all(isinstance(row, str) for row in data)


def data_of(record, default):
    payload = (record or {}).get("payload", {})
    value = payload.get("data", default) if isinstance(payload, dict) else default
    return value if isinstance(value, type(default)) else default


def monthly_series(record, as_of):
    points = {}
    for row in data_of(record, []):
        match = re.fullmatch(r"(\d{4})年(\d{2})月=([\d,.]+)", str(row))
        if not match:
            continue
        year, month = int(match[1]), int(match[2])
        value = optional_number(match[3])
        if not 1 <= year <= 9999 or not 1 <= month <= 12 or value is None:
            continue
        period = f"{year:04}-{month:02}"
        if period < as_of.strftime("%Y-%m"):
            points[period] = value
    return [{"month": period, "value": value} for period, value in sorted(points.items())]


def review_day(value):
    value = str(value or "")
    if re.fullmatch(r"\d{8}", value):
        value = f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return listing_date(value)


def evidence_digest(gathered):
    snapshots = {k: {"payload": v["payload"], "retrieved_on": v["retrieved_on"]}
                 for k, v in gathered.items() if v}
    return hashlib.sha256(json.dumps(snapshots, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def review_matches(note, product, as_of, digest):
    snapshot = note.get("snapshot", {})
    return bool(fresh(note.get("reviewed_on"), as_of)
                and all(snapshot.get(key) == product.get(key) for key in ("online_date", "monthly_sales", "price"))
                and note.get("evidence_digest") == digest and note.get("sources"))


def analyze_new_entrants(root, study, landscape, as_of=None):
    as_of = as_of or date.today()
    candidates = recent_candidates(landscape, as_of)
    notes = read_json(study / "new_entrant_reviews.json").get("products", {})
    output = []
    for product in candidates:
        asin = product["asin"]
        gathered = {kind: evidence(root, study, asin, kind, as_of) for kind in TOOLS}
        detail = data_of(gathered["detail"], {})
        variants = data_of(gathered["variations"], [])
        target = next((v for v in variants if v.get("Asin", v.get("asin")) == asin), {})
        badge = optional_number(target.get("SalesAmount", target.get("sales_amount")))
        reviews = data_of(gathered["reviews"], [])
        dated = [review_day(r.get("review_date")) for r in reviews]
        dated = [d for d in dated if d and d <= as_of.isoformat()]
        before = sum(d < product["online_date"] for d in dated)
        prices = monthly_series(gathered["price"], as_of)
        sales = monthly_series(gathered["sales"], as_of)
        # A same-pack/form benchmark is only a price clue, not proof of identical specs.
        peers = next(f["products"] for f in landscape["forms"] if f["form"] == product["form"])
        comparables = [p for p in peers if p["asin"] != asin and product.get("pack_count")
                       and p.get("pack_count") == product["pack_count"] and (optional_number(p.get("price")) or 0) > 0]
        peer_price = median(p["price"] for p in comparables) if len(comparables) >= 3 else None
        price_change = prices[-1]["value"] / prices[0]["value"] - 1 if len(prices) >= 2 and prices[0]["value"] > 0 else None
        note = notes.get(asin, {})
        digest = evidence_digest(gathered)
        reviewed = review_matches(note, product, as_of, digest) and all(gathered.values())
        snapshot_date = product.get("metric_sources", {}).get("monthly_sales", {}).get("retrieved_on")
        snapshot_fresh = fresh(snapshot_date, as_of)
        blocked = product.get("screening_status") != "eligible"
        if blocked:
            gate, conclusion = "screening_pending", "基础筛选未通过，仅保留起量研究，不作为机会依据。"
        elif not snapshot_fresh:
            gate, conclusion = "needs_refresh", "初筛销量快照过期或日期缺失，需刷新后判断。"
        elif badge is None or badge <= 300:
            gate, conclusion = "sales_scope_unverified", "尚未确认该子款独立超过300；不能把链接级销量当作新品起量。"
        elif not reviewed:
            gate, conclusion = "needs_attribution", "已出现子款销量信号，起量归因和可复制性仍待核验。"
        else:
            gate = note.get("entry_gate", "needs_attribution")
            if gate not in {"brand_dependent", "price_pressure", "inherited_listing", "differentiation_to_validate", "needs_attribution", "independent_entry_supported"}:
                gate = "needs_attribution"
            if gate == "independent_entry_supported" and not note.get("replicability_evidence"):
                gate = "needs_attribution"
            conclusion = note.get("conclusion", "仍需验证可复制性。")
        output.append({
            **product, "brand": detail.get("brand") or product.get("brand") or "未提供",
            "parent_asin": detail.get("parent_asin") or product.get("parent_asin", ""),
            "detail_reviews": detail.get("review_count"), "child_sales_badge": badge,
            "child_properties": target.get("Property", target.get("property", "")),
            "variation_total": max([int(optional_number(v.get("ItemTotal", v.get("item_total"))) or 0) for v in variants] or [0]),
            "variants_returned": len(variants), "reviews_returned": len(reviews),
            "reviews_before_launch": before, "latest_review_date": max(dated) if dated else "",
            "price_series": prices, "sales_series": sales, "price_change": price_change,
            "same_pack_peer_count": len(comparables), "same_pack_peer_price": peer_price,
            "snapshot_date": snapshot_date or "未记录", "evidence_complete": all(gathered.values()),
            "evidence_digest": digest,
            "sources": [{"kind": k, "path": v["path"], "retrieved_on": v["retrieved_on"]} for k, v in gathered.items() if v],
            "reviewed": reviewed, "review_notes": note if reviewed else {},
            "entry_gate": gate, "entry_conclusion": conclusion,
            "fba_fee": optional_number(detail.get("fba_fee")),
        })
    forms = []
    for form in landscape.get("forms", []):
        items = [p for p in output if p["form"] == form["form"]]
        if items:
            supported = sum(p["entry_gate"] == "independent_entry_supported" for p in items)
            forms.append({"form": form["form"], "label": form["label"], "is_target": form.get("is_target", False), "count": len(items),
                          "independent_supported_count": supported,
                          "gates": dict(Counter(p["entry_gate"] for p in items)),
                          "conclusion": (f"有 {supported} 个经核验的独立切入案例；仍需目标产品的成本、需求与风险验证。" if supported else
                                         "这些起量样本尚不能证明普通新卖家可复制；需补独立新品、成本与差异化证据，不等于整个形态不可做。")})
    return {"as_of": as_of.isoformat(), "window_start": six_months_before(as_of).isoformat(),
            "months": 6, "sales_threshold_exclusive": 300, "products": output, "forms": forms,
            "method_note": "初筛按日期回溯六个月，使用窗口内上架且快照月销>300的ASIN。子款销量标记来自近15天内抓取的页面公开销量档位，不等于精确月销；-1表示未更新。价格与销量趋势剔除当月，上市首月可能不完整。",
            "limits": "起量原因是基于证据的机制判断，不是因果证明；品牌贡献、广告/站外投放占比未知。老链接评论不能直接归因于新子款；未证实创新不等于没有创新，没查到品牌证据也不等于无品牌力。"}


def apply_new_entrant_gate(summary, analysis):
    summary = dict(summary)
    summary["new_entrant_analysis"] = analysis
    target = [f for f in analysis["forms"] if f.get("is_target") and not f.get("independent_supported_count")]
    if not target or summary.get("archive_status") == "rejected":
        return summary
    patent = summary.get("required_checks", {}).get("checks", {}).get("patent", {})
    if patent.get("risk") == "high" or patent.get("clearance") == "blocked":
        return summary
    summary["decision"] = "新品起量可复制性待验证，暂缓立项"
    reason = "近半年起量样本尚未证明普通新卖家可复制；不能用品牌扩款、共享销量或利润未验证的低价销量代替切入证据。"
    previous = summary.get("decision_reason", "")
    if reason not in previous:
        summary["decision_reason"] = previous + reason
    return summary


def refresh_new_entrants(root, study, collect=False, max_calls=15):
    landscape = build_landscape(root, study)
    if collect:
        collect_evidence(root, study, landscape, max_calls)
    analysis = analyze_new_entrants(root, study, landscape)
    write_json(study / "new_entrant_analysis.json", analysis)
    summary = read_json(study / "research_summary.json")
    if summary:
        write_json(study / "research_summary.json", apply_new_entrant_gate(summary, analysis))
    return analysis


def entrants_markdown(analysis):
    lines = ["", "## 近半年新品起量核验", "",
             f'{analysis["window_start"]} 至 {analysis["as_of"]} 上架、快照月销 >300：{len(analysis["products"])} 个。',
             "", analysis["method_note"], ""]
    for p in analysis["products"]:
        note = p["review_notes"]
        lines.extend([f'### {p["brand"]} / {p["asin"]}', "", p["entry_conclusion"], ""])
        lines.extend(f'- {f["label"]}：{f["finding"]}' for f in note.get("factors", []))
        lines.extend(f'- 待核：{x}' for x in note.get("next_checks", []))
        for source in note.get("sources", []):
            lines.append(f'- 来源：[{source["title"]}]({source["url"]})' if source.get("url", "").startswith("https://")
                         else f'- 来源：{source["title"]} {source.get("note", "")}')
    lines.extend(["", analysis["limits"], ""])
    return lines


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def collect_evidence(root, study, landscape, max_calls=15, client=None, as_of=None):
    from sorftime_mcp_client import SorftimeMcpClient

    as_of = as_of or date.today()
    calls, reused, errors = 0, 0, []
    for product in recent_candidates(landscape, as_of):
        for kind, (tool, extra) in TOOLS.items():
            if evidence(root, study, product["asin"], kind, as_of):
                reused += 1
                continue
            if calls >= max_calls or errors:
                continue
            args = {"asin": product["asin"], "amz_site": "US", **extra}
            try:
                client = client or SorftimeMcpClient(max_attempts=1)
                calls += 1
                result = client.call_tool(tool, args)
                if not valid_result(result, kind, product["asin"]):
                    raise ValueError("Missing or invalid evidence")
                path = study / "new_entrant_evidence" / f'{product["asin"]}_{kind}.json'
                if path.is_file():
                    previous = read_json(path)
                    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                    write_json(path.parent / "history" / f"{path.stem}_{stamp}.json", previous)
                write_json(path, {"tool": tool, "arguments": args, "retrieved_on": as_of.isoformat(), "result": result})
            except Exception as exc:
                errors.append({"asin": product["asin"], "kind": kind, "error_type": type(exc).__name__})
    manifest = {"retrieved_on": as_of.isoformat(), "tool_calls": calls, "cache_reuses": reused,
                "max_calls": max_calls, "errors": errors}
    write_json(study / "new_entrant_collection.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--asin", required=True)
    parser.add_argument("--collect", action="store_true")
    parser.add_argument("--max-calls", type=int, default=15)
    args = parser.parse_args()
    if not re.fullmatch(r"B[A-Z0-9]{9}", args.asin.upper()) or args.max_calls < 0:
        parser.error("Invalid ASIN or call budget")
    study = ROOT / "research" / args.asin.upper()
    if not study.is_dir():
        parser.error("Research directory does not exist")
    landscape = build_landscape(ROOT, study)
    if args.collect:
        print(json.dumps(collect_evidence(ROOT, study, landscape, args.max_calls)))
    result = analyze_new_entrants(ROOT, study, landscape)
    write_json(study / "new_entrant_analysis.json", result)
    print(f'Recent launch candidates: {len(result["products"])}; reviewed: {sum(p["reviewed"] for p in result["products"])}')


if __name__ == "__main__":
    main()
