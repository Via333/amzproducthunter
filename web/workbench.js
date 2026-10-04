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

  function shape(row, key, forceOpen = false) {
    const refs = references(row);
    return `<details class="wb-shape" data-detail="${esc(key)}" ${forceOpen ? "open" : ""}><summary>
      ${esc(row.product_form_label || row.product_form)} <span class="wb-status ${decision(row)}">${decisionLabel(row)}</span>
      <span class="wb-counts">${decision(row) === "pending" || row.shape_score == null ? "暂不评分 · 分组待核验" : `${fmt(row.shape_score)} 分 · ${fmt(row.form_count)} 个有效样本 · 价格中位 $${Number(row.form_price_median || 0).toFixed(2)}`}</span>
      ${discardButton("shape", row.decision_keys?.shape, row.product_form_label || row.product_form)}
      </summary><div class="wb-shape-content">
      ${decision(row) !== "pending" ? `<div class="wb-metrics"><span>平均月销 <b>${fmt(row.form_avg_sales)}</b></span><span>评论中位 <b>${fmt(row.form_median_reviews)}</b></span><span>低评高销 <b>${fmt(row.form_low_review_high_sales_count)}</b></span><span>新进入者达标 <b>${fmt(row.form_new_entrant_success_count)} / ${fmt(row.form_new_entrant_count)}</b></span></div>` : ""}
      ${row.assessment_scope === "product_family" && decision(row) !== "pending" ? `<div class="wb-path">${row.classification_status === "category_proxy" ? "类目代理统计" : "竞争评估范围"}：${esc(row.product_family_label || row.product_form)} · ${fmt(row.family_sample_count)} 个样本</div>` : ""}
      ${row.source_categories?.length ? `<div class="wb-path">来源：${row.source_categories.map(s => esc(s.name)).join(" / ")}</div>` : ""}
      <p>${esc(row.opportunity_thesis)}</p><p>${esc(row.validation_flags)}${row.next_action ? "；" + esc(row.next_action) : ""}</p>
      <div class="wb-path">倒推到仓预算（非报价/利润）：${row.form_landed_cost_ceiling_median !== "" && row.form_landed_cost_ceiling_median != null ? "$" + Number(row.form_landed_cost_ceiling_median).toFixed(2) : "待核实 FBA 费用"} · 数据商费用覆盖 ${Math.round(Number(row.form_fee_coverage || 0) * 100)}%</div>
      ${row.classification_status === "title_rule" ? '<span class="wb-status">按标题初分，图片与规格待核验</span>' : ""}
      <div class="wb-products">${refs.map(ref => productCard(ref.asin, row.product_family_label || row.product_form, `月销 ${fmt(ref.sales)} · 评论 ${fmt(ref.reviews)}`)).join("") || '<span class="wb-empty">暂无通过基础筛选的参考产品</span>'}</div>
      </div></details>`;
  }

  function groupCounts(rows) {
    const n = state => rows.filter(x => decision(x) === state).length;
    return `${n("opportunity")} 机会 · ${n("watch") + n("pending")} 待判断 · ${n("reject")} 排除`;
  }

  function marketMarkup(group, query, selected) {
    const first = group.rows[0];
    const byFamily = new Map();
    group.rows.forEach(row => {
      const key = row.product_family || `legacy:${row.product_form}`;
      if (!byFamily.has(key)) byFamily.set(key, []);
      byFamily.get(key).push(row);
    });
    const familyMarkup = ([familyKey, rows]) => {
      const key = `${group.key}/${familyKey}`;
      const active = rows.filter(x => decision(x) !== "reject");
      const rejects = rows.filter(x => decision(x) === "reject");
      const visibleRejected = query || selected === "Reject category/form";
      return `<details class="wb-family" data-detail="${esc(key)}" ${query || selected || active.length ? "open" : ""}><summary>${esc(rows[0].product_family_label || rows[0].product_form)} <span class="wb-counts">${groupCounts(rows)} · ${rows.length} 个形态</span>${discardButton("family", rows[0].decision_keys?.family, rows[0].product_family_label || rows[0].product_form)}</summary>
        ${rows[0].family_sources?.length ? `<div class="wb-path">${rows[0].family_sources.map(s => esc(s.name)).join(" / ")} · 去重后 ${fmt(rows[0].family_sample_count)} 个 ASIN · 跨来源重复 ${fmt(rows[0].family_duplicate_count)} 条${rows[0].family_conflict_count ? ` · ${fmt(rows[0].family_conflict_count)} 个 ASIN 来源数值不一致，按固定来源优先级取一份` : ""}</div>` : ""}
        ${active.map((row, i) => shape(row, `${key}/active/${i}`, Boolean(query))).join("")}
        ${rejects.length ? `<details class="wb-rejected" data-detail="${esc(key)}/rejected" ${visibleRejected ? "open" : ""}><summary>已排除形态 ${rejects.length}</summary>${rejects.map((row, i) => shape(row, `${key}/reject/${i}`, Boolean(query))).join("")}</details>` : ""}</details>`;
    };
    const families = [...byFamily];
    const activeFamilies = families.filter(([, rows]) => rows.some(r => decision(r) !== "reject"));
    const rejectedFamilies = families.filter(([, rows]) => rows.every(r => decision(r) === "reject"));
    const sources = new Map(group.rows.flatMap(row => (row.family_sources || []).map(s => [s.id, s])));
    const sampleCount = families.reduce((total, [, rows]) => total + Number(rows[0].family_sample_count || 0), 0);
    return `<details class="wb-category" data-detail="${esc(group.key)}" ${query || selected || group.rows.some(x => decision(x) !== "reject") ? "open" : ""}>
      <summary>${esc(first.market_label || first.source_category_name || first.category_path)} <span class="wb-counts">${byFamily.size} 组产品 · ${groupCounts(group.rows)}</span>${discardButton("market", first.decision_keys?.market, first.market_label || first.source_category_name || first.category_path)}</summary>
      ${sources.size ? `<div class="wb-path">${sources.size} 个来源类目 · ${fmt(sampleCount)} 个去重 ASIN · 扫描批次 ${esc(first.snapshot_id)}<br>来源：${[...sources.values()].map(s => esc(s.name)).join(" / ")}</div>` : `<div class="wb-path">${esc(first.category_path)} · 来源证据未合并</div>`}
      ${activeFamilies.map(familyMarkup).join("")}
      ${rejectedFamilies.length ? `<details class="wb-rejected" data-detail="${esc(group.key)}/rejected-families" ${query || selected ? "open" : ""}><summary>已排除产品组 ${rejectedFamilies.length}</summary>${rejectedFamilies.map(familyMarkup).join("")}</details>` : ""}</details>`;
  }

  function renderShapes(input) {
    data = input;
    const target = document.getElementById("shapeValidationList");
    if (!target) return;
    const opened = new Map([...target.querySelectorAll("details[data-detail]")].map(x => [x.dataset.detail, x.open]));
    const query = document.getElementById("shapeSearch").value.trim().toLowerCase();
    const selected = document.getElementById("shapeRecommendation").value;
    const grouped = new Map();
    (data.shapeValidation || []).forEach(row => {
      if (isDiscarded(row)) return;
      if (selected && row.shape_recommendation !== selected) return;
      const titleText = references(row).map(ref => known(ref.asin).title).join(" ");
      const sourceText = (row.source_categories || []).map(s => `${s.name} ${s.path} ${s.id}`).join(" ");
      if (query && ![row.market_label,row.product_form,row.product_form_label,row.product_family_label,row.source_category_name,row.category_path,row.reference_asins,titleText,sourceText].join(" ").toLowerCase().includes(query)) return;
      const key = row.market_id || row.source_category_id || row.category_path;
      if (!grouped.has(key)) grouped.set(key, {key, rows: []});
      grouped.get(key).rows.push(row);
    });
    const groups = [...grouped.values()].sort((a,b) => Number(a.rows[0].category_health_rank || 9999) - Number(b.rows[0].category_health_rank || 9999));
    const active = groups.filter(group => group.rows.some(row => decision(row) !== "reject"));
    const rejected = groups.filter(group => group.rows.every(row => decision(row) === "reject"));
    target.innerHTML = active.map(group => marketMarkup(group, query, selected)).join("") +
      (rejected.length ? `<details class="wb-rejected" data-detail="all-rejected" ${query || selected ? "open" : ""}><summary>全部形态已排除的产品组 ${rejected.length}</summary>${rejected.map(group => marketMarkup(group, query, selected)).join("")}</details>` : "") || '<div class="wb-empty">没有匹配的产品或形态</div>';
    if (!query && !selected) target.querySelectorAll("details[data-detail]").forEach(node => { if (opened.has(node.dataset.detail)) node.open = opened.get(node.dataset.detail); });
    const opportunityCount = document.getElementById("visibleShapeOpportunityCount");
    if (opportunityCount) opportunityCount.textContent = fmt((data.shapeValidation || []).filter(r => !isDiscarded(r) && decision(r) === "opportunity").length);
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
