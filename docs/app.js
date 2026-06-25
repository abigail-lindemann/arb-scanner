/* Arb scanner dashboard. Reads docs/data.json (produced by scanner_run.py).
   No secrets, no API calls, no localStorage -- all state in memory (§9.2). */
"use strict";

const FMT = {
  pct: (x) => (x == null ? "—" : (x * 100).toFixed(1) + "%"),
  usd: (x) => (x == null ? "—" : "$" + x.toFixed(2)),
  num: (x) => (x == null ? "—" : Math.round(x).toLocaleString()),
};

const state = { events: [], filtered: [], chart: null, selected: null };

function plottedKalshi(e) {
  // inverted pairs are plotted against (1 - kalshi) so the diagonal still
  // means "agreement" (§9.2).
  return e.inverted ? 1 - e.kalshi_mid : e.kalshi_mid;
}

// distance from the y=x line, normalized to [0,1] over the reachable range.
function diffScore(e) {
  return Math.abs(e.pm_mid - plottedKalshi(e)) / Math.SQRT1_2 / Math.SQRT2;
}

function pointColor(e) {
  if (e.resolution_warning) return "#ffd24a";
  const d = Math.min(1, Math.abs(e.pm_mid - plottedKalshi(e)) / 0.2);
  // green (on-line / agreement) -> hot red (far / big edge)
  const r = Math.round(0 + d * 255);
  const g = Math.round(255 - d * 165);
  return `rgba(${r},${g},${Math.round(156 - d * 120)},0.9)`;
}

async function load() {
  let data;
  try {
    const res = await fetch("data.json", { cache: "no-store" });
    data = await res.json();
  } catch (err) {
    document.getElementById("meta").textContent = "data.json not found — run the scanner once to populate it.";
    return;
  }
  state.events = data.events || [];
  const when = data.generated_at ? new Date(data.generated_at).toLocaleString() : "—";
  document.getElementById("meta").textContent = `${state.events.length} matched pairs · updated ${when}`;
  populateCategories();
  wireFilters();
  apply();
}

function populateCategories() {
  const sel = document.getElementById("f-category");
  [...new Set(state.events.map((e) => e.category))].sort().forEach((c) => {
    const o = document.createElement("option");
    o.value = c; o.textContent = c; sel.appendChild(o);
  });
}

function wireFilters() {
  ["f-category", "f-net", "f-liq", "f-conf", "f-warn"].forEach((id) =>
    document.getElementById(id).addEventListener("input", apply)
  );
}

function apply() {
  const cat = document.getElementById("f-category").value;
  const minNet = parseFloat(document.getElementById("f-net").value);
  const minLiq = parseFloat(document.getElementById("f-liq").value) || 0;
  const minConf = parseFloat(document.getElementById("f-conf").value);
  const hideWarn = document.getElementById("f-warn").checked;

  state.filtered = state.events.filter((e) => {
    if (cat && e.category !== cat) return false;
    if (e.net_spread < minNet) return false;
    if (e.confidence < minConf) return false;
    if (hideWarn && e.resolution_warning) return false;
    const liq = Math.min(e.volume_pm || 0, e.volume_kalshi || 0);
    if (liq < minLiq) return false;
    return true;
  });
  renderScatter();
  renderTables();
}

function renderScatter() {
  const ctx = document.getElementById("scatter").getContext("2d");
  const pts = state.filtered.map((e) => ({ x: e.pm_mid, y: plottedKalshi(e), e }));
  const ds = {
    label: "matched pairs",
    data: pts,
    pointBackgroundColor: pts.map((p) => pointColor(p.e)),
    pointRadius: 5, pointHoverRadius: 8, showLine: false,
  };
  const diag = { label: "agreement", data: [{ x: 0, y: 0 }, { x: 1, y: 1 }],
    type: "line", borderColor: "rgba(124,140,134,0.6)", borderDash: [4, 4],
    borderWidth: 1, pointRadius: 0, fill: false };

  if (state.chart) state.chart.destroy();
  state.chart = new Chart(ctx, {
    type: "scatter",
    data: { datasets: [ds, diag] },
    options: {
      maintainAspectRatio: false,
      scales: {
        x: { min: 0, max: 1, title: { display: true, text: "Polymarket prob", color: "#7c8c86" },
             grid: { color: "#1b2429" }, ticks: { color: "#7c8c86" } },
        y: { min: 0, max: 1, title: { display: true, text: "Kalshi prob (inversion-adjusted)", color: "#7c8c86" },
             grid: { color: "#1b2429" }, ticks: { color: "#7c8c86" } },
      },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (c) => c.raw.e ? `${c.raw.e.title_pm} · net ${FMT.pct(c.raw.e.net_spread)}` : "" } },
      },
      onClick: (evt, els) => {
        const hit = els.find((el) => state.chart.data.datasets[el.datasetIndex].data[el.index].e);
        if (hit) {
          const p = state.chart.data.datasets[hit.datasetIndex].data[hit.index];
          showKpi(p.e);
        }
      },
    },
  });
}

function showKpi(e) {
  state.selected = e;
  const netCls = e.net_spread >= 0.05 ? "net-pos" : "net-neg";
  const badges =
    (e.resolution_warning ? '<span class="badge warn">resolution mismatch</span>' : "") +
    (e.inverted ? '<span class="badge inv">inverted</span>' : "");
  document.getElementById("kpi").innerHTML = `
    <div class="kpi">
      <div class="cat">${e.category}</div>
      <h3>${e.title_pm}</h3>
      <div class="cat">Kalshi: ${e.title_kalshi}</div>
      <div style="margin-top:8px">${badges || ""}</div>
      <div class="kpi-grid">
        <div><span class="k">PM mid</span><span>${FMT.pct(e.pm_mid)}</span></div>
        <div><span class="k">Kalshi mid</span><span>${FMT.pct(e.kalshi_mid)}</span></div>
        <div><span class="k">PM bid/ask</span><span>${FMT.pct(e.pm_bidask[0])} / ${FMT.pct(e.pm_bidask[1])}</span></div>
        <div><span class="k">Kalshi bid/ask</span><span>${FMT.pct(e.kalshi_bidask[0])} / ${FMT.pct(e.kalshi_bidask[1])}</span></div>
        <div><span class="k">Gross spread</span><span>${FMT.pct(e.gross_spread)}</span></div>
        <div><span class="k">Net spread</span><span class="${netCls}">${FMT.pct(e.net_spread)}</span></div>
        <div><span class="k">Edge @ $100</span><span class="${netCls}">${FMT.usd(e.est_edge_usd)}</span></div>
        <div><span class="k">Confidence</span><span>${e.confidence.toFixed(2)}</span></div>
        <div><span class="k">Vol PM 24h</span><span>${FMT.num(e.volume_pm)}</span></div>
        <div><span class="k">Vol Kalshi 24h</span><span>${FMT.num(e.volume_kalshi)}</span></div>
      </div>
      <canvas id="spark" height="48"></canvas>
      <div style="margin-top:10px">
        <a href="${e.link_pm}" target="_blank" rel="noopener">Polymarket ↗</a> ·
        <a href="${e.link_kalshi}" target="_blank" rel="noopener">Kalshi ↗</a>
      </div>
    </div>`;
  drawSpark(e);
}

function drawSpark(e) {
  const hist = e.spread_history || [];
  if (!hist.length) return;
  const ctx = document.getElementById("spark").getContext("2d");
  new Chart(ctx, {
    type: "line",
    data: { labels: hist.map((h) => h.ts),
      datasets: [{ data: hist.map((h) => h.net), borderColor: "#00ff9c",
        borderWidth: 1.5, pointRadius: 0, fill: false, tension: 0.25 }] },
    options: { maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: { x: { display: false }, y: { display: false } } },
  });
}

function tableRows(el, rows, valueFn, valueLabel) {
  const head = `<thead><tr><th>Event</th><th>Cat</th><th class="num">${valueLabel}</th></tr></thead>`;
  const body = rows.map((e) =>
    `<tr data-id="${e.pair_id}"><td>${e.title_pm}</td><td>${e.category}</td>` +
    `<td class="num pos">${valueFn(e)}</td></tr>`).join("");
  el.innerHTML = head + `<tbody>${body}</tbody>`;
  el.querySelectorAll("tr[data-id]").forEach((tr) =>
    tr.addEventListener("click", () => {
      const e = state.filtered.find((x) => String(x.pair_id) === tr.dataset.id);
      if (e) showKpi(e);
    }));
}

function convergeScore(e) {
  // until the convergence model lands, proxy by spread velocity (last move).
  const h = e.spread_history || [];
  if (h.length >= 2) return Math.abs(h[h.length - 1].net - h[h.length - 2].net);
  return 0;
}

function renderTables() {
  const byNet = [...state.filtered].sort((a, b) => b.net_spread - a.net_spread).slice(0, 12);
  tableRows(document.getElementById("t-roi"), byNet, (e) => FMT.pct(e.net_spread), "net");

  const byConv = [...state.filtered].sort((a, b) => convergeScore(b) - convergeScore(a)).slice(0, 12);
  tableRows(document.getElementById("t-converge"), byConv, (e) => FMT.pct(convergeScore(e)), "velocity");

  const byConf = [...state.filtered].sort((a, b) => b.confidence - a.confidence).slice(0, 12);
  tableRows(document.getElementById("t-conf"), byConf, (e) => e.confidence.toFixed(2), "conf");
}

load();
