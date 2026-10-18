/* Saved products work offline; execution requires the same-origin local service. */
(() => {
  "use strict";
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const fmt = value => Number(value || 0).toLocaleString("zh-CN", {maximumFractionDigits: 1});
  const storageKey = "amz-workbench-favorites-v1";
  const dismissalStorageKey = "amz-workbench-dismissals-v1";
  let data = {}, online = false, token = "", favorites = [], jobs = [], tab = "favorites", attached = false;
  let dismissals = [];
  let keywordJobs = null;
  let connectionError = "正在连接研究服务", syncing = null;
  const pending = new Set();
  try { favorites = JSON.parse(localStorage.getItem(storageKey) || "[]"); } catch (_) { favorites = []; }
  if (!Array.isArray(favorites)) favorites = [];
  try { dismissals = JSON.parse(localStorage.getItem(dismissalStorageKey) || "[]"); } catch (_) { dismissals = []; }
  if (!Array.isArray(dismissals)) dismissals = [];
  const activeDismissals = () => dismissals.filter(x => x && x.discarded && typeof x.key === "string");
  const discarded = (kind, key) => Boolean(key) && activeDismissals().some(x => x.kind === kind && x.key === key);
  const isDiscarded = row => Object.entries(row.decision_keys || {}).some(([kind, key]) => discarded(kind, key));
  const icons = () => window.lucide?.createIcons();
  const icon = name => `<i data-lucide="${name}" aria-hidden="true"></i>`;
  const known = asin => data.productCatalog?.[asin] || {asin, title: asin};
  const saved = asin => favorites.some(item => item.asin === asin && !item.localRemoved);
  const jobFor = asin => jobs.find(item => item.asin === asin);
  const resultUrl = asin => known(asin).url || favorites.find(x => x.asin === asin)?.url || jobs.find(x => x.asin === asin && x.status === "completed")?.url;
  const stateLabel = status => ({queued:"排队中",running:"研究中",completed:"研究已生成",failed:"失败",interrupted:"已中断",cancelled:"已取消"}[status] || status);
  const decision = row => row.shape_recommendation === "Needs classification" ? "pending" : row.shape_recommendation === "Reject category/form" ? "reject" : row.classification_status === "unresolved" ? "pending" : row.shape_recommendation === "Shape opportunity" ? "opportunity" : "watch";
  const decisionLabel = row => row.classification_status === "category_proxy" && decision(row) !== "reject" ? "类目代理待核验" : decision(row) === "pending" && row.classification_status === "needs_specification" ? "形态待确认" : ({reject:"已排除",pending:"用途待确认",opportunity:"机会待深研",watch:"待机会判断"}[decision(row)]);
  const scopeLabel = kind => ({market:"整组",family:"产品组",shape:"形态",asin:"产品"}[kind] || "记录");

  function discardButton(kind, key, label, restore = false) {
    if (!key) return "";
    const title = `${restore ? "恢复" : "舍弃"}${scopeLabel(kind)}：${label}`;
    return `<button type="button" class="wb-discard wb-icon-button" data-action="${restore ? "restore" : "discard"}" data-kind="${esc(kind)}" data-key="${esc(key)}" data-label="${esc(label)}" title="${esc(title)}" aria-label="${esc(title)}">${icon(restore ? "rotate-ccw" : "archive-x")}</button>`;
  }

  function actions(asin, family = "") {
    const current = jobFor(asin), url = resultUrl(asin);
    const busy = current && ["queued", "running"].includes(current.status);
    return `<div class="wb-actions">
      <button type="button" data-action="favorite" data-asin="${esc(asin)}" data-family="${esc(family)}" aria-pressed="${saved(asin)}" aria-label="${saved(asin) ? "取消收藏" : "收藏"} ${esc(asin)}" title="${saved(asin) ? "取消收藏" : "收藏，暂不研究"}">${icon("bookmark")}</button>
      ${url && !busy ? `<a href="${esc(url)}">${icon("file-search")}查看研究</a>` : `<button type="button" data-action="research" data-asin="${esc(asin)}" ${!online || busy || pending.has(asin) ? "disabled" : ""} title="${online ? "加入单品研究队列；将使用 Sorftime 额度" : esc(connectionError)}">${icon("scan-search")}${busy ? stateLabel(current.status) : "单品研究"}</button>`}
    </div>`;
  }

  function productCard(asin, family = "", metadata = "") {
    const item = {...known(asin), ...favorites.find(x => x.asin === asin)};
    const image = String(item.image || "").startsWith("https://") ? `<img src="${esc(item.image)}" alt="${esc(item.title || asin)}" loading="lazy" referrerpolicy="no-referrer">` : `<span class="wb-no-image">暂无图片</span>`;
    return `<article class="wb-product" data-product="${esc(asin)}">${image}<div>
      <a class="wb-product-title" href="https://www.amazon.com/dp/${asin}" target="_blank" rel="noopener noreferrer" title="${esc(item.title || asin)}">${esc(item.title || asin)}</a>
      <div class="wb-product-asin">${esc(asin)}</div><div class="wb-product-meta">${esc(metadata || family || item.family || "")}</div></div>${actions(asin, family)}</article>`;
  }

  function references(row) {
    return String(row.reference_asins || "").split(";").map(s => s.trim()).map(s => {
      const m = s.match(/^([A-Z0-9]{10})(?::([^/]*)(?:\/(.*))?)?$/i);
      return m ? {asin: m[1].toUpperCase(), sales: m[2], reviews: m[3]} : null;
    }).filter(Boolean);
  }

  const L = () => window.AmzLabels;
  const shapeName = row => String(row.product_form_label || row.product_form || "未识别形态").replace(/\s*·\s*类目代理待核验$/, "");
  const scopeTag = row => ({
    category_proxy: "整类目统计",
    product_type_confirmed: "商品类型一致，规格待核",
    title_rule: "按标题初分",
    needs_specification: "规格待确认",
    unresolved: "分组未确定",
  }[row.classification_status] || "");
  const money = value => value === "" || value == null || !Number(value) ? "-" : "$" + Number(value).toFixed(2);
  const sources = row => row.family_sources?.length ? row.family_sources : row.source_categories || [];

  function context(row) {
    const name = shapeName(row).toLowerCase();
    const list = sources(row);
    const market = row.market_label || row.source_category_name || "";
    const parts = [];
    if (market && market.toLowerCase() !== name) parts.push(market);
    if (list.length > 1) parts.push(`${list.length} 个类目：${list.slice(0, 3).map(s => s.name).join(" / ")}${list.length > 3 ? " 等" : ""}`);
    else if (!parts.length && row.category_path) parts.push(L().parentPath(row.category_path, 2));
    return parts.join(" · ");
  }

  function reasonChips(row) {
    const items = L().flags(row.validation_flags);
    if (!items.length) return '<span class="wb-reason ok">没有发现硬伤</span>';
    return items.map(item => `<span class="wb-reason ${item.tone}">${esc(item.text)}</span>`).join("");
  }

  function reasonLine(row, state) {
    if (state === "opportunity" || state === "pending") return "";
    const text = L().mainReason(row.validation_flags, state);
    return text ? `<em class="wb-row-reason ${state}">${state === "reject" ? "排除：" : ""}${esc(text)}</em>` : "";
  }

  function shape(row, key, forceOpen = false) {
    const refs = references(row);
    const state = decision(row);
    const scored = state !== "pending" && row.shape_score != null && row.shape_score !== "";
    const tag = scopeTag(row);
    const list = sources(row);
    const nextAction = String(row.next_action || "").trim();
    return `<details class="wb-shape wb-row ${state}" data-detail="${esc(key)}" ${forceOpen ? "open" : ""}><summary>
      <span class="wb-row-name"><b>${esc(shapeName(row))}</b>${tag ? `<span class="wb-tag">${esc(tag)}</span>` : ""}<small>${esc(context(row))}${reasonLine(row, state)}</small></span>
      <span class="wb-row-num score" data-label="分数">${scored ? fmt(row.shape_score) : "—"}</span>
      <span class="wb-row-num" data-label="平均月销">${scored ? fmt(row.form_avg_sales) : "—"}</span>
      <span class="wb-row-num" data-label="评论中位">${scored ? fmt(row.form_median_reviews) : "—"}</span>
      <span class="wb-row-num" data-label="价格中位">${scored ? money(row.form_price_median) : "—"}</span>
      <span class="wb-row-num" data-label="样本">${fmt(row.form_count || row.family_sample_count)}</span>
      ${discardButton("shape", row.decision_keys?.shape, shapeName(row))}
      </summary><div class="wb-shape-content">
      <div class="wb-reasons">${reasonChips(row)}</div>
      ${row.opportunity_thesis ? `<p>${esc(L().prose(row.opportunity_thesis))}</p>` : ""}
      ${nextAction ? `<p class="wb-next"><b>下一步</b>${esc(nextAction)}</p>` : ""}
      ${scored ? `<div class="wb-metrics"><span>近 18 个月新品达标 <b>${fmt(row.form_new_entrant_success_count)} / ${fmt(row.form_new_entrant_count)}</b></span><span>低评高销 <b>${fmt(row.form_low_review_high_sales_count)}</b></span><span>倒推到仓预算 <b>${row.form_landed_cost_ceiling_median !== "" && row.form_landed_cost_ceiling_median != null ? money(row.form_landed_cost_ceiling_median) : "待核实 FBA 费用"}</b></span>${row.form_top_materials ? `<span>主要材质 <b>${esc(row.form_top_materials)}</b></span>` : ""}</div>` : ""}
      ${list.length ? `<div class="wb-path">来源类目：${list.map(s => esc(s.name)).join(" / ")}${row.snapshot_id ? ` · 扫描批次 ${esc(row.snapshot_id)}` : ""}</div>` : row.category_path ? `<div class="wb-path" title="${esc(row.category_path)}">来源类目：${esc(L().shortPath(row.category_path, 3))}</div>` : ""}
      <div class="wb-products">${refs.map(ref => productCard(ref.asin, row.product_family_label || row.product_form, `月销 ${fmt(ref.sales)} · 评论 ${fmt(ref.reviews)}`)).join("") || '<span class="wb-empty">暂无通过基础筛选的参考产品</span>'}</div>
      </div></details>`;
  }

  const TIERS = [
    {id: "opportunity", title: "可以进入单品研究", note: "需求、竞争、风险和价格带都通过了。", empty: "这一批没有通过验证的形态。先看下面「值得人工核对」排在前面的几项。"},
    {id: "watch", title: "值得人工核对", note: "有需求信号，但还差一步证据。按分数从高到低排，点开看原因和下一步。"},
    {id: "pending", title: "用途待确认", note: "样本的用途或结构不一致，先拆清形态再评分。"},
    {id: "reject", title: "已排除", note: ""},
  ];

  function topReasons(rows) {
    const counts = new Map();
    rows.forEach(row => L().flags(row.validation_flags).filter(item => item.tone === "hard").slice(0, 1).forEach(item => counts.set(item.text, (counts.get(item.text) || 0) + 1)));
    return [...counts].sort((a, b) => b[1] - a[1]).slice(0, 4).map(([text, n]) => `${esc(text)} ${n}`).join(" · ");
  }

  const header = '<div class="wb-row-head" aria-hidden="true"><span>形态</span><span>分数</span><span>平均月销</span><span>评论中位</span><span>价格中位</span><span>样本</span></div>';

  function renderShapes(input) {
    data = input;
    const target = document.getElementById("shapeValidationList");
    if (!target) return;
    const opened = new Map([...target.querySelectorAll("details[data-detail]")].map(x => [x.dataset.detail, x.open]));
    const query = document.getElementById("shapeSearch").value.trim().toLowerCase();
    const selected = document.getElementById("shapeRecommendation").value;
    const rows = (data.shapeValidation || []).filter(row => {
      if (isDiscarded(row)) return false;
      if (selected && row.shape_recommendation !== selected) return false;
      if (!query) return true;
      const titleText = references(row).map(ref => known(ref.asin).title).join(" ");
      const sourceText = sources(row).map(s => `${s.name} ${s.path} ${s.id}`).join(" ");
      return [row.market_label,row.product_form,row.product_form_label,row.product_family_label,row.source_category_name,row.category_path,row.reference_asins,titleText,sourceText].join(" ").toLowerCase().includes(query);
    });
    const byTier = new Map(TIERS.map(t => [t.id, []]));
    rows.forEach(row => byTier.get(decision(row)).push(row));
    byTier.forEach((list, id) => list.sort((a, b) => id === "pending"
      ? Number(b.form_count || b.family_sample_count || 0) - Number(a.form_count || a.family_sample_count || 0)
      : Number(b.shape_score || 0) - Number(a.shape_score || 0)));
    const filtered = Boolean(query || selected);
    target.innerHTML = TIERS.map(tier => {
      const list = byTier.get(tier.id);
      if (!list.length && (filtered || tier.id !== "opportunity")) return "";
      const open = filtered || tier.id === "opportunity" || tier.id === "watch";
      const note = tier.id === "reject" && list.length ? `主要原因：${topReasons(list)}` : tier.note;
      const body = list.length
        ? header + list.map((row, i) => shape(row, `shape:${row.decision_keys?.shape || tier.id + i}`, false)).join("")
        : `<div class="wb-empty">${esc(tier.empty || "暂无")}</div>`;
      return `<details class="wb-tier ${tier.id}" data-detail="tier:${tier.id}" ${open ? "open" : ""}>
        <summary><span class="wb-tier-title">${esc(tier.title)}</span><span class="wb-tier-count">${list.length}</span><span class="wb-tier-note">${note}</span></summary>
        ${body}</details>`;
    }).join("") || '<div class="wb-empty">没有匹配的形态</div>';
    if (!filtered) target.querySelectorAll("details[data-detail]").forEach(node => { if (opened.has(node.dataset.detail)) node.open = opened.get(node.dataset.detail); });
    const visible = state => (data.shapeValidation || []).filter(r => !isDiscarded(r) && decision(r) === state).length;
    const opportunityCount = document.getElementById("visibleShapeOpportunityCount");
    if (opportunityCount) opportunityCount.textContent = fmt(visible("opportunity"));
    const watchCount = document.getElementById("visibleShapeWatchCount");
    if (watchCount) watchCount.textContent = fmt(visible("watch"));
    window.dispatchEvent(new Event("amz-dismissals-changed"));
    icons();
  }

  function renderConnection() {
    const target = document.getElementById("shapeValidationList");
    let status = document.getElementById("shapeWorkbenchConnection");
    if (target && !status) {
      status = document.createElement("div");
      status.id = "shapeWorkbenchConnection";
      target.before(status);
      const notice = document.createElement("div");
      notice.id = "shapeWorkbenchNotice";
      notice.setAttribute("role", "status");
      notice.setAttribute("aria-live", "polite");
      target.before(notice);
    }
    const label = online ? "研究服务已连接" : connectionError;
    const markup = `<span role="status">${icon(online ? "circle-check" : "circle-alert")}${esc(label)}</span>${online ? "" : `<button type="button" class="wb-icon-button" data-action="reconnect" title="重新连接研究服务" aria-label="重新连接研究服务">${icon("refresh-cw")}</button>`}<button type="button" class="wb-icon-button" data-action="show-dismissals">${icon("archive-x")}已舍弃 ${activeDismissals().length}</button>`;
    for (const node of [status, document.getElementById("workbenchConnection")]) {
      if (!node) continue;
      node.classList.add("wb-connection");
      node.classList.toggle("offline", !online);
      node.innerHTML = markup;
    }
  }

  function renderPanel() {
    window.AmzKeywordSearch?.updateState({online, token, jobs: keywordJobs, error: connectionError});
    renderConnection();
    const body = document.getElementById("workbenchBody");
    if (!body) return;
    const visibleFavorites = favorites.filter(x => !x.localRemoved);
    document.getElementById("favoriteCount").textContent = visibleFavorites.length;
    document.getElementById("jobCount").textContent = jobs.filter(x => ["queued","running"].includes(x.status)).length;
    const count = document.getElementById("dismissalCount");
    if (count) count.textContent = activeDismissals().length;
    document.querySelectorAll("[data-workbench-tab]").forEach(button => button.setAttribute("aria-selected", String(button.dataset.workbenchTab === tab)));
    if (tab === "favorites") {
      body.innerHTML = visibleFavorites.length ? `<div class="wb-products">${visibleFavorites.map(x => productCard(x.asin, x.family)).join("")}</div>` : '<div class="wb-empty">暂无收藏</div>';
    } else if (tab === "dismissals") {
      body.innerHTML = activeDismissals().map(item => `<div class="wb-job"><div class="wb-job-title"><strong>${esc(item.label || item.key)}</strong><div>${scopeLabel(item.kind)} · ${esc(new Date(item.updated).toLocaleString("zh-CN"))}${item.localDirty ? " · 仅存本浏览器，待同步" : ""}</div></div><div class="wb-actions">${discardButton(item.kind, item.key, item.label || item.key, true)}</div></div>`).join("") || '<div class="wb-empty">暂无已舍弃记录</div>';
    } else {
      body.innerHTML = jobs.map(job => `<div class="wb-job"><div class="wb-job-title"><strong>${esc(job.title || known(job.asin).title || job.asin)}</strong><div>${esc(job.asin)} · ${stateLabel(job.status)} · ${esc(new Date(job.created).toLocaleString("zh-CN"))}</div><div>${esc(job.message)}</div></div><div class="wb-actions">
        ${job.status === "queued" ? `<button type="button" data-action="cancel" data-id="${job.id}" ${!online ? "disabled" : ""}>${icon("x")}取消排队</button>` : ""}
        ${["failed","interrupted","cancelled"].includes(job.status) ? `<button type="button" data-action="research" data-asin="${esc(job.asin)}" ${!online || pending.has(job.asin) ? "disabled" : ""}>${icon("rotate-cw")}重试</button>` : ""}
        ${job.status === "completed" && job.url ? `<a href="${esc(job.url)}">${icon("file-search")}查看研究</a>` : ""}</div></div>`).join("") || '<div class="wb-empty">暂无研究任务</div>';
    }
    icons();
  }

  function notice(message) { for (const id of ["workbenchNotice", "shapeWorkbenchNotice"]) { const node = document.getElementById(id); if (node) node.textContent = message; } }
  function persist() {
    try { localStorage.setItem(storageKey, JSON.stringify(favorites)); }
    catch (_) { notice("浏览器未允许保存收藏；请连接本地服务后收藏。"); }
  }
  function persistDismissals(items = dismissals, required = false) {
    try { localStorage.setItem(dismissalStorageKey, JSON.stringify(items)); }
    catch (_) {
      if (required) throw new Error("浏览器禁止保存，未舍弃；请连接本机服务后重试");
    }
  }
  async function request(path, payload) {
    const response = await fetch(path, {method: "POST", headers: {"Content-Type":"application/json", "X-Workbench-Token": token}, body: JSON.stringify(payload), signal: AbortSignal.timeout(10000)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "提交失败");
    return result;
  }
  async function readState() {
    const response = await fetch("/api/workbench", {cache:"no-store", signal: AbortSignal.timeout(5000)});
    if (!response.ok) throw new Error(response.status === 403 ? "研究服务拒绝连接（403），请重连" : response.status === 404 ? "当前站点没有研究服务，请使用本机入口" : `研究服务连接失败（${response.status}）`);
    const state = await response.json();
    if (!state.token || !Array.isArray(state.jobs) || !Array.isArray(state.favorites)) throw new Error("当前站点未提供研究接口");
    if (!Array.isArray(state.dismissals)) throw new Error("研究服务需更新后才能同步舍弃记录");
    return state;
  }
  async function syncState() {
    const before = JSON.stringify([online, connectionError, favorites, jobs, dismissals, keywordJobs]);
    if (!/^https?:$/.test(location.protocol)) {
      online = false; connectionError = "本地文件模式，研究需打开本机服务";
      renderPanel(); renderShapes(data); return;
    }
    try {
      let state = await readState();
      token = state.token;
      // Only offline edits from this browser are uploaded; a passive poll never resurrects deletions.
      const dirty = favorites.filter(item => item.localDirty);
      for (const item of dirty) await request("/api/favorites", {asin:item.asin,saved:!item.localRemoved,title:item.title,family:item.family});
      const dirtyDismissals = dismissals.filter(item => item.localDirty);
      for (const item of dirtyDismissals) await request("/api/dismissals", {kind:item.kind,key:item.key,label:item.label,discarded:Boolean(item.discarded)});
      if (dirty.length || dirtyDismissals.length) {
        favorites = favorites.map(x => ({...x,localDirty:false})); persist();
        dismissals = dismissals.map(x => ({...x,localDirty:false})); persistDismissals();
        state = await readState(); token = state.token;
      }
      favorites = state.favorites; jobs = state.jobs; online = true; connectionError = ""; persist();
      keywordJobs = state.keyword_jobs;
      dismissals = state.dismissals; persistDismissals();
    } catch (error) {
      online = false;
      connectionError = error.name === "TimeoutError" ? "研究服务连接超时，可重连" : error instanceof TypeError ? "研究服务未连接，可重连；收藏仍可使用" : error.message;
    }
    if (before !== JSON.stringify([online, connectionError, favorites, jobs, dismissals, keywordJobs])) { renderPanel(); renderShapes(data); }
  }
  function sync() {
    if (!syncing) syncing = syncState().finally(() => { syncing = null; });
    return syncing;
  }

  async function handleClick(event) {
    const tabButton = event.target.closest("[data-workbench-tab]");
    if (tabButton) { tab = tabButton.dataset.workbenchTab; renderPanel(); return; }
    const button = event.target.closest("button[data-action]");
    if (!button || button.disabled) return;
    const asin = button.dataset.asin, action = button.dataset.action;
    const operation = asin || `${action}:${button.dataset.kind || ""}:${button.dataset.key || ""}`;
    if (pending.has(operation)) return;
    if (["discard", "restore"].includes(action)) { event.preventDefault(); event.stopPropagation(); }
    button.disabled = true;
    pending.add(operation);
    try {
      if (action === "show-dismissals") {
        tab = "dismissals";
        location.hash = "research-workbench";
      } else if (["discard", "restore"].includes(action)) {
        // Finish any passive poll before writing so its older response cannot undo the click.
        if (syncing) await syncing;
        const item = {kind:button.dataset.kind,key:button.dataset.key,label:button.dataset.label,
                      discarded:action === "discard",updated:new Date().toISOString()};
        if (online) {
          await request("/api/dismissals", item);
          dismissals = dismissals.filter(x => x.kind !== item.kind || x.key !== item.key);
          if (item.discarded) dismissals.push(item);
          persistDismissals();
        } else {
          const updated = dismissals.filter(x => x.kind !== item.kind || x.key !== item.key);
          updated.push({...item,localDirty:true});
          persistDismissals(updated, true);
          dismissals = updated;
        }
        notice(`${item.label} 已${item.discarded ? "舍弃，可在“已舍弃”中恢复" : "恢复；若上级产品组仍已舍弃，需一并恢复上级"}${online ? "" : "；目前仅保存在本浏览器"}。`);
      } else if (action === "reconnect") {
        await sync();
      } else if (action === "favorite") {
        const wasSaved = saved(asin), item = {...known(asin),family:button.dataset.family || "",asin};
        if (online) {
          await request("/api/favorites", {...item, saved:!wasSaved});
          await sync();
        } else {
          favorites = favorites.filter(x => x.asin !== asin);
          // An offline removal is a tombstone until the next successful sync.
          favorites.push({...item,localDirty:true,localRemoved:wasSaved});
          persist();
        }
        notice(wasSaved ? `${asin} 已取消收藏` : `${asin} 已收藏，未发起研究`);
      } else if (action === "research") {
        if (!online) throw new Error("本地研究服务未连接，尚未提交任务");
        const result = await request("/api/research", {asin,title:known(asin).title});
        notice(result.status === "completed" ? `${asin} 已有研究，未重复调用` : `${asin} 已加入研究队列`);
        tab = "jobs";
        await sync();
      } else if (action === "cancel") {
        await request("/api/research/cancel", {id:Number(button.dataset.id)});
        notice("已取消排队，未启动的任务不会调用数据接口");
        await sync();
      }
    } catch (error) {
      notice(`未确认保存/提交成功：${error.message}。可刷新核对，重复研究请求会去重。`);
    } finally {
      pending.delete(operation);
      renderShapes(data); renderPanel();
      if (button.isConnected) button.disabled = false;
    }
  }

  function attach(input) {
    data = input;
    if (attached) return;
    attached = true;
    document.addEventListener("click", handleClick);
    const reconnect = () => { if (!document.hidden && !pending.size) sync(); };
    window.addEventListener("focus", reconnect);
    window.addEventListener("online", reconnect);
    document.addEventListener("visibilitychange", reconnect);
    renderPanel();
    sync();
    setInterval(reconnect, 7000);
  }
  window.AmzWorkbench = {renderShapes, attach, isDiscarded, discardButton, sync};
})();
