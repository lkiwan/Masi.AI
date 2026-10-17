/* Masi.AI terminal — data wiring. */
const $ = (id) => document.getElementById(id);
const fmtN = (v, d = 2) => v == null ? "—" : Number(v).toLocaleString("en-US", {
  minimumFractionDigits: d, maximumFractionDigits: d });
const fmtInt = (v) => v == null ? "—" : Number(v).toLocaleString("en-US");
const chgClass = (v) => v == null ? "flat" : v > 0 ? "up" : v < 0 ? "down" : "flat";
const chgTxt = (v) => v == null ? "—" : (v > 0 ? "+" : "") + v.toFixed(2) + "%";

let market = [];
let selected = null;
let sortKey = "ticker", sortDir = 1;
let chart = null, chartRange = 30;

/* ---------------- instruments ---------------- */
async function loadMarket() {
  const r = await fetch("/api/v1/market/overview");
  market = await r.json();
  $("inst-count").textContent = market.length + " tickers";
  renderList();
  if (!selected && market.length) selectTicker(market[0].ticker);
}

function renderList() {
  const q = $("search").value.trim().toLowerCase();
  let rows = market.filter(m =>
    !q || m.ticker.toLowerCase().includes(q) || m.name.toLowerCase().includes(q));
  rows.sort((a, b) => {
    const av = a[sortKey], bv = b[sortKey];
    if (av == null) return 1; if (bv == null) return -1;
    return (typeof av === "string" ? av.localeCompare(bv) : av - bv) * sortDir;
  });
  $("inst-list").innerHTML = rows.map(m => `
    <div class="inst-row ${m.ticker === selected ? "selected" : ""}" data-t="${m.ticker}">
      <div><div class="inst-ticker">${m.ticker}</div>
      <div class="inst-name">${m.name || ""}</div></div>
      <div class="inst-price mono">${fmtN(m.price)}</div>
      <div class="mono num ${chgClass(m.change_pct)}">${chgTxt(m.change_pct)}</div>
    </div>`).join("");
  document.querySelectorAll(".inst-row").forEach(el =>
    el.addEventListener("click", () => selectTicker(el.dataset.t)));
}

document.querySelectorAll(".table-head button").forEach(b =>
  b.addEventListener("click", () => {
    const k = b.dataset.sort;
    if (sortKey === k) sortDir *= -1; else { sortKey = k; sortDir = 1; }
    renderList();
  }));
$("search").addEventListener("input", renderList);

/* ---------------- quote detail ---------------- */
async function selectTicker(t) {
  selected = t;
  renderList();
  const m = market.find(x => x.ticker === t);
  if (!m) return;
  $("q-name").textContent = m.name || t;
  $("q-ticker").textContent = t;
  $("q-price").textContent = fmtN(m.price) + " MAD";
  $("q-price").className = "q-price mono " + chgClass(m.change_pct);
  $("q-chg").textContent = chgTxt(m.change_pct);
  $("q-chg").className = "q-chg mono " + chgClass(m.change_pct);
  $("q-vol").textContent = fmtInt(m.volume_titles);
  $("q-turn").textContent = m.volume_mad == null ? "—" : fmtInt(Math.round(m.volume_mad)) + " MAD";
  $("q-date").textContent = m.session_date || "—";
  loadChart(t);
  loadOrderBook(t);
}

async function loadChart(t) {
  const r = await fetch(`/api/v1/history/${t}?limit=1000`);
  const bars = await r.json();
  bars.sort((a, b) => a.date.localeCompare(b.date));
  const data = chartRange > 0 ? bars.slice(-chartRange) : bars;
  const labels = data.map(b => b.date);
  const closes = data.map(b => b.close);
  const up = closes.length > 1 && closes[closes.length - 1] >= closes[0];
  const col = up ? "#3cb56e" : "#e05252";

  if (chart) chart.destroy();
  Chart.defaults.font.family = "'IBM Plex Mono', monospace";
  Chart.defaults.color = "#5b6577";
  chart = new Chart($("price-chart"), {
    type: "line",
    data: { labels, datasets: [{
      data: closes, borderColor: col, borderWidth: 1.5, pointRadius: 0,
      tension: 0.1, fill: false }]},
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "#121823", borderColor: "#1d2534", borderWidth: 1,
          titleColor: "#d8dde6", bodyColor: "#d8dde6", padding: 10, displayColors: false,
          callbacks: { label: (c) => fmtN(c.parsed.y) + " MAD" }
        }
      },
      scales: {
        x: { grid: { color: "#141b27" }, ticks: { maxTicksLimit: 8, font: { size: 10 } } },
        y: { position: "right", grid: { color: "#141b27" },
             ticks: { font: { size: 10 }, callback: (v) => fmtN(v, 0) } }
      }
    }
  });
}

document.querySelectorAll("#range-btns button").forEach(b =>
  b.addEventListener("click", () => {
    document.querySelectorAll("#range-btns button").forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    chartRange = parseInt(b.dataset.range, 10);
    if (selected) loadChart(selected);
  }));

/* ---------------- order book ---------------- */
async function loadOrderBook(t) {
  const r = await fetch(`/api/v1/orderbook/${t}`);
  if (!r.ok) {
    $("ob-bids").innerHTML = $("ob-asks").innerHTML =
      '<div class="ob-row"><span class="c">No data</span></div>';
    $("ob-spread").textContent = "—"; $("ob-time").textContent = "";
    return;
  }
  const lvls = await r.json();
  const bids = lvls.filter(l => l.side === "bid").sort((a, b) => b.price - a.price);
  const asks = lvls.filter(l => l.side === "ask").sort((a, b) => a.price - b.price);
  const maxQ = Math.max(1, ...lvls.map(l => l.qty));

  const row = (l, side) => `
    <div class="ob-row ${side}">
      <div class="bar" style="width:${(l.qty / maxQ * 100).toFixed(1)}%"></div>
      <span>${fmtInt(l.qty)}</span><span class="c">${l.orders}</span>
      <span class="r">${fmtN(l.price)}</span>
    </div>`;
  $("ob-bids").innerHTML = bids.map(l => row(l, "bid")).join("") ||
    '<div class="ob-row"><span class="c">—</span></div>';
  $("ob-asks").innerHTML = asks.map(l => row(l, "ask")).join("") ||
    '<div class="ob-row"><span class="c">—</span></div>';

  if (bids.length && asks.length) {
    const spread = asks[0].price - bids[0].price;
    $("ob-spread").textContent = fmtN(spread) + " MAD (" +
      (spread / bids[0].price * 100).toFixed(2) + "%)";
  } else $("ob-spread").textContent = "—";
  $("ob-time").textContent = "snapshot";
}

/* ---------------- news ---------------- */
async function loadNews() {
  const r = await fetch("/api/v1/news?limit=40");
  const arts = await r.json();
  $("news-list").innerHTML = arts.map(a => `
    <a class="news-item" href="${a.url}" target="_blank" rel="noopener">
      <div class="news-title">${a.title}</div>
      <div class="news-meta">
        <span class="src ${a.source}">${a.source === "medias24" ? "Medias24" : "L'Économiste"}</span>
        <span class="news-date">${a.published_at ? a.published_at.slice(0, 10) : ""}</span>
      </div>
    </a>`).join("");
}

/* ---------------- macro + MASI ---------------- */
const MACRO_LABELS = {
  gdp_growth_pct: "GDP growth", inflation_cpi_pct: "Inflation (CPI)",
  unemployment_pct: "Unemployment", imf_debt_pct_gdp: "Public debt / GDP",
  imf_current_account_pct_gdp: "Current account / GDP", gdp_per_capita_usd: "GDP per capita",
};

function sparkline(el, vals, color) {
  const c = document.createElement("canvas");
  c.className = "macro-spark"; el.appendChild(c);
  new Chart(c, {
    type: "line",
    data: { labels: vals.map((_, i) => i), datasets: [{
      data: vals, borderColor: color, borderWidth: 1.2, pointRadius: 0, tension: 0.2 }]},
    options: { responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: { x: { display: false }, y: { display: false } },
      animation: false }
  });
}

async function loadMacro() {
  const box = $("macro-list");
  box.innerHTML = "";
  for (const [ind, label] of Object.entries(MACRO_LABELS)) {
    try {
      const r = await fetch(`/api/v1/macro/${ind}?limit=30`);
      if (!r.ok) continue;
      const s = await r.json();
      if (!s.length) continue;
      const vals = s.map(p => p.value).reverse();
      const last = s[0], prev = s[1];
      const d = prev ? last.value - prev.value : null;
      const row = document.createElement("div");
      row.className = "macro-row";
      row.innerHTML = `
        <div class="macro-name">${label}<div class="lbl">${last.date.slice(0, 4)} · ${last.source}</div></div>
        <div class="macro-val">${fmtN(last.value)}<span class="${chgClass(d)}" style="font-size:10px"> ${d == null ? "" : (d > 0 ? "▲" : d < 0 ? "▼" : "")}</span></div>`;
      box.appendChild(row);
      sparkline(row, vals, d == null ? "#8a94a6" : d >= 0 ? "#3cb56e" : "#e05252");
    } catch { /* indicator unavailable — skip */ }
  }
  // MASI index in header
  try {
    const r = await fetch("/api/v1/macro/masi_index?limit=30");
    const s = await r.json();
    if (s.length > 1) {
      const last = s[0].value, prev = s[1].value;
      const chg = (last - prev) / prev * 100;
      $("masi-value").textContent = fmtN(last, 2);
      const el = $("masi-chg");
      el.textContent = chgTxt(chg);
      el.className = "mono " + chgClass(chg);
    }
  } catch { /* skip */ }
}

/* ---------------- clock / session ---------------- */
function tickClock() {
  try {
    const now = new Date(new Date().toLocaleString("en-US", { timeZone: "Africa/Casablanca" }));
    $("clock").textContent = now.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) +
      " · " + now.toLocaleDateString("en-GB", { day: "2-digit", month: "short" });
    const day = now.getDay(), mins = now.getHours() * 60 + now.getMinutes();
    const open = day >= 1 && day <= 5 && mins >= 600 && mins < 930; // ~10:00–15:30
    $("status-dot").className = "status-dot " + (open ? "open" : "closed");
    $("status-text").textContent = open ? "Open" : "Closed";
  } catch { /* skip */ }
}

/* ---------------- boot ---------------- */
(async function init() {
  tickClock(); setInterval(tickClock, 15000);
  await Promise.all([loadMarket(), loadNews(), loadMacro()]);
  $("foot-updated").textContent = "Updated " +
    new Date().toLocaleString("en-GB", { timeZone: "Africa/Casablanca" });
  // refresh quotes every 5 min
  setInterval(async () => { await loadMarket(); if (selected) loadOrderBook(selected); }, 300000);
})();
