"""Group products by use before distinguishing their physical forms."""

import re

from product_taxonomy import barware_title_identity, first_match, load_taxonomy, normalize
from title_product_names import find_title_product, normalize_product_name, title_subject


# Ordered, bounded title rules: category/product-type labels alone are not proof.
FAMILIES = [
    ("tofu_press", "豆腐/奶酪压制脱水工具", r"\b(?:tofu (?:press|presser|mould|mold)|paneer (?:press|maker))\b"),
    ("sink_caddy", "水槽清洁用品收纳", r"\bsink\s+(?:caddy|sponge holder|organizer)\b"),
    ("card_storage", "卡片/照片收纳盒", r"\b(?:index|recipe|flash|photo)\s+card\s+(?:storage\s+|holder\s+)?box\b|\b(?:flashcard|photo)\s+(?:storage\s+)?box\b"),
    ("waste_bin", "垃圾收集容器", r"\b(?:trash|waste|garbage)\s+(?:can|bin|basket)\b"),
    ("dough_scraper", "面团刮板", r"\b(?:dough|bench|bowl|sourdough)\s+(?:scraper|cutter)s?\b"),
    ("cake_guide", "蛋糕裱花定位工具", r"\b(?:cake arch guide|swag marker|ruffle (?:guide|marker)|cake piping guide)\b"),
    ("cake_scraper", "蛋糕抹面刮板", r"\b(?:cake|icing|frosting|cream)\s+(?:scraper|smoother|comb)s?\b|\bcake decorating (?:scraper|comb)\b"),
    ("cake_leveler", "蛋糕分层切割器", r"\bcake\s+(?:leveler|levcake|cutter slicer)\b"),
    ("cake_dummy", "蛋糕展示模型", r"\b(?:cake dumm(?:y|ies)|dummy cake|fake cake)\b"),
    ("cake_brush", "烘焙装饰画笔", r"\b(?:decorating|cake painting|food safe paint|cake candy decorating)\s+brush(?:es)?\b"),
    ("cookie_scribe", "糖霜排气描线针", r"\b(?:scribe|scriber|sugar stir)\s+(?:needle|tool)s?\b"),
    ("cake_collar", "蛋糕围边膜", r"\b(?:cake collar|acetate (?:roll|sheet)|cake strips)\b"),
    ("rolling_pin", "擀面杖", r"\brolling pins?\b|\bdough roller\b"),
    ("cookie_cutter", "饼干切模", r"\bcookie cutters?\b"),
    ("cookie_stamp", "饼干印模", r"\bcookie stamps?\b"),
    ("drain_linkage", "浴缸排水连杆组件", r"\b(?:drain linkage|extension linkage|trip lever)\b"),
    ("drain_stopper", "浴缸/水槽止水塞", r"\b(?:drain stopper|drain plug|tub stopper|sink stopper|bathtub stopper)\b"),
    ("drain_strainer", "排水口过滤器", r"\b(?:drain strainer|drain hair catcher|sink strainer|tub strainer)\b"),
    ("tool_mat", "磁吸工具垫", r"\bmagnetic (?:tool|parts) mats?\b"),
    ("tool_holster", "工具腰挂", r"\btool holster\b|\bdrill holster\b"),
    ("hose_splitter", "园艺水管分水器", r"\b(?:hose|water) splitter\b"),
    ("toilet_brush", "马桶清洁刷", r"\btoilet (?:bowl )?brush\b"),
    ("landing_net", "抄鱼网", r"\blanding (?:fishing )?net\b"),
    ("cast_net", "撒网", r"\bcast(?:ing)? (?:fishing )?net\b"),
    ("duster", "除尘掸", r"\bduster(?:s| refill)?\b"),
]

GENERIC = {"unknown", "kitchen", "home", "tools", "kitchen tools", "plumbing fixture",
           "plug stopper", "food spatula", "hardware", "misc other", "art supplies",
           "sporting goods", "outdoor living", "sculpting modeling tools", "product"}


def unresolved_identity(form):
    return {"product_family": "unresolved:" + (normalize_product_name(form) or "unknown"),
            "product_family_label": "用途待确认", "product_form": form or "unknown",
            "product_form_label": form or "unknown", "classification_status": "unresolved"}


def product_identity(title, category="", form="unknown"):
    text = normalize_product_name(title)
    matched = find_title_product(title, FAMILIES)
    if matched.get("unresolved_accessory"):
        return unresolved_identity(form)
    barware = barware_title_identity(text)
    subject = normalize_product_name(title_subject(title))
    bar_subject = barware_title_identity(subject)
    bar_position = re.search(r"\b(?:jigger|cocktail|boston|cobbler|bartender|bartending|mixology|drink measure|shot measuring cup|liquor measuring cup|spirit measure)\b", subject)
    other_position = subject.find(matched.get("matched_name", "")) if matched else -1
    if barware and bar_subject and (not matched or matched["key"] == "cocktail_shaker"
                                   or (bar_position and bar_position.start() < other_position)):
        return barware
    if matched.get("form"):
        key, label, shape = matched["key"], matched["label"], matched["form"]
        # Alternative product names share the existing family identity.
        if key == "biscuit_cutter":
            key = "cookie_cutter"
        if key == "tea_drawer":
            key = "tea_organizer"
        if key == "drain_hair_catcher":
            key = "drain_strainer"
        variant = next((v for v in matched.get("variants", []) if re.search(v["pattern"], text)), None)
        if variant:
            shape += " - " + variant["key"]
            if key == "tea_organizer":
                shape = {"drawer": "tea bag drawer", "box": "tea storage box"}[variant["key"]]
        pending = matched.get("specification_required", False) and not variant
        return {"product_family": key, "product_family_label": label,
                "product_form": shape,
                "product_form_label": label + (" · " + variant["label"] if variant else " · 规格待确认" if pending else ""),
                "classification_status": "needs_specification" if pending else "title_rule"}
    for key, label, pattern in FAMILIES:
        if key != matched.get("key"):
            continue
        variant = ""
        if key in {"cake_scraper", "dough_scraper", "rolling_pin"}:
            for expression, name in [(r"\b(?:adjustable|thickness rings?)\b", "可调厚度" if key == "rolling_pin" else "可调结构"),
                                     (r"\b(?:stainless|metal|steel)\b", "金属"),
                                     (r"\b(?:wood|wooden|bamboo)\b", "木/竹质"),
                                     (r"\bmarble\b", "大理石"),
                                     (r"\bacrylic\b", "亚克力"),
                                     (r"\b(?:plastic|flexible|pe)\b", "塑料/柔性")]:
                if re.search(expression, text):
                    variant = name
                    break
        elif key == "drain_stopper":
            if "silicone" in text or "slicone" in text or "suction" in text:
                variant = "软质覆盖式"
            elif "pop up" in text or "push" in text:
                variant = "按压弹跳式"
            elif "lift" in text or "twist" in text:
                variant = "提拉旋转式"
        elif key == "toilet_brush":
            variant = "一次性替换头" if "disposable" in text or "refill" in text else "可重复使用"
        elif key == "hose_splitter":
            match = re.search(r"\b([234]) way\b", text)
            variant = f"{match[1]} 路" if match else ""
        elif key == "tofu_press":
            if re.search(r"\b(?:mould|mold)\b", text):
                variant = "沥水模具"
            elif "stainless" in text:
                variant = "不锈钢压制器"
            else:
                variant = "其他压制器"
        canonical = {"tool_mat": "magnetic tool mat", "tool_holster": "tool holster",
                     "sink_caddy": "sink caddy",
                     "card_storage": "card storage box", "waste_bin": "trash can",
                     "landing_net": "landing fishing net", "cast_net": "cast fishing net"}
        variant_key = {"可调厚度":"adjustable", "可调结构":"adjustable", "金属":"metal", "木/竹质":"wood bamboo",
                       "大理石":"marble", "亚克力":"acrylic", "塑料/柔性":"flexible plastic",
                       "软质覆盖式":"soft cover", "按压弹跳式":"push pop up", "提拉旋转式":"lift turn",
                       "一次性替换头":"disposable refill", "可重复使用":"reusable",
                       "沥水模具":"mould", "不锈钢压制器":"stainless steel", "其他压制器":"other mechanism",
                       "2 路":"2 way", "3 路":"3 way", "4 路":"4 way"}.get(variant, "")
        shape = canonical.get(key) or key.replace("_", " ") + (f" - {variant_key}" if variant_key else "")
        if key == "duster":
            shape = "duster refill" if "refill" in text else "duster"
        if key == "tofu_press":
            shape = {"沥水模具": "tofu press mould", "不锈钢压制器": "stainless steel tofu press",
                     "其他压制器": "tofu press"}[variant]
        return {"product_family": key, "product_family_label": label,
                "product_form": shape, "product_form_label": f"{label} · {variant or '规格待细分'}",
                "classification_status": "title_rule"}
    # An explicit title alias can rescue a wrong provider type, but category alone cannot.
    title_override = first_match(normalize(title_subject(title)), load_taxonomy().get("overrides", []))
    form = title_override or form
    base = normalize_product_name(form)
    title_supports_form = bool(base and (re.search(rf"\b{re.escape(base)}\b", subject) or title_override))
    if base in GENERIC or not title_supports_form:
        return unresolved_identity(form)
    return {"product_family": "legacy:" + base, "product_family_label": form,
            "product_form": form, "product_form_label": form, "classification_status": "legacy_form"}
