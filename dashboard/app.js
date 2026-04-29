// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  initSizeSwitcher();

  fetch("/api/state")
    .then((r) => { if (!r.ok) throw new Error(); return r.json(); })
    .then(render)
    .catch(() => setConn(false));

  connectSSE();
});

function initSizeSwitcher() {
  const saved = localStorage.getItem("dash-size") || "m";
  applySize(saved);
  document.querySelectorAll(".size-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      applySize(btn.dataset.size);
      localStorage.setItem("dash-size", btn.dataset.size);
    });
  });
}

function applySize(size) {
  document.documentElement.setAttribute("data-size", size);
  document.querySelectorAll(".size-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.size === size);
  });
}

function connectSSE() {
  const es = new EventSource("/api/events");
  es.onopen = () => setConn(true);
  es.onmessage = (e) => { try { render(JSON.parse(e.data)); } catch (_) {} };
  es.onerror = () => setConn(false);
}

function setConn(ok) {
  const dot = document.getElementById("connection-indicator");
  const lbl = document.getElementById("live-label");
  if (!dot) return;
  dot.className = "live-dot" + (ok ? " connected" : "");
  if (lbl) lbl.textContent = ok ? "live" : "disconnected";
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------

function render(data) {
  renderHeader(data);
  renderWorkers(data.workers || []);
  renderWorkItems(data.workItems || []);
  renderActivity(data.activity || []);
  renderProjects(data.projects || []);
  renderKnowledge(data.knowledge || []);
}

function renderHeader(data) {
  // Status badge
  const badge = document.getElementById("org-status");
  const status = (data.status || "IDLE").toUpperCase();
  badge.textContent = status;
  badge.className = "status-badge status-" + status.toLowerCase();

  // Objective
  const obj = document.getElementById("objective");
  obj.textContent = data.objective || "No active objective";

  // Timestamp
  const ts = document.getElementById("last-updated");
  if (data.updated) {
    ts.textContent = new Date(data.updated).toLocaleTimeString("ja-JP", {
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  }
}

// ---------------------------------------------------------------------------
// Workers
// ---------------------------------------------------------------------------

function renderWorkers(workers) {
  const list = document.getElementById("workers-list");
  const countEl = document.getElementById("worker-count");
  if (countEl) countEl.textContent = workers.length;

  if (workers.length === 0) {
    list.innerHTML = '<p class="empty-state">No active workers</p>';
    list.style.background = "var(--surface)";
    return;
  }

  list.style.background = "var(--border)";
  list.innerHTML = workers.map(workerCard).join("");

  // Tick elapsed counters
  clearInterval(window._tick);
  window._tick = setInterval(() => {
    workers.forEach((w) => {
      const el = document.getElementById("el-" + w.id);
      if (el) el.textContent = elapsedStr(w.started);
    });
  }, 10000);
}

function workerCard(w) {
  return `
  <div class="worker-card active">
    <div class="worker-top">
      <span class="worker-id-tag">${esc(w.shortId || w.id.slice(0, 8))}</span>
      <span class="worker-task-tag">${esc(w.task || "–")}</span>
      <span class="worker-pulse"></span>
    </div>
    <div class="worker-progress">${esc(w.lastProgress || "作業中...")}</div>
    <div class="worker-footer">
      <span id="el-${esc(w.id)}">${elapsedStr(w.started)}</span>
      ${w.paneId ? `<span>pane ${esc(w.paneId)}</span>` : ""}
    </div>
  </div>`;
}

function elapsedStr(iso) {
  if (!iso) return "";
  try {
    const m = Math.floor((Date.now() - new Date(iso)) / 60000);
    if (m < 1) return "just now";
    if (m < 60) return `${m}m`;
    const h = Math.floor(m / 60);
    return h < 24 ? `${h}h ${m % 60}m` : `${Math.floor(h / 24)}d`;
  } catch { return ""; }
}

// ---------------------------------------------------------------------------
// Work Items
// ---------------------------------------------------------------------------

const STATUS_ICON = {
  IN_PROGRESS: "🟢", COMPLETED: "✅", PENDING: "⚪",
  BLOCKED: "🔴", REVIEW: "🟡", ABANDONED: "❌",
};

function renderWorkItems(items) {
  const el = document.getElementById("work-items-list");
  if (!items.length) {
    el.innerHTML = '<p class="empty-state">No work items</p>'; return;
  }
  el.innerHTML = [...items].reverse().map((item) => `
    <div class="work-item">
      <span class="wi-icon">${STATUS_ICON[item.status] || "❓"}</span>
      <div class="wi-body">
        <div class="wi-title">
          <span class="wi-id">${esc(item.id)}</span>
          <span class="wi-label">${esc(item.title)}</span>
        </div>
        <div class="wi-meta">${esc(item.progress || item.status)}${item.worker ? ` — ${esc(item.worker)}` : ""}</div>
      </div>
    </div>
  `).join("");
}

// ---------------------------------------------------------------------------
// Activity
// ---------------------------------------------------------------------------

function renderActivity(items) {
  const el = document.getElementById("activity-list");
  if (!items.length) {
    el.innerHTML = '<p class="empty-state">No activity</p>'; return;
  }
  el.innerHTML = items.slice(0, 20).map((item) => {
    const t = item.ts
      ? new Date(item.ts).toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit" })
      : "--";
    return `
      <div class="activity-item">
        <span class="act-time">${t}</span>
        <span class="act-text">${esc(item.summary || item.event || "")}</span>
      </div>`;
  }).join("");
}

// ---------------------------------------------------------------------------
// Projects
// ---------------------------------------------------------------------------

function renderProjects(projects) {
  const el = document.getElementById("projects-list");
  if (!projects.length) {
    el.innerHTML = '<p class="empty-state">No projects</p>'; return;
  }
  el.innerHTML = projects.map((p) => `
    <div class="project-card">
      <div class="project-name">${esc(p.name)}</div>
      ${p.description ? `<div class="project-desc">${esc(p.description)}</div>` : ""}
      ${p.tasks?.length ? `<div class="project-tasks">${p.tasks.map((t) => `<span>${esc(t)}</span>`).join("")}</div>` : ""}
    </div>
  `).join("");
}

// ---------------------------------------------------------------------------
// Knowledge
// ---------------------------------------------------------------------------

function renderKnowledge(items) {
  const el = document.getElementById("knowledge-list");
  if (!items.length) {
    el.innerHTML = '<p class="empty-state">No knowledge yet</p>'; return;
  }
  const total = items.reduce((s, k) => s + (k.count || 0), 0);
  el.innerHTML = `
    <div class="knowledge-meta">${items.length} THEMES · ${total} ENTRIES</div>
    <div class="knowledge-items">
      ${items.map((k) => `
        <span class="knowledge-chip">
          ${esc(k.theme)}<span class="k-count">${k.count || 0}</span>
        </span>`).join("")}
    </div>`;
}

// ---------------------------------------------------------------------------
// Utils
// ---------------------------------------------------------------------------

function esc(str) {
  const el = document.createElement("span");
  el.textContent = String(str ?? "");
  return el.innerHTML;
}
