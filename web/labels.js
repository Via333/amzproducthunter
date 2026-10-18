/* Chinese display labels for pipeline codes. Display only: never feeds scoring or filtering. */
(() => {
  "use strict";

  // Pipeline flag -> [Chinese label, tone]. tone: "hard" blocks the shape, "soft" is a caveat to check.
  const FLAGS = {
    "category proxy requires classification": ["按整个类目统计，需先拆成具体形态", "soft"],
    "thin unit economics": ["利润按默认成本粗估，待报价", "soft"],
    "insufficient brand evidence": ["品牌数据不足", "soft"],
    "weak form demand": ["形态需求偏弱", "hard"],
    "price band outside target": ["价格带不在 $12–$80", "hard"],
    "new entrants not selling": ["近 18 个月的新品没卖起来", "hard"],
    "no new entrants": ["近 18 个月没有新品进入", "hard"],
    "thin form sample": ["形态样本太少", "soft"],
    "excluded products": ["大件 / 易碎 / 合规等排除品占比高", "hard"],
    "all products excluded": ["样本全部是排除品", "hard"],
    "mixed with excluded products": ["混有排除品", "soft"],
    "generic form classification": ["形态分得太宽泛", "soft"],
    "form review wall": ["形态评论墙高", "hard"],
    "top10 review wall": ["Top10 评论墙高", "hard"],
    "category review wall": ["类目评论墙高", "hard"],
    "sales concentration": ["销量集中在头部几款", "hard"],
    "form sales concentrated": ["形态内销量集中", "hard"],
    "brand concentration": ["头部品牌集中", "hard"],
    "no listing-age data": ["缺上架时间数据", "soft"],
    "brand-dependent accessory": ["依附其他品牌的配件（IP / 跟卖风险）", "hard"],
    "seasonal holiday products": ["节日季节性商品", "hard"],
    "partly seasonal holiday products": ["部分是节日季节性商品", "soft"],
    "excluded title term": ["标题含排除词", "hard"],
    "brand moat": ["品牌护城河", "hard"],
    "oversize risk": ["大件", "hard"],
    "fragile risk": ["易碎", "hard"],
    "compliance risk": ["强合规", "hard"],
    "supplier quote required": ["需要供应商报价", "soft"],
    "estimated profit is not a rejection gate": ["", "skip"],
    "review count above 600": ["评论数超过 600", "hard"],
  };

  const VERDICTS = {
    "Shape opportunity": ["可以研究", "green"],
    "Watch shape": ["待核对", "orange"],
    "Needs classification": ["用途待确认", "gray"],
    "Needs category Top100": ["缺类目 Top100", "gray"],
    "Reject category/form": ["已排除", "red"],
    "Go": ["可以研究", "green"],
    "Supplier validation": ["进入供应商验证", "green"],
    "Watch or collect more data": ["待补数据", "orange"],
    "Watch": ["待核对", "orange"],
    "Reject": ["已排除", "red"],
  };

  const ARCHIVE_STATUS = {
    active: ["历史验证有效", "green"],
    active_in_latest_run: ["本轮验证有效", "green"],
    stale_needs_rescan: ["待复扫", "orange"],
    failed_on_rescan: ["复扫未通过", "gray"],
    invalidated_by_rule: ["新规则判定失效", "gray"],
    ip_review_required: ["知识产权风险拦截", "red"],
    suppressed_by_user: ["人工舍弃", "gray"],
    rejected_by_research: ["研究拦截", "gray"],
    seasonal_confirmed: ["季节风险待复核", "orange"],
    not_in_latest_run: ["最近一轮未出现", "gray"],
  };

  const RESEARCH_STATUS = {
    needs_supplier_validation: "待供应商验证",
    supplier_validation: "供应商验证中",
    researched: "已研究",
  };

  const ROTATION = {
    never_scanned: ["首次扫描", "green"],
    oldest_rescan: ["到期复扫", "gray"],
    manual_seed: ["手动指定", "gray"],
  };

  const normalize = text => String(text || "").trim().toLowerCase().replace(/top\d+/g, "top10");

  // "thin unit economics: budget scenario only" -> base key "thin unit economics", detail kept when useful.
  function flag(raw) {
    const text = String(raw || "").trim();
    if (!text) return null;
    const key = normalize(text);
    const exact = FLAGS[key];
    if (exact) return exact[1] === "skip" ? null : {text: exact[0], tone: exact[1]};
    const base = Object.keys(FLAGS).sort((a, b) => b.length - a.length).find(name => key.startsWith(name));
    if (!base) return {text, tone: "soft"};
    const [label, tone] = FLAGS[base];
    if (tone === "skip") return null;
    const rest = text.slice(base.length).replace(/^[:\s]+/, "");
    if (base === "all products excluded" || base === "excluded title term") {
      const inner = flag(rest.replace(/:\s*[\d.]+$/, ""));
      const detail = inner && inner.text !== rest ? inner.text : rest.replace(/:\s*[\d.]+$/, "");
      return {text: detail ? `${label}（${detail}）` : label, tone};
    }
    if (base === "brand moat" && rest) return {text: `${label}：${rest.replace(/:\s*[\d.]+$/, "")}`, tone};
    return {text: label, tone};
  }

  function flags(value) {
    const seen = new Set();
    return String(value || "").split(/;\s*/).map(flag).filter(item => item && item.text && !seen.has(item.text) && seen.add(item.text));
  }

  function flagsText(value, fallback = "") {
    const items = flags(value);
    return items.length ? items.map(item => item.text).join("；") : fallback;
  }

  const pair = (table, value, fallback) => table[value] || [fallback ?? value ?? "-", "gray"];
  const verdict = value => pair(VERDICTS, String(value || "").trim(), value || "-");
  const archiveStatus = value => pair(ARCHIVE_STATUS, value, "历史机会");
  const rotation = value => pair(ROTATION, value, "历史基线");
  const researchStatus = value => RESEARCH_STATUS[value] || value || "";

  function archiveNote(value) {
    const text = String(value || "").trim();
    if (!text) return "";
    if (/rotation status repaired/i.test(text)) return "旧记录已修复，需要重新扫描确认";
    const invalidated = text.match(/^invalidated_by_rule\s*\(([^)]*)\):\s*(.*)$/i);
    if (invalidated) return `新规则判定失效：${flagsText(invalidated[2], invalidated[2])}`;
    return flagsText(text, text);
  }

  // Last N levels of an Amazon breadcrumb; the full path goes in a tooltip.
  function shortPath(path, levels = 2) {
    const parts = String(path || "").split(/\s*>\s*/).filter(Boolean);
    return parts.slice(-levels).join(" › ");
  }

  function parentPath(path, levels = 3) {
    const parts = String(path || "").split(/\s*>\s*/).filter(Boolean);
    return parts.slice(Math.max(0, parts.length - 1 - levels), -1).join(" › ");
  }

  // Pipeline prose carries raw "ASIN:sales/reviews" lists and float noise like "2494.0".
  function prose(value) {
    return String(value || "")
      .replace(/\b([A-Z0-9]{10}):(\d+)\/(\d+)/g, "$1（月销 $2 · 评论 $3）")
      .replace(/(\d)\.0(?!\d)/g, "$1")
      .replace(/\s*;\s*(?=B0)/g, "、");
  }

  // The one reason worth showing on a collapsed row.
  function mainReason(value, decision) {
    const items = flags(value).filter(item => !/^按整个类目统计|^利润按默认成本/.test(item.text));
    const pick = decision === "reject" ? items.find(item => item.tone === "hard") || items[0] : items[0];
    return pick ? pick.text : "";
  }

  window.AmzLabels = {prose, mainReason, flag, flags, flagsText, verdict, archiveStatus, archiveNote, rotation, researchStatus, shortPath, parentPath};
})();
