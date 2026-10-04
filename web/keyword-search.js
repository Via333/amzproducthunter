/* Execution stays on the authenticated local origin; file mode only links there. */
(() => {
  "use strict";
  const form = document.getElementById("keywordSearchForm");
  if (!form) return;
  const input = document.getElementById("keywordSearchInput");
  const button = document.getElementById("keywordSearchSubmit");
  const status = document.getElementById("keywordSearchStatus");
  const list = document.getElementById("keywordSearchJobs");
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const normalize = value => value.trim().replace(/\s+/g, " ").toLowerCase();
  const active = job => ["queued", "running"].includes(job.status);
  const labels = {queued:"排队中", running:"搜索中", completed:"已完成", failed:"失败", interrupted:"已中断", cancelled:"已取消"};
  const storageKey = "amz-keyword-request-v1";
  let state = {online:false, jobs:null}, submitting = false, message = "", receipt = null;
  let loadedJob = 0, loadingJob = 0, latestStateJobs = "";
  try {
    receipt = JSON.parse(sessionStorage.getItem(storageKey) || "null");
    if (receipt?.keyword) input.value = receipt.keyword;
  } catch (_) { receipt = null; }
  // Carry only an inert keyword from a file page. Navigation never starts a paid search.
  const initial = new URLSearchParams(location.search).get("keyword");
  if (initial && initial.length <= 200) input.value = initial;
  function persist() {
    try { sessionStorage.setItem(storageKey, JSON.stringify(receipt)); } catch (_) { /* In-memory idempotency still applies. */ }
  }
  function render() {
    const fileMode = location.protocol === "file:";
    const supported = Array.isArray(state.jobs);
    const running = (state.jobs || []).find(job => active(job) && normalize(job.keyword) === normalize(input.value));
    button.disabled = submitting || !state.online || !supported || Boolean(running) || !input.value.trim();
    button.querySelector("span").textContent = submitting ? "提交中" : running ? labels[running.status] : "开始搜索";
    if (fileMode) {
      const url = new URL("http://127.0.0.1:8765/index.html");
      url.searchParams.set("keyword", input.value.trim());
      url.hash = "keyword-search";
      status.innerHTML = `本地文件版不能执行搜索。<a href="${esc(url.href)}">打开服务版搜索</a>`;
    } else {
      status.textContent = !state.online ? state.error || "正在连接搜索服务" : message || (running ? `${running.keyword} · ${labels[running.status]}` :
        state.online ? supported ? "" : "搜索服务需更新后才能执行" : state.error || "正在连接搜索服务");
    }
    const signature = JSON.stringify([state.jobs, state.online]);
    if (signature !== latestStateJobs) {
      latestStateJobs = signature;
      list.innerHTML = (state.jobs || []).slice(0, 10).map(job => `<div class="wb-job"><div class="wb-job-title"><strong>${esc(job.keyword)}</strong> · ${esc(labels[job.status] || job.status)}<div>${esc(job.message || "")}</div></div><div class="wb-actions">${job.status === "completed" ? `<button type="button" data-keyword-result="${job.id}"><i data-lucide="file-search" aria-hidden="true"></i>查看结果</button>` : job.status === "queued" ? `<button type="button" data-keyword-cancel="${job.id}" ${state.online ? "" : "disabled"} title="取消排队" aria-label="取消 ${esc(job.keyword)} 排队"><i data-lucide="x" aria-hidden="true"></i></button>` : ["failed","interrupted","cancelled"].includes(job.status) ? `<button type="button" data-keyword-retry="${job.id}" ${state.online ? "" : "disabled"} title="重新搜索，将使用 Sorftime 额度"><i data-lucide="rotate-cw" aria-hidden="true"></i>重试</button>` : ""}</div></div>`).join("");
      window.lucide?.createIcons();
    }
  }
  async function post(path, payload) {
    const response = await fetch(path, {method:"POST", headers:{"Content-Type":"application/json", "X-Workbench-Token":state.token}, body:JSON.stringify(payload), signal:AbortSignal.timeout(10000)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "提交失败");
    return result;
  }
  async function showResult(id) {
    if (loadingJob) return;
    loadingJob = id;
    try {
      const response = await fetch(`/api/keyword-search/results?job=${id}`, {cache:"no-store", signal:AbortSignal.timeout(10000)});
      const result = await response.json();
      if (!response.ok || !result.summary || !Array.isArray(result.rows)) throw new Error(result.error || "结果暂不可用");
      input.value = result.summary.keyword;
      window.dispatchEvent(new CustomEvent("amz-keyword-results", {detail:result}));
      message = `${result.summary.keyword} 搜索完成：${Number(result.summary.raw_result_count)} 个相关产品，${Number(result.summary.eligible_candidate_count)} 个通过基础过滤。`;
      loadedJob = id;
    } catch (error) { message = `结果读取失败：${error.message}。可点击查看结果重试，无需重新搜索。`; }
    finally { loadingJob = 0; render(); }
  }
  async function submit() {
    const keyword = input.value.trim().replace(/\s+/g, " ");
    if (submitting || !keyword || !state.online || !Array.isArray(state.jobs)) return;
    if (state.jobs.some(job => active(job) && normalize(job.keyword) === normalize(keyword))) return;
    submitting = true; message = "正在提交搜索任务";
    if (!receipt || normalize(receipt.keyword) !== normalize(keyword) ||
        state.jobs.some(job => job.id === receipt.id && !active(job))) {
      receipt = {keyword, request_id:crypto.randomUUID()};
      persist();
    }
    render();
    try {
      const job = await post("/api/keyword-search", {keyword, request_id:receipt.request_id});
      receipt.id = job.id; persist();
      state.jobs = [{...job, message:""}, ...state.jobs.filter(item => item.id !== job.id)];
      message = `${keyword} · ${labels[job.status] || job.status}`;
      await window.AmzWorkbench?.sync();
      if (job.status === "completed") await showResult(job.id);
    } catch (error) {
      message = `未确认提交成功：${error.message}。重试会沿用本次请求标识，避免重复执行。`;
    } finally { submitting = false; render(); }
  }
  form.addEventListener("submit", event => { event.preventDefault(); submit(); });
  input.addEventListener("input", () => { message = ""; render(); });
  list.addEventListener("click", async event => {
    const target = event.target.closest("button");
    if (!target || target.disabled) return;
    if (target.dataset.keywordResult) { await showResult(Number(target.dataset.keywordResult)); return; }
    if (target.dataset.keywordRetry) {
      const job = state.jobs.find(item => item.id === Number(target.dataset.keywordRetry));
      if (!job) return;
      input.value = job.keyword; receipt = null; persist(); await submit(); return;
    }
    if (target.dataset.keywordCancel) {
      target.disabled = true;
      try {
        await post("/api/research/cancel", {id:Number(target.dataset.keywordCancel)});
        message = "已取消排队，未启动搜索";
        await window.AmzWorkbench?.sync();
      } catch (error) { message = `取消失败：${error.message}`; }
      finally { if (target.isConnected) target.disabled = false; render(); }
    }
  });
  window.AmzKeywordSearch = {updateState(next) {
    state = next;
    const job = (state.jobs || []).find(item => item.id === receipt?.id || normalize(item.keyword) === normalize(input.value));
    if (job && receipt && normalize(job.keyword) === normalize(receipt.keyword) && active(job)) { receipt.id = job.id; persist(); }
    if (job && !submitting && state.online) {
      message = `${job.keyword} · ${labels[job.status]}${job.message ? "：" + job.message : ""}`;
      if (job.status === "completed" && job.id !== loadedJob) showResult(job.id);
    }
    render();
  }};
  render();
})();
