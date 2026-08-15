/* Kube Siesta — static SPA wired to the engine's /api/v1.
   No build step, no framework: plain fetch() against the REST API. */
const App = (() => {
  const $ = (id) => document.getElementById(id);
  const el = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  const state = {
    apiBase: "",
    clusters: [], activeCluster: null,
    allNs: false, selected: new Set(), depCache: {},
    sources: [], editingSource: null,
    lastRun: null, lastRunType: "job", evidence: null,
    mode: "job",
  };

  // ---- mode toggle ------------------------------------------------------
  function setMode(m) {
    state.mode = m;
    document.querySelectorAll("#modeseg .seg-btn").forEach((b) => b.classList.toggle("active", b.dataset.m === m));
    $("modehint").textContent = m === "job"
      ? "Find idle workloads to move to Jobs / scale-to-zero"
      : "Find the lowest-impact downtime window for an app + its upstream dependents";
    // Reset the results banner so leftover state from the other mode doesn't confuse.
    const rb = $("runbanner"); if (rb) rb.style.display = "none";
    // Nudge the s4 title/pill so if the user is already on step 4 it re-labels correctly.
    setStep4Chrome();
  }

  function setStep4Chrome() {
    if (state.mode === "job") {
      $("s4title").textContent = "Strategic workload recommendations";
      $("s4sub").textContent = "Workloads that are mostly idle with predictable periodic spikes — good candidates for a Job / CronJob or scale-to-zero.";
    } else {
      $("s4title").textContent = "Maintenance window recommendations";
      $("s4sub").textContent = "Lowest-impact downtime windows before your deadline, across the target and its upstream dependents.";
    }
  }

  // Config-dispatch used by the "Configure & run ▸" button on step 3.
  function openConfig() {
    if (state.mode === "maint") {
      populateMaintTargets();
      openM("m-config-maint");
    } else {
      openM("m-config");
    }
  }

  function populateMaintTargets() {
    const sel = $("maint-target");
    sel.innerHTML = "";
    // Populate from workloads selected in step 2. If none, ask user to go back.
    const uids = [...state.selected];
    if (!uids.length) {
      sel.innerHTML = `<option value="">— pick a workload in step 2 first —</option>`;
      return;
    }
    uids.forEach((uid) => {
      const opt = document.createElement("option");
      opt.value = uid; opt.textContent = uid;
      sel.appendChild(opt);
    });
  }

  // ---- API client -------------------------------------------------------
  async function api(method, path, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    const res = await fetch(state.apiBase + path, opts);
    const text = await res.text();
    const data = text ? JSON.parse(text) : null;
    if (!res.ok) { const e = new Error((data && data.detail) || res.statusText); e.status = res.status; e.data = data; throw e; }
    return data;
  }

  function defaultBase() {
    const saved = localStorage.getItem("kubesiesta_api");
    if (saved) return saved;
    const q = new URLSearchParams(location.search).get("api");
    if (q) return q;
    return location.origin.startsWith("http") ? location.origin + "/api/v1" : "http://localhost:8000/api/v1";
  }

  async function checkHealth() {
    try { await api("GET", "/healthz"); $("apidot").className = "dot"; $("apienv").textContent = "API connected"; $("apistatus").innerHTML = '<span class="ok">● reachable</span>'; return true; }
    catch { $("apidot").className = "dot off"; $("apienv").textContent = "API unreachable"; $("apistatus").innerHTML = '<span class="bad">● unreachable — check the URL</span>'; return false; }
  }

  function setApiBase(v) {
    state.apiBase = (v || $("apibase").value).replace(/\/+$/, "");
    localStorage.setItem("kubesiesta_api", state.apiBase);
    $("apibase").value = state.apiBase;
    init(true);
  }

  // ---- navigation / modals ---------------------------------------------
  function goto(n) {
    document.querySelectorAll(".screen").forEach((s) => s.classList.remove("show"));
    $("s" + n).classList.add("show");
    document.querySelectorAll(".step").forEach((st) => { const i = +st.dataset.s; st.classList.toggle("active", i === n); st.classList.toggle("done", i < n); });
    window.scrollTo(0, 0);
    if (n === 3) loadSources();
  }
  const openM = (id) => $(id).classList.add("show");
  const closeM = (id) => $(id).classList.remove("show");
  let toastT;
  function toast(msg) { const t = $("toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("show"), 2600); }

  // ---- clusters ---------------------------------------------------------
  async function loadClusters() {
    try { state.clusters = (await api("GET", "/clusters")).clusters; } catch { state.clusters = []; }
    const grid = $("clusters"); grid.innerHTML = "";
    const pillCls = (status) => status === "reachable" ? "green" : status === "unreachable" ? "amber" : "gray";
    state.clusters.forEach((c) => {
      grid.appendChild(el(`<div class="card">
        <div class="row" style="margin-bottom:10px"><span class="pill ${pillCls(c.status)}">● ${esc(c.status || "added")}</span><b>${esc(c.name)}</b></div>
        <div class="small muted">${esc(c.api_url || "—")} · ${esc(c.auth_method || "n/a")}</div>
        <div class="row" style="margin-top:12px">
          <button class="btn sm primary" data-sel="${c.id}">Select →</button>
          <button class="btn sm" data-test="${c.id}">Test</button>
          <button class="btn ghost sm danger" data-del="${c.id}" style="margin-left:auto">Remove</button>
        </div></div>`));
    });
    grid.appendChild(el(`<div class="card" style="border-style:dashed;display:flex;align-items:center;justify-content:center;color:var(--muted);cursor:pointer" id="addcl">+ Add another cluster</div>`));
    $("addcl").onclick = () => openM("m-cluster");
    grid.querySelectorAll("[data-sel]").forEach((b) => b.onclick = () => { selectCluster(b.dataset.sel); goto(2); });
    grid.querySelectorAll("[data-test]").forEach((b) => b.onclick = () => testCluster(b.dataset.test));
    grid.querySelectorAll("[data-del]").forEach((b) => b.onclick = () => deleteCluster(b.dataset.del));
    renderClusterDD();
    if (!state.activeCluster && state.clusters.length) state.activeCluster = state.clusters[0].id;
  }

  function renderClusterDD() {
    const dd = $("cldd"); dd.innerHTML = "";
    state.clusters.forEach((c) => {
      const item = el(`<div class="dd-item ${c.id === state.activeCluster ? "active" : ""}"><span>${esc(c.name)}</span><span class="pill green" style="margin-left:auto">added</span></div>`);
      item.onclick = () => { selectCluster(c.id); dd.style.display = "none"; };
      dd.appendChild(item);
    });
    const active = state.clusters.find((c) => c.id === state.activeCluster);
    $("clname").textContent = active ? active.name : "—";
  }

  async function saveCluster(cont) {
    const name = $("cl-name").value.trim();
    if (!name) return toast("Cluster name is required");
    const method = document.querySelector("#authtabs .tab.active").dataset.a;
    const url = { token: "cl-url-tok", client_cert: "cl-url-crt", basic: "cl-url-bsc" }[method];
    const ref = { kubeconfig: "cl-ref-kc", token: "cl-ref-tok", client_cert: "cl-ref-crt", basic: "cl-ref-bsc" }[method];
    const body = { name, auth_method: method, api_url: url ? $(url).value.trim() || null : null, credential_ref: $(ref).value.trim() || null };
    try {
      const created = await api("POST", "/clusters", body);
      toast(`Cluster “${name}” added`); closeM("m-cluster"); $("cl-name").value = "";
      await loadClusters();
      if (cont) { selectCluster(created.id); goto(2); }
    } catch (e) { toast(`Add failed: ${e.message}`); }
  }
  async function deleteCluster(id) { try { await api("DELETE", "/clusters/" + id); if (state.activeCluster === id) state.activeCluster = null; toast("Cluster removed"); loadClusters(); } catch (e) { toast(e.message); } }
  function probeMsg(r) { return r.reachable ? `✓ Reachable${r.server_version ? " · " + r.server_version : ""}${r.detail ? " — " + r.detail : ""}` : `✗ Unreachable: ${r.detail || "unknown error"}`; }
  async function testCluster(id) { toast("Testing connection…"); try { const r = await api("POST", `/clusters/${id}:test`); toast(probeMsg(r)); loadClusters(); } catch (e) { toast(`Test failed: ${e.message}`); } }
  async function testConnection() {
    const method = document.querySelector("#authtabs .tab.active").dataset.a;
    const url = { token: "cl-url-tok", client_cert: "cl-url-crt", basic: "cl-url-bsc" }[method];
    const ref = { kubeconfig: "cl-ref-kc", token: "cl-ref-tok", client_cert: "cl-ref-crt", basic: "cl-ref-bsc" }[method];
    const apiUrl = url ? $(url).value.trim() || null : null;
    const credRef = $(ref).value.trim() || null;
    if (!apiUrl && !credRef) { return toast("Enter an API URL and/or a credential reference first."); }
    toast("Testing connection…");
    try { const r = await api("POST", "/clusters:test", { auth_method: method, api_url: apiUrl, credential_ref: credRef }); toast(probeMsg(r)); } catch (e) { toast(`Test failed: ${e.message}`); }
  }

  // ---- workloads (step 2) ----------------------------------------------
  function toggleClDD(e) { e.stopPropagation(); const d = $("cldd"); d.style.display = d.style.display === "none" ? "block" : "none"; }
  function selectCluster(id) {
    state.activeCluster = id; state.selected.clear(); state.depCache = {}; state.allNs = false; $("allns").checked = false;
    renderClusterDD(); loadNamespaces(); updateCount();
  }

  async function loadNamespaces() {
    const tree = $("tree"); tree.style.opacity = "1"; tree.style.pointerEvents = "auto";
    tree.innerHTML = `<div class="loadrow"><span class="spin"></span> Loading namespaces…</div>`;
    let namespaces = [];
    try { namespaces = (await api("GET", `/clusters/${state.activeCluster}/namespaces`)).namespaces; } catch (e) { tree.innerHTML = `<div class="empty">Couldn't load namespaces: ${esc(e.message)}</div>`; return; }
    if (!namespaces.length) { tree.innerHTML = `<div class="empty">No workloads discovered yet.<br><span class="small">Ingest metrics for this cluster first (<code>collector ingest</code> or <code>engine synth --seed-db</code>).</span></div>`; return; }
    tree.innerHTML = "";
    namespaces.sort((a, b) => a.name.localeCompare(b.name)).forEach((ns) => {
      const row = el(`<div class="ns-row"><span class="caret">▸</span><input type="checkbox" onclick="event.stopPropagation()"><b>${esc(ns.name)}</b><span class="small muted" style="margin-left:auto">expand to load</span></div>`);
      const wrap = el(`<div style="display:none" data-loaded="0"></div>`);
      const nscount = row.querySelector(".small.muted");
      row.querySelector('input').onchange = (ev) => selectNamespace(ns.name, wrap, ev.target.checked);
      row.onclick = () => {
        if (state.allNs) return;
        const open = wrap.style.display === "none";
        wrap.style.display = open ? "block" : "none";
        row.querySelector(".caret").classList.toggle("open", open);
        if (open && wrap.dataset.loaded === "0") loadDeps(ns.name, wrap, nscount);
      };
      tree.appendChild(row); tree.appendChild(wrap);
    });
  }

  async function loadDeps(ns, wrap, nscount) {
    nscount.innerHTML = '<span class="spin" style="width:12px;height:12px"></span>';
    wrap.innerHTML = `<div class="loadrow"><span class="spin"></span> Fetching workloads…</div>`;
    let workloads = [];
    try { workloads = (await api("GET", `/clusters/${state.activeCluster}/namespaces/${encodeURIComponent(ns)}/workloads`)).workloads; }
    catch (e) { wrap.innerHTML = `<div class="loadrow">Error: ${esc(e.message)}</div>`; return; }
    state.depCache[ns] = workloads;
    wrap.innerHTML = "";
    workloads.forEach((w) => {
      const checked = state.selected.has(w.workload_uid) ? "checked" : "";
      const dep = el(`<div class="dep"><input type="checkbox" class="depchk" ${checked}><span>${esc(w.name)}</span><span class="pill gray" style="margin-left:auto">${esc(w.kind)}</span></div>`);
      dep.querySelector("input").onchange = (ev) => { ev.target.checked ? state.selected.add(w.workload_uid) : state.selected.delete(w.workload_uid); updateCount(); };
      wrap.appendChild(dep);
    });
    nscount.textContent = `${workloads.length} workload${workloads.length === 1 ? "" : "s"}`;
    wrap.dataset.loaded = "1"; updateCount();
  }

  async function selectNamespace(ns, wrap, checked) {
    // Selecting a whole namespace needs its workload list; fetch if not cached.
    if (!state.depCache[ns]) { try { state.depCache[ns] = (await api("GET", `/clusters/${state.activeCluster}/namespaces/${encodeURIComponent(ns)}/workloads`)).workloads; } catch { return; } }
    state.depCache[ns].forEach((w) => checked ? state.selected.add(w.workload_uid) : state.selected.delete(w.workload_uid));
    wrap.querySelectorAll(".depchk").forEach((c) => (c.checked = checked));
    updateCount();
  }

  function toggleAll(cb) { state.allNs = cb.checked; const t = $("tree"); t.style.opacity = state.allNs ? ".4" : "1"; t.style.pointerEvents = state.allNs ? "none" : "auto"; updateCount(); }
  function updateCount() {
    const el2 = $("selcount");
    if (state.allNs) { el2.textContent = "All workloads · all namespaces"; return; }
    const n = state.selected.size; el2.textContent = n ? `${n} workload${n === 1 ? "" : "s"} selected` : "None selected";
  }
  function currentScope() {
    if (state.allNs || state.selected.size === 0) return "all";
    return { workload_uids: [...state.selected] };
  }

  // ---- data sources (step 3) -------------------------------------------
  async function loadSources() {
    if (!state.activeCluster) { $("sources").innerHTML = `<div class="empty">Connect and select a cluster first.</div>`; return; }
    try { state.sources = (await api("GET", `/clusters/${state.activeCluster}/sources`)).sources; } catch { state.sources = []; }
    const grid = $("sources"); grid.innerHTML = "";
    const tag = { prometheus: "blue", opencost: "amber", mesh: "purple", custom_api: "gray", file: "gray" };
    const kind = { prometheus: "metrics", opencost: "cost", mesh: "interactions", custom_api: "custom", file: "import" };
    if (!state.sources.length) grid.appendChild(el(`<div class="empty">No data sources yet. Add Prometheus to start.</div>`));
    state.sources.forEach((s) => {
      const health = s.health === "healthy" ? '<span class="pill green">● healthy</span>' : s.health === "unreachable" ? '<span class="pill amber">● unreachable</span>' : '<span class="pill gray">not checked</span>';
      const card = el(`<div class="card">
        <div class="between"><b>${esc(s.name)}</b><span class="pill ${tag[s.type] || "gray"}">${esc(kind[s.type] || s.type)}</span></div>
        <div class="small muted" style="margin:8px 0">${esc(s.endpoint || "—")}</div>
        <div class="row" style="margin-top:10px">${health}<button class="btn ghost sm" style="margin-left:auto" data-edit="${s.id}">Edit</button></div></div>`);
      card.querySelector("[data-edit]").onclick = () => openEdit(s.id);
      grid.appendChild(card);
    });
  }
  async function saveSource() {
    const body = { type: $("srctype").value, name: $("src-name").value.trim() || $("srctype").value, endpoint: $("src-endpoint").value.trim() || null };
    try { await api("POST", `/clusters/${state.activeCluster}/sources`, body); toast("Source added"); closeM("m-source"); $("src-name").value = ""; $("src-endpoint").value = ""; loadSources(); }
    catch (e) { toast(`Add failed: ${e.message}`); }
  }
  function openEdit(id) {
    const s = state.sources.find((x) => x.id == id); state.editingSource = s;
    $("edit-title").textContent = `Edit data source — ${s.name}`;
    $("editbody").innerHTML = `<label class="f">Name</label><input id="e-name" type="text" value="${esc(s.name)}" style="margin-bottom:12px">
      <label class="f">Endpoint URL</label><input id="e-endpoint" type="url" value="${esc(s.endpoint || "")}" style="margin-bottom:12px">
      <label class="chk"><input type="checkbox" id="e-enabled" ${s.enabled ? "checked" : ""}> enabled</label>`;
    openM("m-editsrc");
  }
  async function saveSourceEdit() {
    const id = state.editingSource.id;
    try { await api("PUT", "/sources/" + id, { name: $("e-name").value.trim(), endpoint: $("e-endpoint").value.trim() || null, enabled: $("e-enabled").checked }); toast("Saved"); closeM("m-editsrc"); loadSources(); }
    catch (e) { toast(e.message); }
  }
  async function deleteSource() { try { await api("DELETE", "/sources/" + state.editingSource.id); toast("Source removed"); closeM("m-editsrc"); loadSources(); } catch (e) { toast(e.message); } }
  async function testSource() { try { const r = await api("POST", `/sources/${state.editingSource.id}:test`); toast(`Health: ${r.health}`); loadSources(); } catch (e) { toast(e.message); } }

  // ---- run flow ---------------------------------------------------------
  function setStep(id, st, txt) {
    const ic = st === "spin" ? '<span class="spin"></span>' : st === "done" ? '<span style="color:var(--green)">✓</span>' : st === "warn" ? '<span style="color:var(--amber)">⚠</span>' : '<span style="color:var(--faint)">○</span>';
    $(id).innerHTML = ic + " <span>" + txt + "</span>";
  }
  async function startRun() {
    if (!state.activeCluster) return toast("Select a cluster first");
    const isMaint = state.mode === "maint";

    let body;
    if (isMaint) {
      const target = $("maint-target").value.trim();
      const duration = $("maint-duration").value.trim();
      const deadline = $("maint-deadline").value;
      const resample = $("maint-resample").value.trim();
      if (!target) { toast("Pick a target workload in step 2 first"); return; }
      if (!duration) { toast("Enter a maintenance duration (e.g. 30m, 2h)"); return; }
      const resources = [...document.querySelectorAll(".maint-rescb:checked")].map((c) => c.value);
      const config = { resources };
      if (resample) config.resample_freq = resample;
      body = {
        cluster_id: state.activeCluster,
        run_type: "maintenance",
        config,
        maintenance: { target_workload_uid: target, duration, deadline },
      };
      closeM("m-config-maint");
    } else {
      const resources = [...document.querySelectorAll(".rescb:checked")].map((c) => c.value);
      const window = $("cfg-window").value;
      const minp = parseInt($("cfg-minperiod").value, 10);
      const config = { resources, window }; if (!isNaN(minp)) config.min_period = minp;
      body = { cluster_id: state.activeCluster, scope: currentScope(), config, collectData: false };
      closeM("m-config");
    }

    setStep("rs1", "spin", "Collecting metrics…");
    setStep("rs2", "wait", isMaint ? "Finding low-impact windows" : "Running analysis");
    setStep("rs3", "wait", "Preparing recommendations");
    openM("m-run");

    // Step 1 — trigger collection and poll it (failure-tolerant: engine uses stored data).
    // Maintenance doesn't yet support collectData through the runner, but the collector
    // service is still worth kicking off so the DB has fresh data.
    let collectionOk = false;
    try {
      const col = await api("POST", "/collections", {
        cluster_id: state.activeCluster,
        scope: isMaint ? "all" : currentScope(),
        resources: body.config.resources,
        window: isMaint ? "7d" : body.config.window,
      });
      let cstatus = col.status;
      for (let i = 0; i < 120 && cstatus === "running"; i++) {
        await new Promise((r) => setTimeout(r, 500));
        cstatus = (await api("GET", "/collections/" + col.collection_id)).status;
      }
      if (cstatus === "success" || cstatus === "partial") { collectionOk = true; setStep("rs1", "done", "Metrics collected · data as of just now"); }
      else { setStep("rs1", "warn", "Collection failed — using stored data"); }
    } catch (e) {
      setStep("rs1", "warn", e.status === 503 ? "Collector unavailable — using stored data" : "Collection skipped — using stored data");
    }

    // Step 2 — run the engine.
    setStep("rs2", "spin", isMaint ? "Projecting patterns + scoring windows…" : "Running analysis…");
    let run;
    try { run = await api("POST", "/runs", body); }
    catch (e) { setStep("rs2", "warn", `Run failed: ${e.message}`); toast(e.message); return; }
    // Poll until completed (usually already completed since the run is synchronous).
    let status = run.status;
    for (let i = 0; i < 40 && status !== "completed" && status !== "failed"; i++) { await new Promise((r) => setTimeout(r, 250)); status = (await api("GET", "/runs/" + run.run_id)).status; }
    setStep("rs2", "done", isMaint ? "Windows scored" : "Analysis complete");

    // Step 3 — load recommendations.
    setStep("rs3", "spin", "Preparing recommendations…");
    const cards = await api("GET", `/runs/${run.run_id}/recommendations`);
    state.lastRun = run.run_id;
    state.lastRunType = cards.run.run_type || (isMaint ? "maintenance" : "job");
    setStep("rs3", "done", "Ready");
    setTimeout(() => { closeM("m-run"); renderRecommendations(cards, collectionOk); goto(4); }, 350);
  }

  function renderRecommendations(cards, collectionOk) {
    setStep4Chrome();
    const run = cards.run;
    const isMaint = run.run_type === "maintenance";
    const rb = $("runbanner");
    const scope = isMaint
      ? `deadline in ${esc(String(fmtDeadlineHint(run.deadline)))}`
      : esc(run.window || "");
    if (run.stale || !collectionOk) {
      rb.className = "hint warn";
      rb.innerHTML = `<span>⚠</span><span>Run <b>${esc(run.name)}</b> · ${esc(run.cluster || "")} · showing <b>stored data</b> as of <b>${esc(run.data_as_of || "unknown")}</b> · ${scope}.</span>`;
    } else {
      rb.className = "hint";
      rb.innerHTML = `<span>ⓘ</span><span>Run <b>${esc(run.name)}</b> · ${esc(run.cluster || "")} · data as of <b>${esc(run.data_as_of || "just now")}</b> · ${scope}.</span>`;
    }
    rb.style.display = "flex";

    if (isMaint) renderMaintenanceCards(cards);
    else renderJobCards(cards);
  }

  function renderJobCards(cards) {
    const run = cards.run;
    $("candcount").textContent = `${cards.recommendations.length} candidate${cards.recommendations.length === 1 ? "" : "s"} · ${esc(run.window || "")}`;
    const grid = $("recos"); grid.innerHTML = "";
    if (!cards.recommendations.length) { grid.appendChild(el(`<div class="empty">No candidates found in this scope — nothing is idle-with-periodic-spikes enough to shift.</div>`)); return; }
    cards.recommendations.forEach((r) => {
      const cc = r.confidence === "high" ? "green" : r.confidence === "medium" ? "amber" : "gray";
      const save = r.savings && r.savings.amount != null ? `$${Math.round(r.savings.amount)}/mo` : "—";
      const card = el(`<div class="reco">
        <div class="h"><span class="name">${esc(r.workload.name)}</span><span class="pill ${cc}">${esc(r.confidence)} confidence</span></div>
        <div class="flowline"><div><div class="k">from</div><div class="v">${esc(r.from)}</div></div><div>→</div>
          <div class="to"><div class="k">to</div><div class="v">${esc(r.to_target)}</div><div class="small muted">${esc(r.cadence || "")}</div></div></div>
        <div class="row" style="gap:8px;margin-bottom:4px"><span class="pill green">Cost saving ${save}</span><span class="pill gray">run ${esc(r.run_time || "—")}</span></div>
        <div class="foot"><button class="btn ghost sm" data-why="${r.id}">Why?</button><button class="btn sm" data-sim="${r.id}" style="margin-left:auto">Similar ▸</button></div></div>`);
      card.querySelector("[data-why]").onclick = () => openWhy(r.id, r.workload.name);
      card.querySelector("[data-sim]").onclick = () => openSimilar(r.id, r.workload.name);
      grid.appendChild(card);
    });
  }

  function renderMaintenanceCards(cards) {
    $("candcount").textContent = `${cards.recommendations.length} window${cards.recommendations.length === 1 ? "" : "s"} · deadline ${fmtDate(cards.run.deadline)}`;
    const grid = $("recos"); grid.innerHTML = "";
    if (!cards.recommendations.length) { grid.appendChild(el(`<div class="empty">No maintenance recommendations — check the target workload was found in step 2.</div>`)); return; }
    cards.recommendations.forEach((r) => {
      const cc = r.confidence === "high" ? "green" : r.confidence === "medium" ? "amber" : "gray";
      const winTxt = `${fmtDateTime(r.recommended_start)} – ${fmtTime(r.recommended_end)}`;
      const durTxt = `${Math.round(r.duration_minutes || 0)} min`;
      const chips = (r.impacted_apps_preview || [])
        .map((a) => `<span class="pill gray">${esc(a.name || a.workload_uid || "—")}</span>`).join(" ")
        || `<span class="pill gray">no upstream callers detected</span>`;
      const card = el(`<div class="reco">
        <div class="h"><span class="name">${esc(r.workload.name)}</span><span class="pill ${cc}">${esc(r.confidence)} impact confidence</span></div>
        <div class="k" style="margin-top:2px">recommended window</div>
        <div class="v" style="font-size:15px">${esc(winTxt)}</div>
        <div class="small muted" style="margin-bottom:10px">duration ${esc(durTxt)} · before your deadline</div>
        <div class="k" style="margin-bottom:5px">impacted upstream apps (${r.impacted_apps_count || 0})</div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px">${chips}</div>
        <div class="foot"><button class="btn ghost sm" data-why="${r.id}">Why?</button>
          <button class="btn sm" data-imp="${r.id}" style="margin-left:auto">Impacted apps ▸</button></div></div>`);
      card.querySelector("[data-why]").onclick = () => openMaintWhy(r.id, r.workload.name);
      card.querySelector("[data-imp]").onclick = () => openImpacted(r.id, r.workload.name);
      grid.appendChild(card);
    });
  }

  // ---- formatting helpers (maintenance display) -------------------------
  function fmtDateTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso); if (isNaN(d)) return iso;
    return d.toISOString().replace("T", " ").replace(/:\d\d\.\d+Z$/, " UTC");
  }
  function fmtTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso); if (isNaN(d)) return iso;
    return d.toISOString().slice(11, 16) + " UTC";
  }
  function fmtDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso); if (isNaN(d)) return iso;
    return d.toISOString().slice(0, 10);
  }
  function fmtDeadlineHint(iso) {
    if (!iso) return "—";
    const d = new Date(iso); if (isNaN(d)) return iso;
    const hrs = Math.max(0, (d - Date.now()) / 3600000);
    if (hrs < 48) return `${Math.round(hrs)}h`;
    return `${Math.round(hrs / 24)}d`;
  }

  // ---- why / similar ----------------------------------------------------
  async function openWhy(recId, name) {
    let ev; try { ev = await api("GET", `/runs/${state.lastRun}/recommendations/${recId}/evidence`); } catch (e) { return toast(e.message); }
    state.evidence = ev;
    $("why-text").innerHTML = `<b>${esc(name)}</b> — ${esc(ev.summary)}`;
    const m = ev.metrics;
    $("why-metrics").innerHTML = `
      <div><span class="k">Peak load jump</span><span class="v">+${Math.round(m.jump_pct)}%</span></div>
      <div><span class="k">Active / idle</span><span class="v">${(m.active_idle_ratio || 0).toFixed(2)}</span></div>
      <div><span class="k">Period P</span><span class="v">${m.period_hours} h</span></div>
      <div><span class="k">Active duration</span><span class="v">${Math.round(m.active_duration_min)} min</span></div>
      <div><span class="k">Resource overlap</span><span class="v">${Math.round(m.overlap_pct)}%</span></div>
      <div><span class="k">Confidence</span><span class="v">${esc(m.confidence)}</span></div>`;
    $("why-chart-toggle").checked = true;
    drawChart();
    openM("m-why");
  }
  function toggleChart() { $("whychart").style.display = $("why-chart-toggle").checked ? "block" : "none"; if ($("why-chart-toggle").checked) drawChart(); }

  function drawChart() {
    const svg = $("whychart"); const ev = state.evidence;
    const series = ev && ev.series && ev.series[0];
    if (!series || !series.points || !series.points.length) { svg.innerHTML = `<text x="12" y="24" font-size="12" fill="#8a93a3">no chart series available</text>`; return; }
    $("why-chart-label").textContent = `${series.resource} (${series.unit || ""})`;
    const W = 660, H = 180, pad = 16;
    const pts = series.points, ov = series.overlay || {};
    const vals = pts.map((p) => p.v);
    const times = pts.map((p) => Date.parse(p.t));
    let vmin = Math.min(...vals), vmax = Math.max(...vals);
    if (ov.eps_max != null) vmax = Math.max(vmax, ov.eps_max);
    if (ov.eps_min != null) vmin = Math.min(vmin, ov.eps_min);
    const span = vmax - vmin || 1;
    const x = (i) => pad + i * (W - 2 * pad) / (pts.length - 1 || 1);
    const y = (v) => H - pad - (v - vmin) / span * (H - 2 * pad);
    let svgparts = "";
    // active-window shading
    (ov.active_windows || []).forEach((w) => {
      const s = Date.parse(w.start), e = Date.parse(w.end);
      let i0 = times.findIndex((t) => t >= s); let i1 = times.length - 1 - [...times].reverse().findIndex((t) => t <= e);
      if (i0 < 0) return; if (i1 < i0) i1 = i0;
      svgparts += `<rect x="${x(i0).toFixed(1)}" y="${pad}" width="${Math.max(1, x(i1) - x(i0)).toFixed(1)}" height="${H - 2 * pad}" fill="#eaf1fe"/>`;
    });
    // eps band
    if (ov.eps_min != null && ov.eps_max != null) svgparts += `<rect x="${pad}" y="${y(ov.eps_max).toFixed(1)}" width="${W - 2 * pad}" height="${Math.max(1, y(ov.eps_min) - y(ov.eps_max)).toFixed(1)}" fill="#f0f5fb" stroke="#dbe6f4"/>`;
    // trend line
    if (ov.trend != null) svgparts += `<line x1="${pad}" y1="${y(ov.trend).toFixed(1)}" x2="${W - pad}" y2="${y(ov.trend).toFixed(1)}" stroke="#c9d3df" stroke-dasharray="4 4"/><text x="${pad + 2}" y="${(y(ov.trend) - 4).toFixed(1)}" font-size="10" fill="#8a93a3">trend</text>`;
    // series
    const poly = pts.map((p, i) => `${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
    svgparts += `<polyline fill="none" stroke="#2563eb" stroke-width="1.4" points="${poly}"/>`;
    svg.innerHTML = svgparts;
  }

  // ---- maintenance: why + impacted apps --------------------------------
  async function openMaintWhy(recId, name) {
    let ev; try { ev = await api("GET", `/runs/${state.lastRun}/recommendations/${recId}/evidence`); } catch (e) { return toast(e.message); }
    state.evidence = ev;
    $("mw-summary").innerHTML = `<b>${esc(name)}</b> — ${esc(ev.summary || "")}`;
    const m = ev.metrics || {};
    const deadlineHint = m.deadline ? fmtDeadlineHint(m.deadline) : "—";
    $("mw-metrics").innerHTML = `
      <div><span class="k">Impact score</span><span class="v">${(m.impact_score != null ? m.impact_score : "—")}</span></div>
      <div><span class="k">Confidence</span><span class="v">${esc(m.confidence || "—")}</span></div>
      <div><span class="k">Duration</span><span class="v">${Math.round(m.duration_minutes || 0)} min</span></div>
      <div><span class="k">Window</span><span class="v">${fmtTime(m.recommended_start)}–${fmtTime(m.recommended_end)}</span></div>
      <div><span class="k">Deadline</span><span class="v">in ${esc(String(deadlineHint))}</span></div>
      <div><span class="k">Upstream apps</span><span class="v">${(ev.impacted_apps || []).length}</span></div>`;
    drawMaintChart(ev);
    openM("m-maint-why");
  }

  async function openImpacted(recId, name) {
    let ev; try { ev = await api("GET", `/runs/${state.lastRun}/recommendations/${recId}/evidence?series=false`); } catch (e) { return toast(e.message); }
    $("imp-name").textContent = name;
    const box = $("imp-list"); box.innerHTML = "";
    const apps = ev.impacted_apps || [];
    if (!apps.length) {
      box.appendChild(el(`<div class="empty">No upstream callers found in the interactions graph — the chosen window depends only on the target's own idle time.</div>`));
      openM("m-impacted");
      return;
    }
    apps.forEach((a) => {
      const kind = a.period_hours == null ? "aperiodic" : `${a.period_hours.toFixed(1)}h cycle`;
      const kindPill = a.period_hours == null ? "amber" : "purple";
      const active = a.active_fraction != null ? Math.round(a.active_fraction * 100) + "%" : "—";
      const impact = a.impact_score != null ? a.impact_score : "—";
      const w = a.workload || {};
      const label = esc(w.name || a.workload_uid || "—");
      box.appendChild(el(`<div class="peer">
        <div class="between"><b>${label}</b><span class="pill ${kindPill}">${esc(kind)}</span></div>
        <div class="small muted" style="margin-top:4px">
          namespace <b>${esc(w.namespace || "—")}</b> · projected active <b>${active}</b> of the horizon · window overlap <b>${impact}</b>
        </div>
        ${a.note ? `<div class="small muted" style="margin-top:6px">${esc(a.note)}</div>` : ""}
      </div>`));
    });
    openM("m-impacted");
  }

  function drawMaintChart(ev) {
    const svg = $("maintchart");
    const series = (ev && ev.series) || [];
    if (!series.length || !series[0].points || !series[0].points.length) {
      svg.innerHTML = `<text x="12" y="24" font-size="12" fill="#8a93a3">no forecast series available</text>`;
      return;
    }
    const W = 660, H = 180, pad = 16;
    // Establish the common time axis from the first series' points.
    const times = series[0].points.map((p) => Date.parse(p.ts));
    const tmin = times[0], tmax = times[times.length - 1] || (tmin + 1);
    const xFor = (t) => pad + (t - tmin) / (tmax - tmin || 1) * (W - 2 * pad);
    const laneH = Math.max(14, Math.min(28, (H - 2 * pad) / Math.max(1, series.length + 1)));
    const palette = ["#2563eb", "#e0731f", "#7c5cd6", "#127a4a", "#b02a2a", "#5b46c4"];

    let g = "";
    // Chosen-window shading (from ev.metrics if present).
    const m = ev.metrics || {};
    if (m.recommended_start && m.recommended_end) {
      const xs = xFor(Date.parse(m.recommended_start));
      const xe = xFor(Date.parse(m.recommended_end));
      g += `<rect x="${xs.toFixed(1)}" y="${pad}" width="${Math.max(2, xe - xs).toFixed(1)}" height="${H - 2 * pad}" fill="#e7f6ee" stroke="#bfe6d0"/>`;
      g += `<text x="${((xs + xe) / 2).toFixed(1)}" y="${pad + 12}" font-size="10" fill="#127a4a" text-anchor="middle">chosen window</text>`;
    }
    // One thin band per workload, tinted where projected active.
    series.forEach((s, li) => {
      const color = palette[li % palette.length];
      const y = pad + 24 + li * laneH;
      // Baseline lane
      g += `<line x1="${pad}" y1="${(y + laneH / 2).toFixed(1)}" x2="${W - pad}" y2="${(y + laneH / 2).toFixed(1)}" stroke="#e4e7eb"/>`;
      // Active spans
      const pts = s.points; let start = null;
      for (let i = 0; i < pts.length; i++) {
        const active = !!pts[i].value;
        if (active && start === null) start = pts[i].ts;
        if ((!active || i === pts.length - 1) && start !== null) {
          const endTs = active ? pts[i].ts : pts[Math.max(0, i - 1)].ts;
          const xs = xFor(Date.parse(start)), xe = xFor(Date.parse(endTs));
          g += `<rect x="${xs.toFixed(1)}" y="${y.toFixed(1)}" width="${Math.max(1.5, xe - xs).toFixed(1)}" height="${laneH - 2}" fill="${color}" opacity="0.55"/>`;
          start = null;
        }
      }
      // Label
      const shortUid = (s.workload_uid || "").split("/").pop() || s.workload_uid || "";
      g += `<text x="${pad}" y="${(y - 4).toFixed(1)}" font-size="10" fill="#657084">${esc(shortUid)}</text>`;
    });
    svg.innerHTML = g;
  }

  async function openSimilar(recId, name) {
    let ev; try { ev = await api("GET", `/runs/${state.lastRun}/recommendations/${recId}/evidence`); } catch (e) { return toast(e.message); }
    $("sim-name").textContent = name;
    const box = $("sim-peers"); box.innerHTML = "";
    if (!ev.peers || !ev.peers.length) { box.appendChild(el(`<div class="empty">No interacting peers share this seasonality.</div>`)); openM("m-similar"); return; }
    ev.peers.forEach((p) => {
      const save = p.savings && p.savings.amount != null ? `$${Math.round(p.savings.amount)}` : "—";
      box.appendChild(el(`<div class="peer">
        <div class="between"><b>${esc(p.workload)}</b><span class="pill purple">shared seasonality</span></div>
        <div class="flowline"><div><div class="k">from</div><div class="v">Deployment</div></div><div>→</div>
          <div class="to"><div class="k">behaves like</div><div class="v">${esc(p.to_target)}</div></div></div>
        <div class="row"><span class="pill green">Est. saving ${save}</span><span class="small muted">${esc(p.note || "")}</span></div></div>`));
    });
    openM("m-similar");
  }

  // ---- init -------------------------------------------------------------
  async function init(reload) {
    if (!reload) {
      state.apiBase = defaultBase(); $("apibase").value = state.apiBase;
      document.querySelectorAll(".step").forEach((st) => (st.onclick = () => goto(+st.dataset.s)));
      document.querySelectorAll(".overlay").forEach((o) => o.addEventListener("click", (e) => { if (e.target === o) o.classList.remove("show"); }));
      document.querySelectorAll("#authtabs .tab").forEach((t) => t.onclick = () => {
        document.querySelectorAll("#authtabs .tab").forEach((x) => x.classList.remove("active")); t.classList.add("active");
        document.querySelectorAll(".authpane").forEach((p) => (p.style.display = "none"));
        $("auth-" + t.dataset.a).style.display = "block";
      });
      document.addEventListener("click", (e) => { const d = $("cldd"); if (d && !e.target.closest("#cldd") && !e.target.closest("button")) d.style.display = "none"; });
    }
    const ok = await checkHealth();
    if (ok) await loadClusters();
  }

  return { init, setApiBase, goto, openM, closeM, toggleClDD, toggleAll, saveCluster, testConnection, saveSource, openEdit, saveSourceEdit, deleteSource, testSource, startRun, openWhy, openSimilar, toggleChart,
           setMode, openConfig, openMaintWhy, openImpacted };
})();
document.addEventListener("DOMContentLoaded", () => App.init());
