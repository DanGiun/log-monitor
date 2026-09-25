const state = {
  config: null,
  statuses: new Map(),
  panelEvents: new Map(),
  websocket: null,
  reconnectTimer: null,
  saveTimer: null,
  statusTimer: null,
  incidentTimer: null,
  incidents: [],
  incidentsLoading: false,
  activeSection: "logs",
};

const $ = (selector, root=document) => root.querySelector(selector);
const $$ = (selector, root=document) => [...root.querySelectorAll(selector)];

async function api(path, options={}) {
  const response = await fetch(path, {
    headers: {"Content-Type": "application/json", ...(options.headers || {})},
    ...options,
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try { const body = await response.json(); message = body.detail || message; } catch (_) {}
    throw new Error(message);
  }
  if (response.status === 204) return null;
  return response.json();
}

function toast(message, type="info") {
  const node = document.createElement("div");
  node.className = `toast ${type}`;
  node.textContent = message;
  $("#toast-container").append(node);
  setTimeout(() => node.remove(), 5000);
}

function activeWorkspace() {
  return state.config.workspaces.find(w => w.id === state.config.active_workspace_id) || state.config.workspaces[0];
}

function sourceById(id) { return state.config.sources.find(source => source.id === id); }

function displayBytes(value) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let amount = value, index = 0;
  while (amount >= 1024 && index < units.length - 1) { amount /= 1024; index++; }
  return `${amount.toFixed(index < 2 ? 0 : 1)} ${units[index]}`;
}

function shiftedDate(event) {
  return event.display_timestamp ? new Date(event.display_timestamp) : new Date(event.received_at);
}

function eventSort(a, b) {
  return shiftedDate(a) - shiftedDate(b) || new Date(a.received_at) - new Date(b.received_at) || a.sequence - b.sequence || a.id.localeCompare(b.id);
}

function arrivalSort(a, b) {
  return new Date(a.received_at) - new Date(b.received_at) || a.sequence - b.sequence || a.id.localeCompare(b.id);
}

function trimSourceWindow(list, sourceId) {
  const limit = sourceById(sourceId)?.history_events;
  if (!Number.isInteger(limit) || limit < 1) return;
  const sourceEvents = list
    .map((event, index) => ({event, index}))
    .filter(item => item.event.source_id === sourceId);
  const excess = sourceEvents.length - limit;
  if (excess <= 0) return;
  const indexes = sourceEvents
    .sort((left, right) => arrivalSort(left.event, right.event))
    .slice(0, excess)
    .map(item => item.index)
    .sort((a, b) => b - a);
  indexes.forEach(index => list.splice(index, 1));
}

function insertSorted(list, event) {
  let low = 0, high = list.length;
  while (low < high) {
    const mid = (low + high) >> 1;
    if (eventSort(list[mid], event) <= 0) low = mid + 1; else high = mid;
  }
  list.splice(low, 0, event);
  trimSourceWindow(list, event.source_id);
}

function buildQuickFilter(panel) {
  const root = document.querySelector(`[data-panel-id="${panel.id}"]`);
  if (!root) return panel.filter || {logic:"AND", negate:false, conditions:[], groups:[]};
  const text = $(".quick-filter", root).value.trim();
  const regex = $(".quick-regex", root).checked;
  const exclude = $(".quick-exclude", root).checked;
  const level = $(".quick-level", root).value;
  const conditions = [...(panel.filter?.conditions || []).filter(c => !c._quick)];
  if (text) conditions.push({field:"message", operator: regex ? (exclude ? "not_regex" : "regex") : (exclude ? "not_contains" : "contains"), value:text, case_sensitive:false, _quick:true});
  if (level) conditions.push({field:"level", operator:"equals", value:level, case_sensitive:false, _quick:true});
  return {logic: panel.filter?.logic || "AND", negate: panel.filter?.negate || false, conditions: conditions.map(({_quick, ...c}) => c), groups:panel.filter?.groups || []};
}

function clientMatches(event, filter) {
  const nested = (object, path) => (path || "").split(".").filter(Boolean).reduce((value, key) => value && typeof value === "object" ? value[key] : undefined, object);
  const evaluate = condition => {
    let actual;
    if (condition.field === "message") actual = event.message;
    else if (condition.field === "level") actual = event.level || "";
    else if (condition.field === "source") actual = event.source_id;
    else if (condition.field === "json") actual = nested(event.fields || {}, condition.json_path);
    else if (condition.field === "timestamp") actual = event.display_timestamp;
    else actual = "";
    const rawExpected = condition.value ?? "";
    if (["gte", "lte"].includes(condition.operator)) {
      const left = condition.field === "timestamp" ? new Date(actual).getTime() : Number(actual);
      const right = condition.field === "timestamp" ? new Date(rawExpected).getTime() : Number(rawExpected);
      if (!Number.isFinite(left) || !Number.isFinite(right)) return false;
      return condition.operator === "gte" ? left >= right : left <= right;
    }
    let left = String(actual ?? ""), right = String(rawExpected);
    if (!condition.case_sensitive) { left = left.toLowerCase(); right = right.toLowerCase(); }
    try {
      if (condition.operator === "contains") return left.includes(right);
      if (condition.operator === "not_contains") return !left.includes(right);
      if (condition.operator === "equals") return left === right;
      if (condition.operator === "not_equals") return left !== right;
      if (condition.operator === "regex") return new RegExp(String(rawExpected), condition.case_sensitive ? "" : "i").test(String(actual ?? ""));
      if (condition.operator === "not_regex") return !new RegExp(String(rawExpected), condition.case_sensitive ? "" : "i").test(String(actual ?? ""));
    } catch (_) { return false; }
    return true;
  };
  const values = (filter.conditions || []).map(evaluate).concat((filter.groups || []).map(group => clientMatches(event, group)));
  let result = values.length === 0 ? true : filter.logic === "OR" ? values.some(Boolean) : values.every(Boolean);
  return filter.negate ? !result : result;
}

async function bootstrap() {
  state.config = await api("/api/config");
  renderWorkspaces();
  renderSources();
  renderPanels();
  await refreshStatuses();
  connectWebSocket();
  clearInterval(state.statusTimer);
  state.statusTimer = setInterval(refreshStatuses, 1500);
  renderIncidentSettings();
}

function showSection(section) {
  state.activeSection = section;
  const showingIncidents = section === "incidents";
  $("#logs-view").classList.toggle("hidden", showingIncidents);
  $("#incidents-view").classList.toggle("hidden", !showingIncidents);
  $("#show-logs").classList.toggle("active", !showingIncidents);
  $("#show-incidents").classList.toggle("active", showingIncidents);
  $$(".logs-only").forEach(node => node.classList.toggle("hidden", showingIncidents));
  clearInterval(state.incidentTimer);
  state.incidentTimer = null;
  if (showingIncidents) {
    renderIncidentSettings();
    loadIncidents();
    state.incidentTimer = setInterval(loadIncidents, 2000);
  }
}

function renderIncidentSettings() {
  const settings = state.config?.incident_settings;
  if (!settings) return;
  $("#incident-retention").value = settings.retention_hours;
  const root = $("#incident-keywords");
  root.innerHTML = settings.keywords.length
    ? settings.keywords.map(keyword => `<span class="keyword-chip">${escapeHtml(keyword)}<button type="button" data-keyword="${escapeHtml(keyword)}" aria-label="Remove ${escapeHtml(keyword)}">×</button></span>`).join("")
    : '<span class="hint">No additional keywords configured.</span>';
}

async function loadIncidents() {
  if (state.incidentsLoading || state.activeSection !== "incidents") return;
  state.incidentsLoading = true;
  try {
    state.incidents = await api(`/api/incidents?workspace_id=${encodeURIComponent(activeWorkspace().id)}`);
    renderIncidents();
  } catch (error) {
    toast(`Could not load incidents: ${error.message}`, "error");
  } finally {
    state.incidentsLoading = false;
  }
}

function renderIncidents() {
  const workspace = activeWorkspace();
  const totalOccurrences = state.incidents.reduce((sum, incident) => sum + incident.count, 0);
  $("#incident-summary").textContent = `${state.incidents.length} incident groups · ${totalOccurrences} occurrences`;
  $("#incident-workspace-name").textContent = `Workspace: ${workspace.name}`;
  const root = $("#incident-list");
  if (!workspace.panels.some(panel => panel.source_ids.length)) {
    root.innerHTML = '<div class="empty-state"><h2>No sources in this workspace</h2><p>Add sources to a panel to see their incidents here.</p></div>';
    return;
  }
  if (!state.incidents.length) {
    root.innerHTML = '<div class="empty-state"><h2>No active incidents</h2><p>No matching events were recorded during the retention period.</p></div>';
    return;
  }
  root.innerHTML = state.incidents.map(incident => {
    const source = sourceById(incident.source_id);
    const color = source?.color || "#999999";
    const reason = incident.match_kind === "error" ? "ERROR" : `Keyword: ${incident.matched_keyword || "—"}`;
    return `<article class="incident-card" style="--incident-color:${color}">
      <div class="incident-card-head">
        <span class="incident-source" title="${escapeHtml(source?.path || incident.source_id)}">${escapeHtml(incident.source_name)}</span>
        <span class="incident-reason ${incident.match_kind}">${escapeHtml(reason)}</span>
        <strong class="incident-count" title="Occurrences in this group">×${incident.count}</strong>
      </div>
      <pre class="incident-message">${escapeHtml(incident.message)}</pre>
      <div class="incident-times"><span>First: ${formatTimestamp(incident.first_seen)}</span><span>Last: ${formatTimestamp(incident.last_seen)}</span></div>
    </article>`;
  }).join("");
}

async function saveIncidentSettings(settings) {
  state.config.incident_settings = await api("/api/incident-settings", {
    method: "PUT",
    body: JSON.stringify(settings),
  });
  renderIncidentSettings();
  await loadIncidents();
}

function renderWorkspaces() {
  const select = $("#workspace-select");
  select.innerHTML = state.config.workspaces.map(w => `<option value="${escapeHtml(w.id)}" ${w.id === state.config.active_workspace_id ? "selected" : ""}>${escapeHtml(w.name)}</option>`).join("");
  $("#grid-columns").value = activeWorkspace().grid_columns;
}

function renderSources() {
  const root = $("#source-list");
  if (!state.config.sources.length) {
    root.innerHTML = '<div class="hint">Add a local file or an SSH/SFTP source.</div>';
    return;
  }
  root.innerHTML = state.config.sources.map(source => {
    const status = state.statuses.get(source.id) || {state: source.enabled ? "starting" : "stopped", detail:""};
    const location = source.kind === "ssh" ? `${source.ssh.username}@${source.ssh.host}:${source.path}` : source.path;
    return `<div class="source-card status-${status.state}" style="--source-color:${source.color}" data-source-id="${source.id}">
      <div class="source-head"><span class="source-name" title="${escapeHtml(source.name)}">${escapeHtml(source.name)}</span><div class="source-actions"><button data-action="edit-source">Edit</button><button data-action="toggle-source">${source.enabled ? "Stop" : "Start"}</button><button class="danger" data-action="delete-source">×</button></div></div>
      <div class="source-path" title="${escapeHtml(location)}">${escapeHtml(location)}</div>
      <div class="source-status status-${status.state}" title="${escapeHtml(status.detail || "")}">${status.state}${status.detail ? ` — ${escapeHtml(status.detail)}` : ""}</div>
    </div>`;
  }).join("");
}

function renderPanels() {
  const workspace = activeWorkspace();
  const grid = $("#panel-grid");
  grid.style.setProperty("--columns", workspace.grid_columns);
  if (!workspace.panels.length) {
    grid.innerHTML = '<div class="empty-state"><h2>No panels yet</h2><p>Add a panel and select one or more log sources.</p></div>';
    return;
  }
  grid.innerHTML = "";
  [...workspace.panels].sort((a,b) => a.order-b.order).forEach(panel => grid.append(createPanel(panel)));
}

function createPanel(panel) {
  const node = document.createElement("section");
  node.className = `log-panel ${panel.wrap_lines ? "wrap" : ""}`;
  node.dataset.panelId = panel.id;
  node.draggable = true;
  node.style.setProperty("--panel-height", `${panel.min_height || 420}px`);
  const sources = panel.source_ids.map(sourceById).filter(Boolean);
  const legend = sources.map(s => `<span class="legend-dot" style="background:${s.color}" title="${escapeHtml(s.name)} — ${escapeHtml(s.path)}"></span>`).join("");
  node.innerHTML = `<div class="panel-head">
      <span class="panel-title">${escapeHtml(panel.title)}</span><span class="panel-legend">${legend}</span>
      <div class="panel-actions"><button data-action="pause">${panel.paused ? "Resume" : "Pause"}</button><button data-action="autoscroll">${panel.autoscroll === false ? "Auto off" : "Auto on"}</button><button data-action="latest">↓</button><button data-action="wrap">Wrap</button><button data-action="aggregate">Stats</button><button data-action="advanced-filter">Filter</button><button data-action="fullscreen">⛶</button><button class="danger" data-action="remove-panel">×</button></div>
    </div>
    <div class="panel-filter"><input class="quick-filter" placeholder="grep text or regex"><label class="inline"><input class="quick-regex" type="checkbox"> Regex</label><label class="inline"><input class="quick-exclude" type="checkbox"> Exclude</label><select class="quick-level"><option value="">All levels</option><option>TRACE</option><option>DEBUG</option><option>INFO</option><option>WARNING</option><option>ERROR</option><option>CRITICAL</option><option>FATAL</option></select><button data-action="reload">Apply</button></div>
    <div class="panel-banner hidden"></div>
    <div class="log-scroll"></div>
    <div class="panel-foot"><span class="event-count">0 events</span><span>${sources.length > 1 ? "Combined timeline" : "Single source"}</span></div>`;
  node.addEventListener("dragstart", () => node.classList.add("dragging"));
  node.addEventListener("dragend", () => node.classList.remove("dragging"));
  node.addEventListener("mouseup", () => {
    panel.min_height = Math.round(node.getBoundingClientRect().height);
    scheduleWorkspaceSave();
  });
  setTimeout(() => loadPanel(panel), 0);
  return node;
}

async function loadPanel(panel) {
  if (!panel.source_ids.length) return true;
  const root = document.querySelector(`[data-panel-id="${panel.id}"]`);
  try {
    const filter = buildQuickFilter(panel);
    const includeWithoutTimestamp = panel.source_ids.length === 1;
    const batches = await Promise.all(panel.source_ids.map(sourceId => {
      const limit = sourceById(sourceId)?.history_events || 1;
      return api("/api/query", {method:"POST", body:JSON.stringify({source_ids:[sourceId], filter, limit, include_without_timestamp:includeWithoutTimestamp})});
    }));
    const events = batches.flat();
    state.panelEvents.set(panel.id, events.sort(eventSort));
    renderPanelEvents(panel, root);
    return true;
  } catch (error) { toast(`Panel query failed: ${error.message}`, "error"); return false; }
}

function renderPanelEvents(panel, root=document.querySelector(`[data-panel-id="${panel.id}"]`)) {
  if (!root) return;
  const scroll = $(".log-scroll", root);
  const wasAtBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 30;
  const filter = buildQuickFilter(panel);
  const events = (state.panelEvents.get(panel.id) || []).filter(event => clientMatches(event, filter));
  scroll.innerHTML = events.slice(-3000).map(event => {
    const source = sourceById(event.source_id) || {name:event.source_id, color:"#999999", path:""};
    const time = event.display_timestamp ? formatTimestamp(event.display_timestamp) : "arrival order";
    return `<div class="log-row" style="--row-color:${source.color}" title="Source: ${escapeHtml(source.name)}\n${escapeHtml(source.path)}" data-event-id="${event.id}">
      <span class="log-time">${time}</span><span class="log-level ${event.level || ""}">${escapeHtml(event.level || "—")}</span><span class="log-message">${escapeHtml(event.message)}</span>
    </div>`;
  }).join("");
  $(".event-count", root).textContent = `${events.length} events${panel.paused ? " — paused" : ""}`;
  updatePanelBanner(panel, root);
  if ((panel.autoscroll !== false && wasAtBottom) || !scroll.dataset.initialized) { scroll.scrollTop = scroll.scrollHeight; scroll.dataset.initialized = "1"; }
}

function updatePanelBanner(panel, root) {
  const unavailable = panel.source_ids.map(id => ({source:sourceById(id), status:state.statuses.get(id)})).filter(x => x.status && ["missing","error"].includes(x.status.state));
  const banner = $(".panel-banner", root);
  if (unavailable.length) {
    banner.classList.remove("hidden");
    banner.textContent = unavailable.map(x => `${x.source?.name || x.status.source_id}: ${x.status.detail || x.status.state}`).join(" | ");
  } else banner.classList.add("hidden");
}

function connectWebSocket() {
  clearTimeout(state.reconnectTimer);
  if (state.websocket) {
    state.websocket.onclose = null;
    state.websocket.close();
    state.websocket = null;
  }
  const ids = state.config.sources.filter(s => s.enabled).map(s => s.id);
  if (!ids.length) return;
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${protocol}://${location.host}/ws?source_ids=${encodeURIComponent(ids.join(","))}`);
  state.websocket = ws;
  ws.onmessage = message => {
    const event = JSON.parse(message.data);
    for (const panel of activeWorkspace().panels) {
      if (panel.paused || !panel.source_ids.includes(event.source_id)) continue;
      if (panel.source_ids.length > 1 && !event.display_timestamp) continue;
      const list = state.panelEvents.get(panel.id) || [];
      insertSorted(list, event);
      state.panelEvents.set(panel.id, list);
      renderPanelEvents(panel);
    }
  };
  ws.onclose = () => {
    if (state.websocket !== ws) return;
    state.websocket = null;
    clearTimeout(state.reconnectTimer);
    state.reconnectTimer = setTimeout(connectWebSocket, 1500);
  };
}

async function refreshStatuses() {
  try {
    const [statuses, health] = await Promise.all([api("/api/statuses"), api("/api/health")]);
    const previous = state.statuses;
    state.statuses = new Map(statuses.map(item => [item.source_id, item]));
    for (const status of statuses) {
      const old = previous.get(status.source_id);
      if (old && old.state !== status.state && ["missing","error","running"].includes(status.state)) {
        const source = sourceById(status.source_id);
        toast(`${source?.name || status.source_id}: ${status.state}${status.detail ? ` — ${status.detail}` : ""}`, status.state === "running" ? "info" : "error");
      }
    }
    renderSources();
    for (const panel of activeWorkspace().panels) updatePanelBanner(panel, document.querySelector(`[data-panel-id="${panel.id}"]`));
    $("#buffer-usage").textContent = `${displayBytes(health.cache_bytes)} / ${displayBytes(state.config.settings.disk_buffer_bytes)}`;
    $("#buffer-progress").style.width = `${Math.min(100, health.cache_bytes/state.config.settings.disk_buffer_bytes*100)}%`;
  } catch (_) {}
}

function scheduleWorkspaceSave() {
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(saveWorkspace, 350);
}

async function saveWorkspace() {
  const workspace = activeWorkspace();
  try { await api(`/api/workspaces/${workspace.id}`, {method:"PUT", body:JSON.stringify(workspace)}); }
  catch (error) { toast(`Could not save layout: ${error.message}`, "error"); }
}

function openSourceDialog(source=null) {
  $("#source-dialog-title").textContent = source ? "Edit source" : "Add source";
  $("#source-id").value = source?.id || crypto.randomUUID().replaceAll("-", "");
  $("#source-name").value = source?.name || "";
  $("#source-kind").value = source?.kind || "local";
  $("#source-path").value = source?.path || "";
  $("#source-offset").value = source?.timezone_offset_hours ?? 0;
  $("#source-history").value = source?.history_events ?? 30;
  $("#source-color").value = source?.color || uniqueColor();
  $("#source-parser").value = source?.parser?.kind || "auto";
  $("#source-regex").value = source?.parser?.timestamp_regex || "";
  $("#source-format").value = source?.parser?.timestamp_format || "";
  $("#ssh-host").value = source?.ssh?.host || "";
  $("#ssh-port").value = source?.ssh?.port || 22;
  $("#ssh-user").value = source?.ssh?.username || "";
  $("#ssh-key").value = source?.ssh?.key_path || "";
  toggleSshFields();
  $("#source-dialog").showModal();
}

function sourceFromForm(existing=null) {
  const kind = $("#source-kind").value;
  return {
    id:$("#source-id").value, name:$("#source-name").value.trim(), kind,
    path:$("#source-path").value.trim(), color:$("#source-color").value,
    timezone_offset_hours:Number($("#source-offset").value), history_events:Number($("#source-history").value), poll_interval_ms:existing?.poll_interval_ms ?? 500, enabled:existing?.enabled ?? true,
    parser:{kind:$("#source-parser").value, timestamp_regex:$("#source-regex").value.trim() || null, timestamp_format:$("#source-format").value.trim() || null, timestamp_fields:existing?.parser?.timestamp_fields || ["timestamp","time","datetime","date","@timestamp"], level_fields:existing?.parser?.level_fields || ["level","severity","loglevel"]},
    ssh: kind === "ssh" ? {host:$("#ssh-host").value.trim(), port:Number($("#ssh-port").value), username:$("#ssh-user").value.trim(), key_path:$("#ssh-key").value.trim() || null} : null,
  };
}

function uniqueColor() {
  const palette = ["#6ea8fe","#67d5b5","#f7c45c","#e58adf","#ff8a76","#80c7ff","#b7dc6f","#c6a0ff","#f09a54","#68d8e8","#ff7faa","#9de26e"];
  const used = new Set(state.config.sources.map(s => s.color));
  return palette.find(c => !used.has(c)) || `#${Math.floor(Math.random()*0xffffff).toString(16).padStart(6,"0")}`;
}

function openPanelDialog() {
  $("#panel-title").value = "Log panel";
  $("#panel-source-options").innerHTML = state.config.sources.map(s => `<label><input type="checkbox" value="${s.id}"><span class="legend-dot" style="background:${s.color}"></span>${escapeHtml(s.name)}</label>`).join("") || '<div class="hint">Add a source first.</div>';
  $("#panel-dialog").showModal();
}

function addConditionRow(condition={field:"message",operator:"contains",value:"",case_sensitive:false,json_path:null}) {
  const row = $("#condition-template").content.firstElementChild.cloneNode(true);
  $(".condition-field", row).value = condition.field;
  $(".condition-operator", row).value = condition.operator;
  $(".condition-value", row).value = condition.value ?? "";
  $(".condition-case", row).checked = condition.case_sensitive;
  $(".condition-path", row).value = condition.json_path || "";
  $(".condition-path", row).classList.toggle("hidden", condition.field !== "json");
  $(".condition-field", row).onchange = e => $(".condition-path", row).classList.toggle("hidden", e.target.value !== "json");
  $(".remove-condition", row).onclick = () => row.remove();
  $("#condition-list").append(row);
}

function openFilterDialog(panel) {
  $("#saved-filter-select").innerHTML = '<option value="">Saved filters…</option>' + (state.config.saved_filters || []).map(item => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join("");
  $("#saved-filter-name").value = "";
  $("#filter-panel-id").value = panel.id;
  $("#filter-logic").value = panel.filter?.logic || "AND";
  $("#filter-negate").checked = panel.filter?.negate || false;
  $("#condition-list").innerHTML = "";
  (panel.filter?.conditions || []).forEach(addConditionRow);
  if (!(panel.filter?.conditions || []).length) addConditionRow();
  $("#filter-dialog").showModal();
}

async function showAggregations(panel) {
  const dialog = $("#aggregation-dialog");
  dialog.dataset.panelId = panel.id;
  $("#aggregation-content").innerHTML = "Loading…";
  dialog.showModal();
  try {
    const data = await api("/api/aggregations", {method:"POST", body:JSON.stringify({source_ids:panel.source_ids, filter:buildQuickFilter(panel), apply_filter:panel.aggregation_after_filter, interval:"minute", top_n:10})});
    const levels = Object.entries(data.by_level).map(([key,value]) => `<tr><td>${escapeHtml(key)}</td><td>${value}</td></tr>`).join("");
    const sources = Object.entries(data.by_source).map(([key,value]) => `<tr><td>${escapeHtml(sourceById(key)?.name || key)}</td><td>${value}</td></tr>`).join("");
    const errors = data.top_errors.map(item => `<tr><td title="${escapeHtml(item.message)}">${escapeHtml(item.message.slice(0,100))}</td><td>${item.count}</td></tr>`).join("");
    const repeated = data.top_normalized.map(item => `<tr><td title="${escapeHtml(item.message)}">${escapeHtml(item.message.slice(0,100))}</td><td>${item.count}</td><td>${item.first ? formatTimestamp(item.first) : "—"}</td><td>${item.last ? formatTimestamp(item.last) : "—"}</td></tr>`).join("");
    $("#aggregation-content").innerHTML = `<label class="inline"><input type="checkbox" id="aggregation-after-filter" ${panel.aggregation_after_filter ? "checked" : ""}> Calculate after active filters</label><div class="metrics"><div class="metric"><span>Total events</span><strong>${data.total}</strong></div><div class="metric"><span>Error patterns</span><strong>${data.top_errors.length}</strong></div><div class="metric"><span>Sources</span><strong>${Object.keys(data.by_source).length}</strong></div><div class="metric"><span>Time buckets</span><strong>${Object.keys(data.timeline).length}</strong></div></div><div class="agg-grid"><div><h3>Levels</h3><table class="agg-table">${levels}</table></div><div><h3>Sources</h3><table class="agg-table">${sources}</table></div><div><h3>Top warnings/errors</h3><table class="agg-table">${errors}</table></div><div><h3>Repeated normalized messages</h3><table class="agg-table"><tr><th>Pattern</th><th>Count</th><th>First</th><th>Last</th></tr>${repeated}</table></div></div>`;
  } catch (error) { $("#aggregation-content").textContent = error.message; }
}

function formatTimestamp(value) {
  const date = new Date(value);
  const p = (n, size=2) => String(n).padStart(size,"0");
  return `${date.getUTCFullYear()}-${p(date.getUTCMonth()+1)}-${p(date.getUTCDate())} ${p(date.getUTCHours())}:${p(date.getUTCMinutes())}:${p(date.getUTCSeconds())}.${p(date.getUTCMilliseconds(),3)}`;
}

function escapeHtml(value) { const div=document.createElement("div"); div.textContent=String(value ?? ""); return div.innerHTML; }

$("#source-kind").addEventListener("change", toggleSshFields);
function toggleSshFields() { $("#ssh-fields").classList.toggle("hidden", $("#source-kind").value !== "ssh"); }

$("#add-source").onclick = () => openSourceDialog();
$("#source-form").addEventListener("submit", async event => {
  event.preventDefault();
  const existing = state.config.sources.find(s => s.id === $("#source-id").value);
  const source = sourceFromForm(existing);
  try {
    const saved = await api(existing ? `/api/sources/${source.id}` : "/api/sources", {method:existing ? "PUT" : "POST", body:JSON.stringify(source)});
    if (existing) Object.assign(existing, saved); else state.config.sources.push(saved);
    $("#source-dialog").close(); renderSources(); renderPanels(); connectWebSocket();
  } catch (error) { toast(error.message, "error"); }
});

$("#source-list").addEventListener("click", async event => {
  const button = event.target.closest("button"); if (!button) return;
  const card = button.closest(".source-card"), source = sourceById(card.dataset.sourceId);
  if (button.dataset.action === "edit-source") openSourceDialog(source);
  if (button.dataset.action === "toggle-source") {
    try { await api(`/api/sources/${source.id}/${source.enabled ? "stop" : "start"}`, {method:"POST"}); source.enabled=!source.enabled; renderSources(); connectWebSocket(); }
    catch (error) { toast(error.message,"error"); }
  }
  if (button.dataset.action === "delete-source" && confirm(`Remove source '${source.name}'? The log file will not be changed.`)) {
    try { await api(`/api/sources/${source.id}`, {method:"DELETE"}); state.config.sources=state.config.sources.filter(s=>s.id!==source.id); state.config.workspaces.forEach(workspace=>workspace.panels.forEach(panel=>panel.source_ids=panel.source_ids.filter(id=>id!==source.id))); renderSources(); renderPanels(); connectWebSocket(); }
    catch (error) { toast(error.message,"error"); }
  }
});

$("#add-panel").onclick = openPanelDialog;
$("#panel-form").addEventListener("submit", event => {
  event.preventDefault(); const ids=$$("#panel-source-options input:checked").map(x=>x.value);
  if (!ids.length) { toast("Select at least one source", "error"); return; }
  const workspace=activeWorkspace(); workspace.panels.push({id:crypto.randomUUID().replaceAll("-",""),title:$("#panel-title").value||"Log panel",source_ids:ids,filter:{logic:"AND",negate:false,conditions:[],groups:[]},aggregation_after_filter:true,paused:false,stopped:false,wrap_lines:false,autoscroll:true,order:workspace.panels.length,min_height:420});
  $("#panel-dialog").close(); renderPanels(); scheduleWorkspaceSave();
});

$("#panel-grid").addEventListener("click", async event => {
  const button=event.target.closest("button"); if(!button) return;
  const node=button.closest(".log-panel"); if(!node) return;
  const panel=activeWorkspace().panels.find(p=>p.id===node.dataset.panelId); const action=button.dataset.action;
  if(action==="pause"){panel.paused=!panel.paused; button.textContent=panel.paused?"Resume":"Pause"; renderPanelEvents(panel,node); scheduleWorkspaceSave();}
  if(action==="autoscroll"){panel.autoscroll=panel.autoscroll===false; button.textContent=panel.autoscroll?"Auto on":"Auto off"; scheduleWorkspaceSave();}
  if(action==="latest"){const scroll=$(".log-scroll",node);scroll.scrollTop=scroll.scrollHeight;}
  if(action==="wrap"){panel.wrap_lines=!panel.wrap_lines; node.classList.toggle("wrap",panel.wrap_lines); scheduleWorkspaceSave();}
  if(action==="reload") loadPanel(panel);
  if(action==="advanced-filter") openFilterDialog(panel);
  if(action==="aggregate") showAggregations(panel);
  if(action==="fullscreen") node.classList.toggle("fullscreen");
  if(action==="remove-panel"){activeWorkspace().panels=activeWorkspace().panels.filter(p=>p.id!==panel.id); state.panelEvents.delete(panel.id); renderPanels(); scheduleWorkspaceSave();}
});

$("#panel-grid").addEventListener("dblclick", async event => {
  const row = event.target.closest(".log-row"); if (!row) return;
  const panelNode = row.closest(".log-panel");
  const item = (state.panelEvents.get(panelNode.dataset.panelId) || []).find(value => value.id === row.dataset.eventId);
  if (!item) return;
  try { await navigator.clipboard.writeText(item.raw || item.message); toast("Log event copied"); }
  catch (_) { toast("Clipboard access failed", "error"); }
});

$("#panel-grid").addEventListener("click", event => {
  const row = event.target.closest(".log-row"); if (!row) return;
  $$(".log-row.selected-row", row.closest(".log-scroll")).forEach(item => item.classList.remove("selected-row"));
  row.classList.add("selected-row");
});

$("#panel-grid").addEventListener("dragover", event => {
  event.preventDefault(); const dragging=$(".log-panel.dragging"); if(!dragging) return;
  const target=event.target.closest(".log-panel"); if(target && target!==dragging) { const rect=target.getBoundingClientRect(); target.parentNode.insertBefore(dragging, event.clientY < rect.top+rect.height/2 ? target : target.nextSibling); }
});
$("#panel-grid").addEventListener("drop", () => { $$(".log-panel").forEach((node,index)=>{const panel=activeWorkspace().panels.find(p=>p.id===node.dataset.panelId); panel.order=index;}); scheduleWorkspaceSave(); });

$("#add-condition").onclick = () => addConditionRow();
$("#filter-form").addEventListener("submit", async event => {
  event.preventDefault(); const panel=activeWorkspace().panels.find(p=>p.id===$("#filter-panel-id").value);
  const original=panel.filter;
  panel.filter={logic:$("#filter-logic").value,negate:$("#filter-negate").checked,conditions:$$('#condition-list .condition-row').map(row=>({field:$(".condition-field",row).value,operator:$(".condition-operator",row).value,value:$(".condition-value",row).value,json_path:$(".condition-path",row).value||null,case_sensitive:$(".condition-case",row).checked})),groups:[]};
  if (await loadPanel(panel)) { $("#filter-dialog").close(); scheduleWorkspaceSave(); }
  else panel.filter=original;
});

$(".dialog-cancel") && $$(".dialog-cancel").forEach(button => button.onclick = () => button.closest("dialog").close());

$("#load-saved-filter").onclick = () => {
  const saved = (state.config.saved_filters || []).find(item => item.id === $("#saved-filter-select").value);
  if (!saved) return;
  $("#filter-logic").value = saved.filter.logic;
  $("#filter-negate").checked = saved.filter.negate;
  $("#condition-list").innerHTML = "";
  (saved.filter.conditions || []).forEach(addConditionRow);
};

$("#save-named-filter").onclick = async () => {
  const name = $("#saved-filter-name").value.trim();
  if (!name) { toast("Enter a filter name", "error"); return; }
  const filter = {logic:$("#filter-logic").value,negate:$("#filter-negate").checked,conditions:$$('#condition-list .condition-row').map(row=>({field:$(".condition-field",row).value,operator:$(".condition-operator",row).value,value:$(".condition-value",row).value,json_path:$(".condition-path",row).value||null,case_sensitive:$(".condition-case",row).checked})),groups:[]};
  const saved = {id:crypto.randomUUID().replaceAll("-",""),name,filter};
  try { const result=await api("/api/filters",{method:"POST",body:JSON.stringify(saved)});state.config.saved_filters.push(result);$("#saved-filter-select").insertAdjacentHTML("beforeend",`<option value="${result.id}">${escapeHtml(result.name)}</option>`);$("#saved-filter-select").value=result.id;toast("Filter saved"); }
  catch(error){toast(error.message,"error");}
};

$("#open-settings").onclick = () => {
  $("#settings-buffer").value = (state.config.settings.disk_buffer_bytes / 1024**3).toFixed(1);
  $("#settings-sort-delay").value = state.config.settings.sort_buffer_seconds;
  $("#settings-browser").checked = state.config.settings.open_browser;
  $("#settings-dialog").showModal();
};
$("#settings-form").addEventListener("submit", async event => {
  event.preventDefault();
  const settings = {...state.config.settings,disk_buffer_bytes:Math.round(Number($("#settings-buffer").value)*1024**3),sort_buffer_seconds:Number($("#settings-sort-delay").value),open_browser:$("#settings-browser").checked};
  try { state.config.settings=await api("/api/settings",{method:"PUT",body:JSON.stringify(settings)});$("#settings-dialog").close();toast("Settings saved"); }
  catch(error){toast(error.message,"error");}
});

$("#aggregation-dialog").addEventListener("change", event => {
  if (event.target.id !== "aggregation-after-filter") return;
  const visiblePanel = activeWorkspace().panels.find(panel => panel.id === event.currentTarget.dataset.panelId);
  if (visiblePanel) { visiblePanel.aggregation_after_filter=event.target.checked; scheduleWorkspaceSave(); }
});

$("#workspace-select").onchange = async event => { state.config.active_workspace_id=event.target.value; await api(`/api/active-workspace/${event.target.value}`,{method:"PUT"}); renderWorkspaces(); renderPanels(); if(state.activeSection==="incidents")await loadIncidents(); };
$("#grid-columns").onchange = event => { activeWorkspace().grid_columns=Number(event.target.value); renderPanels(); scheduleWorkspaceSave(); };
$("#new-workspace").onclick = async () => { const name=prompt("Workspace name"); if(!name)return; const workspace={id:crypto.randomUUID().replaceAll("-",""),name,panels:[],grid_columns:2}; try{await api("/api/workspaces",{method:"POST",body:JSON.stringify(workspace)});state.config.workspaces.push(workspace);state.config.active_workspace_id=workspace.id;await api(`/api/active-workspace/${workspace.id}`,{method:"PUT"});renderWorkspaces();renderPanels();}catch(e){toast(e.message,"error");} };
$("#duplicate-workspace").onclick = async () => { const current=activeWorkspace(),name=prompt("Copy name",`${current.name} copy`);if(!name)return;const copy=structuredClone(current);copy.id=crypto.randomUUID().replaceAll("-","");copy.name=name;copy.panels.forEach(p=>p.id=crypto.randomUUID().replaceAll("-",""));try{await api("/api/workspaces",{method:"POST",body:JSON.stringify(copy)});state.config.workspaces.push(copy);renderWorkspaces();}catch(e){toast(e.message,"error");} };
$("#delete-workspace").onclick = async () => { const w=activeWorkspace();if(w.id==="default"){toast("The default workspace cannot be deleted","error");return;}if(!confirm(`Delete workspace '${w.name}'?`))return;try{await api(`/api/workspaces/${w.id}`,{method:"DELETE"});state.config.workspaces=state.config.workspaces.filter(x=>x.id!==w.id);state.config.active_workspace_id="default";renderWorkspaces();renderPanels();}catch(e){toast(e.message,"error");} };

$("#show-logs").onclick = () => showSection("logs");
$("#show-incidents").onclick = () => showSection("incidents");
$("#refresh-incidents").onclick = loadIncidents;
$("#incident-retention-form").addEventListener("submit", async event => {
  event.preventDefault();
  const settings = {...state.config.incident_settings, retention_hours:Number($("#incident-retention").value)};
  try { await saveIncidentSettings(settings); toast("Incident retention saved"); }
  catch (error) { toast(error.message, "error"); }
});
$("#incident-keyword-form").addEventListener("submit", async event => {
  event.preventDefault();
  const keyword = $("#incident-keyword").value.trim();
  if (!keyword) return;
  if (state.config.incident_settings.keywords.some(item => item.toLowerCase() === keyword.toLowerCase())) {
    toast("This keyword already exists", "error");
    return;
  }
  const settings = {...state.config.incident_settings, keywords:[...state.config.incident_settings.keywords, keyword]};
  try { await saveIncidentSettings(settings); $("#incident-keyword").value=""; toast("Incident keyword added"); }
  catch (error) { toast(error.message, "error"); }
});
$("#incident-keywords").addEventListener("click", async event => {
  const button = event.target.closest("button[data-keyword]");
  if (!button) return;
  const settings = {...state.config.incident_settings, keywords:state.config.incident_settings.keywords.filter(item => item !== button.dataset.keyword)};
  try { await saveIncidentSettings(settings); toast("Incident keyword removed"); }
  catch (error) { toast(error.message, "error"); }
});

bootstrap().catch(error => toast(`Startup failed: ${error.message}`, "error"));
