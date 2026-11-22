const listEl = document.getElementById("list");
const detailEl = document.getElementById("detail");
const healthEl = document.getElementById("health");
const wsEl = document.getElementById("ws");
let selected = null;
let rows = [];

async function refreshHealth() {
  try {
    const h = await fetch("/api/health").then((r) => r.json());
    healthEl.textContent = `${h.provider} · ${h.stats.summaries} summaries`;
  } catch {
    healthEl.textContent = "api down";
  }
}

async function refreshList() {
  rows = await fetch("/api/summaries").then((r) => r.json());
  listEl.innerHTML = "";
  if (!rows.length) {
    listEl.innerHTML = "<li class='meta-row'>No filings yet. Watcher is polling NSE/BSE…</li>";
    return;
  }
  for (const row of rows) {
    const li = document.createElement("li");
    li.dataset.id = row.id;
    if (String(selected) === String(row.id)) li.classList.add("active");
    li.innerHTML = `
      <span class="sym">${row.nse_symbol || "—"} · ${row.source}</span>
      <span class="co">${row.company}</span>
      <div class="meta-row">${row.quarter || row.kind} · ${row.status} · ${(row.tldr && row.tldr[0]) ? row.tldr[0].slice(0, 90) : row.title || ""}</div>
    `;
    li.onclick = () => openFiling(row.id);
    listEl.appendChild(li);
  }
}

function mdList(items) {
  if (!items || !items.length) return "<p class='empty'>None extracted.</p>";
  return `<ul class="facts">${items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
}

function esc(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

async function openFiling(id) {
  selected = id;
  [...listEl.children].forEach((el) => el.classList.toggle("active", el.dataset.id === String(id)));
  const row = await fetch(`/api/summaries/${id}`).then((r) => r.json());
  const s = row.summary || {};
  const segs = (s.segment_performance || [])
    .map((x) => `<li><strong>${esc(x.segment)}:</strong> ${esc(x.notes)}</li>`)
    .join("");
  const metrics = (s.key_metrics || [])
    .map((x) => `<li><strong>${esc(x.metric)}:</strong> ${esc(x.value)} ${esc(x.context || "")}</li>`)
    .join("");
  const qa = (s.notable_qa || [])
    .map(
      (x) => `<div class="qa"><div class="q"><strong>Q${x.asked_by ? " (" + esc(x.asked_by) + ")" : ""}:</strong> ${esc(x.question)}</div><div class="a"><strong>A:</strong> ${esc(x.answer)}</div></div>`
    )
    .join("");
  const tone = s.tone
    ? `<div class="tone">tone ${esc(s.tone.label)} (${s.tone.score})</div>`
    : "";
  detailEl.innerHTML = `
    <div class="kicker">${esc(row.nse_symbol)} · scrip ${esc(row.scrip_code)} · ${esc(row.source)} · ${esc(row.announced_at || "")}</div>
    <h1>${esc(row.company)}</h1>
    ${tone}
    <p class="meta-row">${esc(row.title || "")}${row.source_url ? ` · <a href="${esc(row.source_url)}" style="color:var(--accent)">filing</a>` : ""}</p>
    <h2>TL;DR</h2>
    ${mdList(s.tldr)}
    <h2>Management commentary</h2>
    ${mdList(s.management_commentary)}
    <h2>Guidance / outlook</h2>
    ${mdList(s.guidance)}
    <h2>Segments</h2>
    ${segs ? `<ul class="facts">${segs}</ul>` : "<p class='empty'>None extracted.</p>"}
    <h2>Key numbers</h2>
    ${metrics ? `<ul class="facts">${metrics}</ul>` : "<p class='empty'>None extracted.</p>"}
    <h2>Notable Q&amp;A</h2>
    ${qa || "<p class='empty'>None extracted.</p>"}
    <h2>Risks</h2>
    ${mdList(s.risks)}
  `;
}

function connectWs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => {
    wsEl.textContent = "WS live";
    wsEl.classList.add("on");
    wsEl.classList.remove("off");
  };
  ws.onclose = () => {
    wsEl.textContent = "WS idle";
    wsEl.classList.add("off");
    wsEl.classList.remove("on");
    setTimeout(connectWs, 3000);
  };
  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === "summary" || msg.type === "audio_only") {
        refreshList().then(() => {
          if (msg.filing_id) openFiling(msg.filing_id);
        });
      }
    } catch {
      /* ignore */
    }
  };
}

document.getElementById("backfill").onclick = async () => {
  document.getElementById("backfill").disabled = true;
  try {
    await fetch("/api/backfill", { method: "POST" });
    await refreshList();
    await refreshHealth();
  } finally {
    document.getElementById("backfill").disabled = false;
  }
};

refreshHealth();
refreshList();
connectWs();
setInterval(refreshHealth, 20000);
setInterval(refreshList, 25000);
