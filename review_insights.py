"""Validate semantic review judgments against an unchanged, deduplicated corpus.

Ratings are source metadata, never a classifier. Unreviewed content stays pending.
"""

from __future__ import annotations

import hashlib
import json
import re


SOURCE_FIELDS = ("asin", "review_target_asin", "review_title", "review_text", "rating", "review_date")
KINDS = ("positive", "negative", "pain")


def snapshot_hash(reviews: list[dict]) -> str:
    records = sorted(tuple(str(row.get(key) or "") for key in SOURCE_FIELDS) for row in reviews)
    return hashlib.sha256(json.dumps(records, ensure_ascii=False).encode("utf-8")).hexdigest()


def review_key(row: dict) -> str:
    text = "\n".join(re.sub(r"\s+", " ", str(row.get(key) or "")).strip()
                     for key in ("review_title", "review_text"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compile_review_insights(reviews: list[dict], annotations: dict | None) -> dict:
    corpus = {}
    for row in reviews:
        if not str(row.get("review_text") or row.get("review_title") or "").strip():
            continue
        key = review_key(row)
        item = corpus.setdefault(key, {"review_key": key, "review_title": row.get("review_title", ""),
                                       "review_text": row.get("review_text", ""), "sources": []})
        source = {"asin": row.get("asin") or row.get("review_target_asin") or "",
                  "rating": row.get("rating") or "", "review_date": row.get("review_date") or ""}
        if source not in item["sources"]:
            item["sources"].append(source)

    result = {"groups": {kind: [] for kind in KINDS}, "raw_count": len(reviews),
              "unique_count": len(corpus), "cited_count": 0, "pending": list(corpus.values()),
              "errors": [], "status": "pending", "reviewed_on": ""}
    if not annotations:
        return result
    if not isinstance(annotations, dict) or annotations.get("schema_version") != 2 or annotations.get("source_snapshot") != snapshot_hash(reviews):
        result["status"] = "stale"
        result["errors"].append("评论快照已变化或分析格式过期，旧判断暂不沿用。")
        return result

    items = annotations.get("insights", [])
    if not isinstance(items, list):
        result["errors"].append("观点列表格式无效，原文保留待核验。")
        return result
    cited, identifiers = set(), set()
    for insight in items:
        if not isinstance(insight, dict):
            result["errors"].append("存在格式无效的观点，已保留待核验。")
            continue
        identifier = str(insight.get("id") or "")
        kind = insight.get("kind")
        summary = str(insight.get("summary_zh") or "").strip()
        evidence = insight.get("evidence")
        valid = bool(identifier and identifier not in identifiers and kind in KINDS and summary
                     and isinstance(evidence, list) and evidence)
        if kind == "pain":
            valid = valid and all(isinstance(insight.get(field), str) and insight[field].strip()
                                  for field in ("need_zh", "scenario_zh", "gap_zh", "validation_zh"))
            valid = valid and insight.get("need_basis") in ("explicit_request", "blocked_workflow")
        if not valid:
            result["errors"].append(f"观点 {identifier or '未命名'} 缺少分类、场景需求或证据，已保留待核验。")
            continue
        citations, seen = [], set()
        for source in evidence:
            if not isinstance(source, dict):
                valid = False
                break
            quote = str(source.get("quote") or "").strip()
            field = source.get("field", "review_text")
            if not quote or field not in ("review_title", "review_text"):
                valid = False
                break
            matches = [item for item in corpus.values()
                       if item["review_title"] == source.get("review_title")
                       and quote in item.get(field, "")
                       and any(ref["asin"] == source.get("asin") for ref in item["sources"])]
            if len(matches) != 1:
                valid = False
                break
            item = matches[0]
            signature = (item["review_key"], field, quote)
            if signature not in seen:
                citations.append({**item, "quote": quote, "field": field,
                                  "translation_zh": str(source.get("translation_zh") or "")})
                seen.add(signature)
        if not valid:
            result["errors"].append(f"观点 {identifier} 的引用无法匹配原文，已保留待核验。")
            continue
        keys = {item["review_key"] for item in citations}
        result["groups"][kind].append({**insight, "summary_zh": summary, "evidence": citations, "mention_count": len(keys)})
        cited.update(keys)
        identifiers.add(identifier)

    result.update(status="reviewed" if cited else "pending", cited_count=len(cited),
                  pending=[item for key, item in corpus.items() if key not in cited],
                  reviewed_on=str(annotations.get("reviewed_on") or ""))
    return result
