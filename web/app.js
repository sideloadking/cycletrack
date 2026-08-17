"use strict";

/* ------------------------------------------------------------------ utils */

const $ = (sel) => document.querySelector(sel);
const view = $("#view");

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(el._t);
  el._t = setTimeout(() => (el.hidden = true), 3500);
}

/* make a click-navigable row keyboard-accessible: Enter or Space activates */
function activateRow(el, go) {
  el.tabIndex = 0;
  el.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      go();
    }
  });
}

function fmtDate(unix) {
  if (!unix) return "—";
  const d = new Date(unix * 1000);
  const opts = { day: "numeric", month: "short", year: "numeric" };
  return d.toLocaleDateString(undefined, opts);
}
function fmtDateTime(unix) {
  if (!unix) return "—";
  const d = new Date(unix * 1000);
  return d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}
function fmtDur(s) {
  if (!s && s !== 0) return "—";
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`
               : `${m}:${String(sec).padStart(2, "0")}`;
}
function fmtKm(m) { return m == null ? "—" : `${(m / 1000).toFixed(2)} km`; }
function fmtM(m) { return m == null ? "—" : `${Math.round(m)} m`; }
function fmtKmh(mps) { return mps == null ? "—" : `${(mps * 3.6).toFixed(1)} km/h`; }
function fmtW(w) { return w == null ? "—" : `${Math.round(w)} W`; }
function fmtWkg(w) { return w == null ? "—" : `${w.toFixed(1)} W/kg`; }

const PLOT_CONFIG = { displayModeBar: false, responsive: true };
const PLOT_FONT = { color: "#5b646e", family: "'IBM Plex Mono', monospace", size: 11 };

/* shared axis styling — faint hairlines, no zero-line noise */
const PLOT_AXIS = {
  gridcolor: "#e6e9e0",
  zeroline: false,
  zerolinecolor: "#e6e9e0",
  ticks: "",
  showline: false,
};

/* crosshair shown on hover so the pointed-at sample is always obvious */
const PLOT_SPIKE = {
  showspikes: true,
  spikemode: "across",
  spikesnap: "data",
  spikecolor: "rgba(25,29,34,.35)",
  spikethickness: 1,
  spikedash: "dot",
};

const PLOT_LAYOUT = {
  paper_bgcolor: "transparent",
  plot_bgcolor: "transparent",
  font: PLOT_FONT,
  margin: { l: 50, r: 16, t: 12, b: 40 },
  xaxis: { ...PLOT_AXIS, ...PLOT_SPIKE },
  yaxis: { ...PLOT_AXIS },
  hoverlabel: {
    bgcolor: "#ffffff",
    bordercolor: "#d8dcd2",
    borderradius: 6,
    align: "left",
    namelength: -1,
    font: { color: "#191d22", family: "'IBM Plex Mono', monospace", size: 11 },
  },
};

/* signboard palette — confident is a permanent green sign, an estimate is a
   temporary amber sign, wide uncertainty is hatched */
const C = {
  amber: "#0e7a44",
  amberDim: "#a16d00",
  blue: "#1b5c9e",
  red: "#c9342b",
  green: "#0e7a44",
  faint: "#b7c0b1",
};

/* elevation-source labels — technical backend names, sign-friendly display */
const ELEV_LABELS = {
  lidar: "LIDAR",
  eudem25m: "25 m DEM",
  terrarium: "30 m DEM",
  barometer: "DEVICE",
};

const ICON_PLAY = '<svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true"><path d="M4 2.5v11l9-5.5z" fill="currentColor"/></svg>';
const ICON_PAUSE = '<svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true"><rect x="3.5" y="2.5" width="3.4" height="11" rx=".8" fill="currentColor"/><rect x="9.1" y="2.5" width="3.4" height="11" rx=".8" fill="currentColor"/></svg>';

/* interpolate between two hex colours (t: 0→1) for age-graded series */
function mixColor(a, b, t) {
  const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16));
  const pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16));
  const c = pa.map((v, i) => Math.round(v + (pb[i] - v) * t));
  return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
}

/* ------------------------------------------------------------------ router */

const routes = {
  dashboard: renderDashboard,
  rides: renderRides,
  import: renderImport,
  records: renderRecords,
  routes: renderRoutes,
  route: renderRouteDetail,
  ride: renderRideDetail,
  profile: renderProfile,
};

function currentRoute() {
  const h = location.hash.replace(/^#\//, "") || "dashboard";
  const [name, param] = h.split("/");
  return { name, param };
}

function navigate() {
  const { name, param } = currentRoute();
  document.querySelectorAll("nav a").forEach((a) => {
    const active = a.dataset.nav === name;
    a.classList.toggle("active", active);
    if (active) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  const title = { dashboard: "Dashboard", rides: "Rides", import: "Import",
                  records: "Records", routes: "Routes", profile: "Profile",
                  ride: "Ride", route: "Route" }[name] || "Dashboard";
  $("#page-title").textContent = title;
  if ((name === "ride" || name === "route") && param) {
    (routes[name])(param);
  } else {
    (routes[name] || renderDashboard)();
  }
}

window.addEventListener("hashchange", navigate);

/* ------------------------------------------------------------------ init */

async function init() {
  let ok = false;
  try {
    const h = await api("/api/health");
    $("#sidebar-foot").textContent = `${h.rides} ride${h.rides === 1 ? "" : "s"} · stored locally`;
    ok = true;
  } catch (e) {
    $("#sidebar-foot").textContent = "engine offline";
  }
  $("#topbar-right").innerHTML =
    `<span class="local-chip ${ok ? "" : "offline"}" title="Rides, elevation, power and the database all stay on this machine."><i class="dot"></i>local only</span>`;
  navigate();
}

/* ============================================================== DASHBOARD */

async function renderDashboard() {
  view.innerHTML = `
    <div id="dash-stats"></div>
    <div class="card">
      <h2>Watts at a fixed heart rate <span class="sub">more watts at the same heart rate means fitter</span></h2>
      <div class="flex wrap mb-8" id="hr-picker"></div>
      <div id="watts-hr-chart" class="chart tall"></div>
    </div>
    <div class="card">
      <h2>Power curve trend <span class="sub">best sustained power at a chosen duration</span></h2>
      <div class="flex wrap mb-8" id="pc-picker"></div>
      <div id="pc-trend-chart" class="chart med"></div>
    </div>
    <div class="card">
      <h2>Power duration curves <span class="sub">last 5 rides overlaid</span></h2>
      <div id="pc-curve-chart" class="chart med"></div>
    </div>
    <div class="grid cols-2">
      <div class="card"><h2>Fitness &amp; freshness (CTL / ATL / TSB)</h2><div id="fitness-chart" class="chart med"></div></div>
      <div class="card"><h2>Cardiac drift <span class="sub">HR rise during steady effort</span></h2><div id="drift-chart" class="chart med"></div></div>
      <div class="card"><h2>Personal records</h2><div id="dash-records"></div></div>
      <div class="card"><h2>Most repeated routes <span class="sub">same roads, fair comparison</span></h2><div id="dash-routes"></div></div>
    </div>`;

  const [rides, fitness, records, power, drift, routeList] = await Promise.all([
    api("/api/rides"), api("/api/trends/fitness"), api("/api/records"),
    api("/api/trends/power"), api("/api/trends/cardiac"), api("/api/routes"),
  ]);
  renderDashboardStats(rides);
  renderRecordsList(records, $("#dash-records"));
  renderPowerTrend(power);
  renderPowerCurves(power);
  renderCardiacTrend(drift);
  renderDashRoutes(routeList);

  // watts@HR headline
  const wh = await api("/api/trends/watts_hr");
  const hrs = wh.fixed_hrs.map(String);
  let selected = "140";
  if (!hrs.includes(selected)) selected = hrs[0];
  $("#hr-picker").innerHTML = hrs.map((hr) =>
    `<button class="${hr === selected ? "primary" : ""}" data-hr="${hr}">${hr} bpm</button>`).join("");
  $("#hr-picker").querySelectorAll("button").forEach((b) =>
    b.addEventListener("click", () => { selected = b.dataset.hr; drawWattsHr(wh, selected); }));

  drawWattsHr(wh, selected);
  drawFitness(fitness);
}

function renderDashboardStats(rides) {
  const el = $("#dash-stats");
  if (!rides.length) {
    el.innerHTML = `<div class="card">No rides yet — <a href="#/import" class="text-green">import your first .fit file</a>.</div>`;
    return;
  }
  const totalKm = rides.reduce((a, r) => a + (r.distance_m || 0), 0) / 1000;
  const totalDur = rides.reduce((a, r) => a + (r.duration_s || 0), 0);
  const totalGain = rides.reduce((a, r) => a + (r.gain_m || 0), 0);
  const stats = [
    { value: rides.length, label: "rides imported" },
    { value: totalKm.toFixed(0), label: "total distance", unit: "km" },
    { value: fmtDur(totalDur), label: "total time" },
    { value: Math.round(totalGain), label: "total climbing", unit: "m" },
  ];
  el.innerHTML = `
    <div class="instrument hero">
      ${stats.map((s) => statCard(s.value, s.label, { unit: s.unit })).join("")}
    </div>
    <div class="sig-note">solid = confident · amber = estimated · hatching = wide</div>`;
}

function drawWattsHr(wh, hr) {
  const el = $("#watts-hr-chart");
  if (!el) return;
  const traces = [];
  // Faint context lines for other HRs — dim blue-greys, never fighting the headline.
  const faintPalette = { 130: "#c9cfc4", 140: "#c2c9bc", 150: "#bcc3b6", 160: "#b6bdb0" };

  for (const h of wh.fixed_hrs.map(String)) {
    if (h === hr) continue;
    const pts = wh.series[h];
    if (!pts.length) continue;
    traces.push({
      x: pts.map((p) => fmtDate(p.date)),
      y: pts.map((p) => p.watts),
      mode: "lines", name: `${h} bpm`,
      line: { width: 1, dash: "dot", color: faintPalette[h] || "#556" },
      opacity: 0.4, showlegend: true,
      hovertemplate: "%{y:.0f} W<extra></extra>",
    });
  }

  const pts = wh.series[hr] || [];
  const confident = pts.filter((p) => p.confidence === "confident");
  const context = pts.filter((p) => p.confidence === "context");
  const envelope = (sel, hi, lo, fill) => [
    { x: sel.map((p) => fmtDate(p.date)), y: sel.map((p) => p[hi]), mode: "lines",
      line: { width: 0 }, hoverinfo: "skip", showlegend: false },
    { x: sel.map((p) => fmtDate(p.date)), y: sel.map((p) => p[lo]), mode: "lines",
      line: { width: 0 }, fill: "tonexty", fillcolor: fill, hoverinfo: "skip", showlegend: false },
  ];
  if (confident.length) {
    traces.push(...envelope(confident, "hi", "lo", "rgba(14,122,68,.14)"), {
      x: confident.map((p) => fmtDate(p.date)), y: confident.map((p) => p.watts),
      mode: "lines+markers", name: `${hr} bpm · confident`,
      line: { color: C.amber, width: 2.5 },
      marker: { size: 6, color: C.amber, line: { color: "#ffffff", width: 1 } },
      customdata: confident.map((p) => [Math.round(p.lo), Math.round(p.hi)]),
      hovertemplate: "<b>%{y:.0f} W</b> · band %{customdata[0]}–%{customdata[1]} W<extra></extra>",
    });
  }
  if (context.length) {
    traces.push(...envelope(context, "hi", "lo", "rgba(161,109,0,.14)"), {
      x: context.map((p) => fmtDate(p.date)), y: context.map((p) => p.watts),
      mode: "lines+markers", name: `${hr} bpm · context`,
      line: { color: C.amberDim, width: 2, dash: "dash" },
      marker: { size: 5, color: C.amberDim, line: { color: "#ffffff", width: 1 } },
      customdata: context.map((p) => [Math.round(p.lo), Math.round(p.hi)]),
      hovertemplate: "<b>%{y:.0f} W</b> · band %{customdata[0]}–%{customdata[1]} W<extra></extra>",
    });
  }

  Plotly.newPlot(el, traces, {
    ...PLOT_LAYOUT,
    yaxis: { ...PLOT_LAYOUT.yaxis, title: { text: "watts", font: PLOT_FONT } },
    legend: { orientation: "h", y: 1.12, font: PLOT_FONT },
    hovermode: "x unified",
  }, PLOT_CONFIG);
}

function drawFitness(fit) {
  const el = $("#fitness-chart");
  if (!el) return;
  if (!fit.points.length) {
    el.innerHTML = '<div class="empty">Import rides with heart-rate data to see fitness trends.</div>';
    return;
  }
  const x = fit.points.map((p) => fmtDate(p.date));
  const tsbColors = fit.points.map((p) => (p.tsb >= 0 ? C.green : C.amberDim));
  // TSB bars live on their own right axis so form never stacks on the
  // fitness/fatigue lines.
  Plotly.newPlot(el, [
    { x, y: fit.points.map((p) => p.ctl), name: "CTL (fitness)", line: { color: C.blue, width: 2 },
      hovertemplate: "%{y:.1f}<extra></extra>" },
    { x, y: fit.points.map((p) => p.atl), name: "ATL (fatigue)", line: { color: C.amber, width: 1.6 },
      hovertemplate: "%{y:.1f}<extra></extra>" },
    { x, y: fit.points.map((p) => p.tsb), name: "TSB (form)", type: "bar", yaxis: "y2",
      marker: { color: tsbColors, opacity: 0.55 },
      hovertemplate: "%{y:.1f}<extra></extra>" },
  ], {
    ...PLOT_LAYOUT,
    yaxis: { ...PLOT_LAYOUT.yaxis, title: { text: "CTL / ATL", font: PLOT_FONT } },
    yaxis2: { overlaying: "y", side: "right", showgrid: false, zeroline: false, ticks: "", title: { text: "TSB", font: PLOT_FONT } },
    legend: { orientation: "h", y: 1.12, font: PLOT_FONT },
    hovermode: "x unified",
  }, PLOT_CONFIG);
}

function renderRecordsList(records, el) {
  if (!records.length) {
    el.innerHTML = '<div class="empty">No records yet.</div>';
    return;
  }
  el.innerHTML = records.map((r) => `
    <div class="flex spread row-line">
      <span>${r.label}</span>
      <b class="mono">${r.value_display}</b>
    </div>`).join("");
}

/* ------------------------------------------------------- power trends */

function renderPowerTrend(power) {
  const el = $("#pc-trend-chart");
  if (!el) return;
  const durations = ["1", "5", "20", "60"];
  let selected = "5";
  $("#pc-picker").innerHTML = durations.map((d) =>
    `<button class="${d === selected ? "primary" : ""}" data-min="${d}">${d} min</button>`).join("");
  $("#pc-picker").querySelectorAll("button").forEach((b) =>
    b.addEventListener("click", () => { selected = b.dataset.min; drawPowerTrend(power, selected); }));
  drawPowerTrend(power, selected);
}

function drawPowerTrend(power, dur) {
  const el = $("#pc-trend-chart");
  if (!el) return;
  const pts = power.series[dur] || [];
  if (!pts.length) {
    el.innerHTML = '<div class="empty">Import rides to see best-power trends.</div>';
    return;
  }
  const px = pts.map((p) => fmtDate(p.date));
  const py = pts.map((p) => p.watts);
  Plotly.newPlot(el, [
    { x: px, y: pts.map((p) => (p.hi != null ? p.hi : p.watts)), mode: "lines",
      line: { width: 0 }, hoverinfo: "skip", showlegend: false },
    { x: px, y: pts.map((p) => (p.lo != null ? p.lo : p.watts)), mode: "lines",
      line: { width: 0 }, fill: "tonexty", fillcolor: "rgba(161,109,0,.16)", hoverinfo: "skip", showlegend: false },
    { x: px, y: py, mode: "lines+markers", name: `best ${dur} min`,
      line: { color: C.amberDim, width: 2.5 },
      marker: { size: 6, color: C.amberDim, line: { color: "#ffffff", width: 1 } },
      customdata: pts.map((p) => [p.lo != null ? Math.round(p.lo) : null, p.hi != null ? Math.round(p.hi) : null]),
      hovertemplate: "<b>%{y:.0f} W</b> · band %{customdata[0]}–%{customdata[1]} W<extra></extra>",
    },
  ], {
    ...PLOT_LAYOUT,
    yaxis: { ...PLOT_LAYOUT.yaxis, title: { text: "watts", font: PLOT_FONT } },
    hovermode: "x unified",
  }, PLOT_CONFIG);
}

function renderPowerCurves(power) {
  const el = $("#pc-curve-chart");
  if (!el) return;
  const curves = power.curves || [];
  if (!curves.length) {
    el.innerHTML = '<div class="empty">No rides yet.</div>';
    return;
  }
  // Oldest ride starts dim blue-grey, newest ends headlight amber — so the
  // overlaid curves read oldest→newest instead of a pile of identical lines.
  const traces = curves.map((c, i) => {
    const pts = (c.points || []).filter((p) => p.watts != null);
    const t = curves.length === 1 ? 1 : i / (curves.length - 1);
    const color = mixColor(C.faint, C.amber, t);
    const newest = i === curves.length - 1;
    return {
      x: pts.map((p) => p.min),
      y: pts.map((p) => p.watts),
      mode: "lines+markers",
      name: fmtDate(c.date),
      line: { color, width: 1.4 + 1.1 * t },
      marker: { size: newest ? 6 : 3.5, color, opacity: newest ? 1 : 0.75 },
      hovertemplate: "<b>%{y:.0f} W</b> for %{x:.0f} min<extra></extra>",
    };
  });
  Plotly.newPlot(el, traces, {
    ...PLOT_LAYOUT,
    xaxis: { ...PLOT_LAYOUT.xaxis, type: "log", nticks: 6, tickformat: ".0f", title: { text: "minutes (log)", font: PLOT_FONT } },
    yaxis: { ...PLOT_LAYOUT.yaxis, title: { text: "watts", font: PLOT_FONT } },
    legend: { orientation: "h", y: 1.12, font: { ...PLOT_FONT, size: 9.5 } },
    hovermode: "x unified",
  }, PLOT_CONFIG);
}

function renderCardiacTrend(drift) {
  const el = $("#drift-chart");
  if (!el) return;
  const pts = drift.points || [];
  if (!pts.length) {
    el.innerHTML = '<div class="empty">No steady-effort windows yet — drift needs long, steady effort with HR.</div>';
    return;
  }
  const colors = pts.map((p) => (p.drift_bpm_per_hr > 0 ? C.amberDim : C.blue));
  Plotly.newPlot(el, [{
    x: pts.map((p) => fmtDate(p.date)),
    y: pts.map((p) => p.drift_bpm_per_hr),
    type: "bar",
    marker: { color: colors, opacity: 0.85 },
    customdata: pts.map((p) => `${p.drift_pct_per_hr}%/hr · ${p.duration_min} min @ ~${p.mean_power} W`),
    hovertemplate: "%{x}<br><b>%{y:.1f} bpm/hr</b> (%{customdata})<extra></extra>",
  }], {
    ...PLOT_LAYOUT,
    yaxis: { ...PLOT_LAYOUT.yaxis, title: { text: "bpm per hour", font: PLOT_FONT } },
    hovermode: "x",
  }, PLOT_CONFIG);
}

function renderDashRoutes(routeList) {
  const el = $("#dash-routes");
  if (!el) return;
  const top = (routeList || []).filter((r) => r.n_rides > 1).sort((a, b) => b.n_rides - a.n_rides).slice(0, 4);
  if (!top.length) {
    el.innerHTML = '<div class="empty">Ride the same route twice and it will show up here for fair comparison.</div>';
    return;
  }
  el.innerHTML = top.map((r) => `
    <div class="flex spread route-row row-line" data-route="${r.id}" aria-label="Open route ${r.name}">
      <div>
        <b class="mono">${r.name}</b>
        <div class="muted small">${fmtDate(r.first_at)} → ${fmtDate(r.last_at)}</div>
      </div>
      <div class="flex">
        <span class="pill high">×${r.n_rides}</span>
        <span class="mono small">${fmtKm(r.distance_m)}</span>
      </div>
    </div>`).join("");
  el.querySelectorAll(".route-row").forEach((row) => {
    const go = () => (location.hash = `#/route/${row.dataset.route}`);
    row.addEventListener("click", go);
    activateRow(row, go);
  });
}

/* ============================================================== RIDES */

async function renderRides() {
  view.innerHTML = `
    <div class="card">
      <div class="spread">
        <h2 class="m-0">Rides</h2>
        <a class="btn" href="#/import">Import .fit</a>
      </div>
      <div id="rides-list"></div>
    </div>`;
  const [rides, routeList] = await Promise.all([api("/api/rides"), api("/api/routes")]);
  const routeById = {};
  (routeList || []).forEach((rt) => (routeById[rt.id] = rt));
  const el = $("#rides-list");
  if (!rides.length) {
    el.innerHTML = '<div class="empty">No rides yet. Import your Wahoo .fit history to get started.</div>';
    return;
  }
  el.innerHTML = `
    <div class="table-scroll">
    <table class="list">
      <thead><tr><th class="mono">Date</th><th class="num">Distance</th><th class="num">Time</th><th class="num">Climb</th><th class="num">Avg HR</th><th class="num">TRIMP</th><th class="num">Power (est.)</th><th>Elevation</th><th>Route</th></tr></thead>
      <tbody>
        ${rides.map((r) => {
          const rt = r.route_id != null ? routeById[r.route_id] : null;
          const routeBadge = rt && rt.n_rides > 1
            ? `<span class="pill med route-pill" title="${rt.name}">×${rt.n_rides}</span>`
            : (rt ? `<span class="pill low route-pill" title="${rt.name}">×1</span>` : "—");
          return `
          <tr data-id="${r.id}" data-route="${r.route_id || ""}" aria-label="Open ride ${fmtDateTime(r.started_at)}">
            <td class="mono">${fmtDateTime(r.started_at)}</td>
            <td class="num">${fmtKm(r.distance_m)}</td>
            <td class="num">${fmtDur(r.duration_s)}</td>
            <td class="num">${fmtM(r.gain_m)}</td>
            <td class="num">${r.avg_hr ? Math.round(r.avg_hr) + " bpm" : "—"}</td>
            <td class="num">${r.trimp ? r.trimp.toFixed(0) : "—"}</td>
            <td class="num">${fmtW(r.avg_watts)}</td>
            <td><span class="pill ${r.elevation_source === "lidar" ? "high" : r.elevation_source === "eudem25m" ? "med" : "low"}">${ELEV_LABELS[r.elevation_source] || (r.elevation_source || "—").toUpperCase()}</span></td>
            <td>${routeBadge}</td>
          </tr>`;
        }).join("")}
      </tbody>
    </table>
    </div>`;
  el.querySelectorAll("tbody tr").forEach((tr) => {
    const go = () => (location.hash = `#/ride/${tr.dataset.id}`);
    tr.addEventListener("click", (ev) => {
      if (ev.target.closest(".route-pill")) {
        location.hash = `#/route/${tr.dataset.route}`;
        return;
      }
      go();
    });
    activateRow(tr, go);
  });
}

/* ============================================================== RIDE DETAIL */

function renderNotFound(noun, plural, backHash) {
  view.innerHTML = `<div class="empty">This ${noun} isn't in your history anymore — it may have been deleted.<br><br><a class="back-link" href="${backHash}">← back to ${plural}</a></div>`;
}

async function renderRideDetail(id) {
  view.innerHTML = `
    <div class="skeleton skeleton-instrument"></div>
    <div class="skeleton skeleton-map"></div>
    <div class="grid cols-2">
      ${'<div class="skeleton skeleton-chart"></div>'.repeat(4)}
    </div>`;
  let ride, series;
  try {
    [ride, series] = await Promise.all([
      api(`/api/rides/${id}`), api(`/api/rides/${id}/series?downsample=2500`),
    ]);
  } catch (e) {
    renderNotFound("ride", "rides", "#/rides");
    return;
  }
  const m = ride.metrics || {};
  const route = ride.route || null;
  const drift = m.cardiac_drift || null;

  view.innerHTML = `
    <div class="flex gap-8">
      <a class="back-link" href="#/rides">← back to rides</a>
      <span class="muted small">${ride.filename}</span>
      <span class="muted small">${fmtDateTime(ride.started_at)}</span>
      ${route ? `<a class="pill ${route.size > 1 ? "high" : "low"}" href="#/route/${route.id}" title="open route">${route.name} · ${route.position}/${route.size}</a>` : ""}
      <span class="ml-auto"><button class="danger" id="delete-ride">Delete</button></span>
    </div>

    <div class="instrument">
      <div class="grid cols-4">
        ${statCard(fmtKm(m.distance_m), "distance")}
        ${statCard(fmtDur(m.duration_s), "time")}
        ${statCard(fmtM(m.elevation_gain_m), "climbing · " + (ELEV_LABELS[m.elevation_source] || m.elevation_source))}
        ${statCard(m.avg_hr ? Math.round(m.avg_hr) + " bpm" : "—", "avg heart rate")}
        ${statCard(m.avg_watts != null ? Math.round(m.avg_watts) : "—", "avg power", { unit: "W", tag: "estimated", band: { lo: m.avg_watts_lo, est: m.avg_watts, hi: m.avg_watts_hi } })}
        ${statCard(m.normalized_power != null ? Math.round(m.normalized_power) : "—", "normalised power", { unit: "W", tag: "estimated", band: { lo: m.normalized_power_lo, est: m.normalized_power, hi: m.normalized_power_hi } })}
        ${statCard(m.vo2max ? m.vo2max.toFixed(1) : "—", "vo2max", { unit: "ml/kg/min", tag: "estimate" })}
        ${statCard(m.trimp ? m.trimp.toFixed(0) : "—", "TRIMP load")}
      </div>
    </div>

    <div class="card">
      <div class="spread mb-10">
        <h2 class="m-0">Route <span class="sub">drag the slider, click the map or a chart, or hit play</span></h2>
        <button class="btn" id="scrub-play">${ICON_PLAY} Play</button>
      </div>
      <div id="ride-map" class="ride-map"></div>
      <div class="scrub-row">
        <input type="range" id="scrub-slider" min="0" max="0" value="0" aria-label="Scrub through ride" />
      </div>
      <div class="readout" id="scrub-readout"></div>
    </div>

    <div class="grid cols-2">
      <div class="card"><h2>Elevation &amp; grade</h2><div id="ch-elev" class="chart med"></div></div>
      <div class="card"><h2>Heart rate</h2><div id="ch-hr" class="chart med"></div></div>
      <div class="card"><h2>Power (with uncertainty band)</h2><div id="ch-power" class="chart med"></div></div>
      <div class="card"><h2>Speed</h2><div id="ch-speed" class="chart med"></div></div>
    </div>

    <div class="card"><h2>Gradient distribution</h2><div id="ch-grade" class="chart med"></div></div>
    <div class="card" id="drift-card"></div>`;

  $("#delete-ride").addEventListener("click", async () => {
    if (!confirm("Delete this ride? This removes it from the database.")) return;
    await api(`/api/rides/${id}`, { method: "DELETE" });
    location.hash = "#/rides";
  });

  const charts = drawRideCharts(series, m);
  setupRideScrubber(series, charts);
  renderDriftCard(drift, m.has_hr);
}

function drawRideCharts(series, m) {
  const gps = series.gps || [];
  const hr = series.hr || [];
  const power = series.power || [];
  const hasDist = gps.some((p) => p.dist != null && p.dist > 0);
  const dkm = hasDist
    ? gps.map((p) => (p.dist != null ? p.dist / 1000 : null))
    : gps.map((p) => (p.idx != null ? p.idx : 0));
  const xlabel = hasDist ? "distance (km)" : "point";
  const xtick = hasDist ? ".1f" : ".0f";
  const t0 = gps.length ? gps[0].t : 0;
  const out = {};

  // Elevation + grade
  Plotly.newPlot($("#ch-elev"), [
    { x: dkm, y: gps.map((p) => p.elev), name: "elevation (m)", fill: "tozeroy",
      fillcolor: "rgba(14,122,68,.10)",
      line: { color: C.green, width: 2 },
      hovertemplate: "%{y:.0f} m<extra></extra>" },
    { x: dkm, y: gps.map((p) => (p.grade || 0) * 100), name: "grade (%)", yaxis: "y2",
      line: { color: C.amberDim, width: 1 },
      hovertemplate: "%{y:.1f}%<extra></extra>" },
  ], {
    ...PLOT_LAYOUT,
    xaxis: { ...PLOT_LAYOUT.xaxis, nticks: 8, tickformat: xtick, title: { text: xlabel, font: PLOT_FONT } },
    yaxis: { ...PLOT_LAYOUT.yaxis, title: { text: "m", font: PLOT_FONT } },
    yaxis2: { overlaying: "y", side: "right", showgrid: false, zeroline: false, ticks: "", title: { text: "%", font: PLOT_FONT } },
    legend: { orientation: "h", y: 1.15, font: PLOT_FONT },
    hovermode: "x unified",
  }, PLOT_CONFIG);
  out.elev = { el: $("#ch-elev"), xOf: (i) => dkm[i] };

  // HR
  if (hr.length) {
    Plotly.newPlot($("#ch-hr"), [
      { x: hr.map((p) => (p.t - t0) / 60), y: hr.map((p) => p.hr), name: "HR (bpm)",
        line: { color: C.red, width: 1.6 },
        hovertemplate: "%{y:.0f} bpm<extra></extra>" },
    ], { ...PLOT_LAYOUT, xaxis: { ...PLOT_LAYOUT.xaxis, nticks: 8, tickformat: ".0f", title: { text: "minutes", font: PLOT_FONT } },
         yaxis: { ...PLOT_LAYOUT.yaxis, title: { text: "bpm", font: PLOT_FONT } },
         hovermode: "x unified" }, PLOT_CONFIG);
    out.hr = { el: $("#ch-hr"), xOf: (i) => (gps[i] ? (gps[i].t - t0) / 60 : 0) };
  } else {
    $("#ch-hr").innerHTML = '<div class="empty">No heart-rate data in this ride.</div>';
  }

  // Power band — envelope between lo and hi, estimate drawn on top
  Plotly.newPlot($("#ch-power"), [
    { x: dkm, y: power.map((p) => p.watts_hi), name: "upper", mode: "lines", line: { width: 0 },
      hoverinfo: "skip", showlegend: false },
    { x: dkm, y: power.map((p) => p.watts_lo), name: "lower", mode: "lines", line: { width: 0 },
      fill: "tonexty", fillcolor: "rgba(161,109,0,0.10)",
      fillpattern: { shape: "/", size: 8, solidity: 0.35, fgcolor: "rgba(161,109,0,.32)", bgcolor: "rgba(161,109,0,.05)" },
      hoverinfo: "skip", showlegend: false },
    { x: dkm, y: power.map((p) => p.watts_est), name: "power (W)",
      line: { color: C.amberDim, width: 2 },
      customdata: power.map((p) => [p.watts_lo != null ? Math.round(p.watts_lo) : null, p.watts_hi != null ? Math.round(p.watts_hi) : null]),
      hovertemplate: "<b>%{y:.0f} W</b> · band %{customdata[0]}–%{customdata[1]} W<extra></extra>" },
  ], { ...PLOT_LAYOUT, xaxis: { ...PLOT_LAYOUT.xaxis, nticks: 8, title: { text: xlabel, font: PLOT_FONT } },
       yaxis: { ...PLOT_LAYOUT.yaxis, title: { text: "W", font: PLOT_FONT } },
       hovermode: "x unified" }, PLOT_CONFIG);
  out.power = { el: $("#ch-power"), xOf: (i) => dkm[i] };

  // Speed
  Plotly.newPlot($("#ch-speed"), [
    { x: dkm, y: gps.map((p) => (p.speed != null ? p.speed * 3.6 : null)), name: "speed (km/h)",
      line: { color: C.blue, width: 1.6 },
      hovertemplate: "%{y:.1f} km/h<extra></extra>" },
  ], { ...PLOT_LAYOUT, xaxis: { ...PLOT_LAYOUT.xaxis, nticks: 8, title: { text: xlabel, font: PLOT_FONT } },
       yaxis: { ...PLOT_LAYOUT.yaxis, title: { text: "km/h", font: PLOT_FONT } },
       hovermode: "x unified" }, PLOT_CONFIG);
  out.speed = { el: $("#ch-speed"), xOf: (i) => dkm[i] };

  // Grade distribution
  const gd = m.grade_distribution || [];
  if (gd.length) {
    Plotly.newPlot($("#ch-grade"), [
      { x: gd.map((b) => `${b.from}%`), y: gd.map((b) => b.count), type: "bar",
        marker: { color: C.green, opacity: 0.8 },
        hovertemplate: "%{y} samples at %{x}<extra></extra>" },
    ], { ...PLOT_LAYOUT, xaxis: { ...PLOT_LAYOUT.xaxis, title: { text: "grade", font: PLOT_FONT } },
         yaxis: { ...PLOT_LAYOUT.yaxis, title: { text: "samples", font: PLOT_FONT } },
         hovermode: "x" }, PLOT_CONFIG);
  }
  return out;
}

/* ---------------------------------------------- interactive scrubber */

function setupRideScrubber(series, charts) {
  const gps = series.gps || [];
  const hr = series.hr || [];
  const power = series.power || [];
  const n = gps.length;
  const t0 = gps.length ? gps[0].t : 0;
  const slider = $("#scrub-slider");
  const readout = $("#scrub-readout");
  const playBtn = $("#scrub-play");
  if (!slider || n < 2) return null;

  const hasDist = gps.some((p) => p.dist != null && p.dist > 0);
  const dkm = hasDist ? gps.map((p) => p.dist / 1000) : gps.map((_, i) => i);

  // Align HR + power samples to gps indices (two-pointer nearest by time).
  const hrAt = new Array(n).fill(null);
  let h = 0;
  for (let i = 0; i < n; i++) {
    while (h < hr.length - 1 && Math.abs(hr[h + 1].t - gps[i].t) <= Math.abs(hr[h].t - gps[i].t)) h++;
    if (hr.length && Math.abs(hr[h].t - gps[i].t) < 5) hrAt[i] = hr[h].hr;
  }
  const pwAt = new Array(n).fill(null);
  let k = 0;
  for (let i = 0; i < n; i++) {
    while (k < power.length - 1 && Math.abs(power[k + 1].t - gps[i].t) <= Math.abs(power[k].t - gps[i].t)) k++;
    if (power.length) pwAt[i] = power[k];
  }

  // Map.
  const latlngs = gps.filter((p) => p.lat != null && p.lon != null).map((p) => [p.lat, p.lon]);
  let playhead = null;
  if (latlngs.length > 1 && window.L) {
    const map = L.map($("#ride-map"), { scrollWheelZoom: false }).setView(latlngs[0], 14);
    L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>',
      maxZoom: 19, subdomains: "abcd",
    }).addTo(map);
    L.polyline(latlngs, { color: "#0e7a44", weight: 3.5, opacity: 0.95 }).addTo(map);
    L.circleMarker(latlngs[0], { radius: 5, color: "#0e7a44", weight: 2, fillColor: "#0e7a44", fillOpacity: 1 })
      .addTo(map).bindTooltip("Start");
    L.circleMarker(latlngs[latlngs.length - 1], { radius: 5, color: "#c9342b", weight: 2, fillColor: "#c9342b", fillOpacity: 1 })
      .addTo(map).bindTooltip("Finish");
    playhead = L.circleMarker(latlngs[0], { radius: 6, color: "#fff", weight: 2, fillColor: "#0e7a44", fillOpacity: 1 })
      .addTo(map);
    map.fitBounds(L.latLngBounds(latlngs), { padding: [28, 28] });
    map.on("click", (e) => {
      let best = 0, bestD = Infinity;
      for (let i = 0; i < n; i++) {
        const d = (gps[i].lat - e.latlng.lat) ** 2 + (gps[i].lon - e.latlng.lng) ** 2;
        if (d < bestD) { bestD = d; best = i; }
      }
      seek(best);
    });
  }

  function indexFromKm(x) {
    if (x == null || !dkm.length) return 0;
    let lo = 0, hi = n - 1;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (dkm[mid] < x) lo = mid + 1; else hi = mid; }
    if (lo > 0 && Math.abs(dkm[lo - 1] - x) < Math.abs(dkm[lo] - x)) lo--;
    return Math.max(0, Math.min(n - 1, lo));
  }
  function indexFromMin(x) {
    if (x == null) return 0;
    const t = t0 + x * 60;
    let lo = 0, hi = n - 1;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (gps[mid].t < t) lo = mid + 1; else hi = mid; }
    if (lo > 0 && Math.abs(gps[lo - 1].t - t) < Math.abs(gps[lo].t - t)) lo--;
    return Math.max(0, Math.min(n - 1, lo));
  }

  let i = 0;
  function seek(idx) {
    i = Math.max(0, Math.min(n - 1, idx));
    slider.value = i;
    const p = gps[i] || {};
    const pw = pwAt[i] || null;
    const read = [
      { label: "time", value: fmtDur(p.t != null ? p.t - t0 : 0) },
      { label: "distance", value: fmtKm(p.dist != null ? p.dist : (dkm[i] || 0) * 1000) },
      { label: "speed", value: fmtKmh(p.speed) },
      { label: "heart rate", value: hrAt[i] != null ? Math.round(hrAt[i]) + " bpm" : "—" },
      { label: "power", value: pw ? `${Math.round(pw.watts_est)} W` : "—", band: pw && pw.watts_hi > 0 ? `${Math.round(pw.watts_lo)}–${Math.round(pw.watts_hi)} W` : null },
      { label: "elevation", value: fmtM(p.elev) },
      { label: "grade", value: p.grade != null ? (p.grade * 100).toFixed(1) + "%" : "—" },
    ];
    readout.innerHTML = read.map((r) => `
      <div class="field">
        <div class="label">${r.label}</div>
        <div class="value">${r.value}</div>
        ${r.band ? `<div class="band-note"><span>${r.band}</span></div>` : ""}
      </div>`).join("");
    if (playhead && latlngs.length) playhead.setLatLng(latlngs[i]);
    const vline = { type: "line", x0: 0, x1: 0, y0: 0, y1: 1, yref: "paper",
      line: { color: "rgba(238,242,246,.55)", width: 1, dash: "dot" } };
    for (const key of ["elev", "power", "speed", "hr"]) {
      const c = charts[key];
      if (!c) continue;
      const x = c.xOf(i);
      if (x == null) continue;
      Plotly.relayout(c.el, { shapes: [{ ...vline, x0: x, x1: x }] });
    }
  }

  slider.max = n - 1;
  slider.addEventListener("input", () => seek(+slider.value));

  // Clicking a chart seeks to that point.
  for (const key of ["elev", "power", "speed", "hr"]) {
    const c = charts[key];
    if (!c) continue;
    c.el.on("plotly_click", (ev) => {
      const x = ev.points && ev.points[0] ? ev.points[0].x : null;
      if (x == null) return;
      seek(key === "hr" ? indexFromMin(x) : indexFromKm(x));
    });
  }

  // Playback.
  let timer = null;
  playBtn.addEventListener("click", () => {
    if (timer) {
      clearInterval(timer); timer = null;
      playBtn.innerHTML = ICON_PLAY + " Play";
      return;
    }
    if (i >= n - 1) seek(0);
    timer = setInterval(() => {
      // Stop if the ride view was torn down (navigated away mid-playback).
      if (!document.body.contains(slider)) { clearInterval(timer); timer = null; playBtn.textContent = "▶ Play"; return; }
      if (i >= n - 1) { clearInterval(timer); timer = null; playBtn.textContent = "▶ Play"; return; }
      seek(i + 6); // same speed as before, half the relayouts
    }, 80);
    playBtn.innerHTML = ICON_PAUSE + " Pause";
  });

  seek(0);
  return { seek, indexFromKm, indexFromMin };
}

function renderDriftCard(drift, hasHr) {
  const el = $("#drift-card");
  if (!el) return;
  if (!hasHr) {
    el.innerHTML = '<h2>Cardiac drift</h2><div class="empty">No heart-rate data in this ride, so drift can\'t be measured.</div>';
    return;
  }
  if (!drift) {
    el.innerHTML = `
      <h2>Cardiac drift <span class="sub">HR rise during steady effort</span></h2>
      <div class="empty">No steady-effort window found. This ride's estimated power varied too much — or the effort was too short — to separate HR drift from the noise. On gusty flat roads the estimate can't tell workload apart from wind, so we stay quiet rather than invent a number.</div>`;
    return;
  }
  const pos = drift.drift_bpm_per_hr > 0;
  const sign = pos ? "+" : "";
  el.innerHTML = `
    <h2>Cardiac drift <span class="sub">${drift.duration_min} min steady effort at ~${drift.mean_power} W est.</span></h2>
    <div class="instrument hero drift-hero">
      <div class="field"><div class="label">drift</div>    <div class="value" style="color:${pos ? "var(--amber-deep)" : "var(--blue)"}">${sign}${drift.drift_bpm_per_hr} <span class="unit">bpm/hr</span></div></div>
      <div class="field"><div class="label">relative</div><div class="value">${sign}${drift.drift_pct_per_hr}<span class="unit">%/hr</span></div></div>
      <div class="field"><div class="label">heart rate</div><div class="value">${drift.start_hr} → ${drift.end_hr}<span class="unit">bpm</span></div></div>
      <div class="field"><div class="label">fit r²</div><div class="value">${drift.r2.toFixed(2)}</div></div>
    </div>
    <div class="muted small mt-12">
      ${pos
        ? "Heart rate climbed while estimated power stayed steady — the classic drift signal. More drift at the same power usually means heat, dehydration or fatigue; less drift over the season means a fitter cardiovascular system."
        : "Heart rate actually fell during this window — the opposite of drift (recovery after a surge, or a slowing effort)."}
    </div>`;
}

/* ============================================================== ROUTES */

async function renderRoutes() {
  view.innerHTML = `<div class="card">
    <h2>Routes <span class="sub">same roads ridden more than once — the fairest comparison available</span></h2>
    <div id="routes-list"></div>
  </div>`;
  const routeList = await api("/api/routes");
  const el = $("#routes-list");
  if (!routeList.length) {
    el.innerHTML = '<div class="empty">No rides yet. Import your history and repeated routes are grouped automatically.</div>';
    return;
  }
  const rows = (r) => `
    <tr data-id="${r.id}" aria-label="Open route ${r.name}">
      <td class="mono">${r.name}</td>
      <td class="num">${r.n_rides}</td>
      <td class="mono">${fmtDate(r.first_at)}</td>
      <td class="mono">${fmtDate(r.last_at)}</td>
      <td class="num">${fmtKm(r.distance_m)}</td>
      <td class="num">${fmtM(r.gain_m)}</td>
      <td class="num">${fmtKm(r.total_distance_m)}</td>
    </tr>`;
  const repeated = routeList.filter((r) => r.n_rides > 1);
  const solo = routeList.filter((r) => r.n_rides === 1);
  el.innerHTML = `
    <div class="table-scroll">
    <table class="list">
      <thead><tr><th>Route</th><th class="num">Rides</th><th>First</th><th>Last</th><th class="num">Length</th><th class="num">Climb</th><th class="num">Total distance</th></tr></thead>
      <tbody>
        ${repeated.map(rows).join("")}
        ${solo.map(rows).join("")}
      </tbody>
    </table>
    </div>`;
  el.querySelectorAll("tbody tr").forEach((tr) => {
    const go = () => (location.hash = `#/route/${tr.dataset.id}`);
    tr.addEventListener("click", go);
    activateRow(tr, go);
  });
}

async function renderRouteDetail(id) {
  view.innerHTML = `
    <div class="skeleton skeleton-instrument"></div>
    <div class="skeleton skeleton-chart"></div>`;
  let route;
  try {
    route = await api(`/api/routes/${id}`);
  } catch (e) {
    renderNotFound("route", "routes", "#/routes");
    return;
  }
  const rides = route.rides || [];
  view.innerHTML = `
    <div class="flex gap-8">
      <a class="back-link" href="#/routes">← back to routes</a>
      <span class="mono">${route.name}</span>
      <span class="pill ${rides.length > 1 ? "high" : "low"}">×${rides.length} rides</span>
    </div>
    <div class="instrument">
      <div class="grid cols-4">
        ${statCard(`${rides.length}`, "rides on this route")}
        ${statCard(fmtKm(route.distance_m), "route length")}
        ${statCard(fmtM(route.gain_m), "climbing per ride")}
        ${statCard(rides.length ? `${fmtDate(rides[0].started_at)} → ${fmtDate(rides[rides.length - 1].started_at)}` : "—", "first → last")}
      </div>
    </div>
    <div class="card">
      <h2>Rides on this route <span class="sub">weather shown so you can read the context</span></h2>
      <div id="route-table"></div>
    </div>
    <div class="card">
      <h2>Watts at fixed HR on this route <span class="sub">context — same roads, weather varies</span></h2>
      <div id="route-chart" class="chart med"></div>
    </div>`;

  const el = $("#route-table");
  el.innerHTML = `
    <div class="table-scroll">
    <table class="list">
      <thead><tr><th class="num">#</th><th>Date</th><th class="num">Time</th><th class="num">Avg speed</th><th class="num">Avg HR</th><th class="num">Avg power</th><th class="num">NP</th>            <th class="num">W @ 140 bpm</th><th class="num">TRIMP</th><th class="num">Temp</th><th class="num">Wind</th></tr></thead>
      <tbody>
        ${rides.map((r) => `
          <tr data-id="${r.id}" aria-label="Open ride ${fmtDateTime(r.started_at)}">
            <td class="num mono">${r.route_n}</td>
            <td class="mono">${fmtDateTime(r.started_at)}</td>
            <td class="num">${fmtDur(r.duration_s)}</td>
            <td class="num">${fmtKmh(r.avg_speed_mps)}</td>
            <td class="num">${r.avg_hr ? Math.round(r.avg_hr) + " bpm" : "—"}</td>
            <td class="num">${fmtW(r.avg_watts)}</td>
            <td class="num">${fmtW(r.normalized_power)}</td>
            <td class="num">${fmtW(r.watts_140)}</td>
            <td class="num">${r.trimp ? r.trimp.toFixed(0) : "—"}</td>
            <td class="num">${r.temp_c != null ? r.temp_c.toFixed(0) + "°" : "—"}</td>
            <td class="num">${r.wind_mps != null ? r.wind_mps.toFixed(1) + " m/s" : "—"}</td>
          </tr>`).join("")}
      </tbody>
    </table>
    </div>`;
  el.querySelectorAll("tbody tr").forEach((tr) => {
    const go = () => (location.hash = `#/ride/${tr.dataset.id}`);
    tr.addEventListener("click", go);
    activateRow(tr, go);
  });

  const w140 = rides.filter((r) => r.watts_140 != null)
    .map((r) => ({ x: fmtDate(r.started_at), y: r.watts_140 }));
  const np = rides.filter((r) => r.normalized_power != null)
    .map((r) => ({ x: fmtDate(r.started_at), y: r.normalized_power }));
  if (w140.length || np.length) {
    const traces = [];
    if (w140.length) traces.push({
      x: w140.map((p) => p.x), y: w140.map((p) => p.y),
      mode: "lines+markers", name: "watts @ 140 bpm",
      line: { color: C.green, width: 2.5 }, marker: { size: 7, color: C.green },
      hovertemplate: "<b>%{y:.0f} W</b><extra></extra>",
    });
    if (np.length) traces.push({
      x: np.map((p) => p.x), y: np.map((p) => p.y),
      mode: "lines+markers", name: "normalised power",
      line: { color: C.blue, width: 2 }, marker: { size: 6, color: C.blue },
      hovertemplate: "<b>%{y:.0f} W</b><extra></extra>",
    });
    Plotly.newPlot($("#route-chart"), traces, {
      ...PLOT_LAYOUT,
      yaxis: { ...PLOT_LAYOUT.yaxis, title: { text: "watts", font: PLOT_FONT } },
      legend: { orientation: "h", y: 1.12 }, hovermode: "x unified",
    }, PLOT_CONFIG);
  } else {
    $("#route-chart").innerHTML = '<div class="empty">Not enough data yet — keep riding this route.</div>';
  }
}

function statCard(value, label, opts = {}) {
  const unit = opts.unit ? `<span class="unit">${opts.unit}</span>` : "";
  const tag = opts.tag ? `<span class="est">${opts.tag}</span>` : "";
  let band = "";
  if (opts.band && opts.band.hi > 0) {
    const { lo, est, hi } = opts.band;
    const spread = hi - lo;
    const conf = spread > 0 && est > 0 && spread / est < 0.6 ? "tight" : "wide";
    const pct = (v) => Math.max(0, Math.min(100, (v / hi) * 100));
    band = `
      <div class="band ${conf}">
        <div class="band-track">
          <i class="band-range" style="left:${pct(lo)}%;width:${Math.max(2, pct(hi) - pct(lo))}%"></i>
          <b class="band-marker" style="left:${pct(est)}%"></b>
        </div>
        <span class="band-note"><span>${fmtW(lo)} – ${fmtW(hi)}</span><b>${conf}</b></span>
      </div>`;
  }
  return `<div class="field">
    <div class="label">${label}${tag}</div>
    <div class="value">${value}${unit}</div>
    ${band}
  </div>`;
}

/* ============================================================== IMPORT */

function renderImport() {
  view.innerHTML = `
    <div class="card">
      <h2>Import Wahoo .fit files</h2>
      <div class="dropzone" id="dropzone" role="button" tabindex="0" aria-label="Import .fit files — click or drop files here">
        <div class="big">Drop .fit files here</div>
        <div class="hint mt-6">or click to browse — select many at once</div>
      </div>
      <input type="file" id="file-input" multiple accept=".fit" hidden />
      <div class="muted small mt-14">
        Each file is parsed locally, map-matched to the road network, re-elevated from
        UK lidar (or a 25 m DEM fallback), and run through the power model. Weather is
        fetched for the ride time. Nothing leaves this machine except elevation and
        weather lookups.
      </div>
    </div>
    <div class="card"><h2>Import queue</h2><div id="jobs"></div></div>`;

  const dz = $("#dropzone"), input = $("#file-input");
  dz.addEventListener("click", () => input.click());
  dz.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); }
  });
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("drag"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
  dz.addEventListener("drop", (e) => { e.preventDefault(); dz.classList.remove("drag"); handleFiles(e.dataTransfer.files); });
  input.addEventListener("change", () => handleFiles(input.files));
  loadJobs();
}

async function handleFiles(fileList) {
  const files = [];
  for (const f of fileList) {
    if (!f.name.toLowerCase().endsWith(".fit")) continue;
    const buf = await f.arrayBuffer();
    const bytes = new Uint8Array(buf);
    let bin = "";
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    files.push({ name: f.name, data: btoa(bin) });
  }
  if (!files.length) { toast("No .fit files selected"); return; }
  try {
    const res = await api("/api/import", { method: "POST", body: { files } });
    toast(`Queued ${res.jobs.length} file${res.jobs.length === 1 ? "" : "s"}`);
    pollJobs(res.jobs);
  } catch (e) {
    console.error(e);
    toast("Import failed — the engine hit an error. See the queue for details.");
  }
}

function renderJobList(jobs) {
  const el = $("#jobs");
  if (!el || !el.isConnected) return;
  el.innerHTML = jobs.map((j) => `
    <div class="job">
      <div class="row1"><b>${j.filename || j.id}</b><span class="muted small">${j.status}</span></div>
      ${j.status === "running" ? `<div class="progress-wrap"><div class="progress-fill" style="width:${j.progress}%"></div></div>` : ""}
      ${j.message ? `<div class="msg">${j.message}</div>` : ""}
      ${j.error ? `<div class="err">${j.error}</div>` : ""}
      ${j.status === "done" && j.result ? `<div class="msg">${j.result.distance_m ? fmtKm(j.result.distance_m) : ""} · ${fmtM(j.result.gain_m)} · ${fmtW(j.result.avg_watts)}</div>` : ""}
    </div>`).join("");
}

function pollJobs(jobIds) {
  const el = $("#jobs");
  if (!el) return;
  const timers = {};
  jobIds.forEach((id) => {
    timers[id] = setInterval(async () => {
      try {
        const j = await api(`/api/import/status/${id}`);
        const all = await api("/api/jobs");
        renderJobList(all.filter((x) => jobIds.includes(x.id)).reverse());
        if (["done", "error", "duplicate"].includes(j.status)) {
          clearInterval(timers[id]);
          updateSidebar();
          if (j.status === "done") toast(`Imported ${j.filename}`);
        }
      } catch (e) { /* server restart */ }
    }, 700);
  });
}

async function loadJobs() {
  const el = $("#jobs");
  if (!el) return;
  try {
    const all = await api("/api/jobs");
    if (!all.length) {
      el.innerHTML = '<div class="empty">Nothing queued yet — drop .fit files above.</div>';
      return;
    }
    renderJobList(all.slice().reverse());
    const live = all.filter((j) => !["done", "error", "duplicate"].includes(j.status)).map((j) => j.id);
    if (live.length) pollJobs(live);
  } catch (e) { /* engine offline */ }
}

async function updateSidebar() {
  try {
    const h = await api("/api/health");
    $("#sidebar-foot").textContent = `${h.rides} ride${h.rides === 1 ? "" : "s"} · stored locally`;
  } catch (e) {}
}

/* ============================================================== RECORDS */

async function renderRecords() {
  view.innerHTML = `<div class="card"><h2>Personal records</h2><div id="rec-list"></div></div>`;
  const records = await api("/api/records");
  renderRecordsList(records, $("#rec-list"));
}

/* ============================================================== PROFILE */

async function renderProfile() {
  const { rider, bike } = await api("/api/profile");
  const zones = rider.hr_zones || [];
  view.innerHTML = `
    <div class="grid cols-2">
      <div class="card">
        <h2>Rider</h2>
        <div class="form-row mb-10">
          <label class="field">Age<input id="r-age" type="number" value="${rider.age || 40}"></label>
          <label class="field">Weight (kg)<input id="r-weight" type="number" step="0.5" value="${rider.weight_kg || 75}"></label>
          <label class="field">Height (cm)<input id="r-height" type="number" step="0.5" value="${rider.height_cm || 178}"></label>
        </div>
        <div class="form-row mb-10">
          <label class="field">Resting HR<input id="r-rest" type="number" value="${rider.resting_hr || 55}"></label>
          <label class="field">Max HR<input id="r-maxhr" type="number" value="${rider.max_hr || 180}"></label>
          <label class="field">Bike type
            <select id="r-bike">
              ${["road", "gravel", "mountain", "hybrid", "tt"].map((t) => `<option ${rider.bike_type === t ? "selected" : ""}>${t}</option>`).join("")}
            </select>
          </label>
        </div>
        <h2 class="mt-16">HR zones</h2>
        <div class="hrzone-list" id="zones">${zones.map((z, i) => `
          <div class="hrzone"><b>Z${i + 1}</b>
            <input type="number" id="z${i}-lo" aria-label="Zone ${i + 1} lower bound" value="${z.lo}" min="30" max="240" step="1">
            <span>–</span>
            <input type="number" id="z${i}-hi" aria-label="Zone ${i + 1} upper bound" value="${z.hi}" min="30" max="240" step="1">
          </div>`).join("")}</div>
      </div>

      <div class="card">
        <h2>Bike</h2>
        <div class="form-row mb-10">
          <label class="field">Name<input id="b-name" value="${bike.name || "Road bike"}"></label>
          <label class="field">Mass (kg)<input id="b-mass" type="number" step="0.1" value="${bike.mass_kg || 9}"></label>
        </div>
        <div class="form-row mb-10">
          <label class="field">Rolling resistance (Crr)<input id="b-crr" type="number" step="0.0001" value="${bike.crr || 0.005}"></label>
          <label class="field">Drag area CdA (m²)<input id="b-cda" type="number" step="0.01" value="${bike.cdA || 0.35}"></label>
          <label class="field">Drivetrain efficiency<input id="b-eff" type="number" step="0.01" value="${bike.drivetrain_efficiency || 0.97}"></label>
        </div>
        <div class="muted small mb-12">
          ${bike.calibrated ? '<span class="pill confident">calibrated</span> ' : '<span class="pill context">defaults</span> '}
          Crr and CdA can be tuned by the calibration procedures on suitable rides.
        </div>
        <button class="primary" id="save-profile">Save profile</button>
      </div>
    </div>
    <div class="card"><h2>Calibration history</h2><div id="calib-list"></div></div>`;

  const PROFILE_FIELDS = ["r-age", "r-weight", "r-height", "r-rest", "r-maxhr", "b-mass", "b-crr", "b-cda", "b-eff",
    "z0-lo", "z0-hi", "z1-lo", "z1-hi", "z2-lo", "z2-hi", "z3-lo", "z3-hi", "z4-lo", "z4-hi"];
  PROFILE_FIELDS.forEach((id) => $(`#${id}`).addEventListener("input", () => $(`#${id}`).classList.remove("invalid")));

  $("#save-profile").addEventListener("click", async () => {
    const num = (sel) => { const v = +$(sel).value; return Number.isFinite(v) ? v : NaN; };
    const checks = [
      ["r-age", num("#r-age") >= 13 && num("#r-age") <= 100, "Age"],
      ["r-weight", num("#r-weight") >= 30 && num("#r-weight") <= 200, "Weight"],
      ["r-height", num("#r-height") >= 100 && num("#r-height") <= 250, "Height"],
      ["r-rest", num("#r-rest") >= 30 && num("#r-rest") <= 120, "Resting HR"],
      ["r-maxhr", num("#r-maxhr") > num("#r-rest") && num("#r-maxhr") <= 240, "Max HR"],
      ["b-mass", num("#b-mass") > 0 && num("#b-mass") <= 50, "Bike mass"],
      ["b-crr", num("#b-crr") > 0 && num("#b-crr") <= 0.05, "Rolling resistance"],
      ["b-cda", num("#b-cda") > 0 && num("#b-cda") <= 1.5, "Drag area"],
      ["b-eff", num("#b-eff") > 0 && num("#b-eff") <= 1, "Drivetrain efficiency"],
    ];
    let bad = false;
    checks.forEach(([id, ok]) => {
      $(`#${id}`).classList.toggle("invalid", !ok);
      if (!ok) bad = true;
    });
    const zones = [0, 1, 2, 3, 4].map((i) => {
      const lo = num(`#z${i}-lo`), hi = num(`#z${i}-hi`);
      const ok = Number.isFinite(lo) && Number.isFinite(hi) && lo < hi;
      $(`#z${i}-lo`).classList.toggle("invalid", !ok);
      $(`#z${i}-hi`).classList.toggle("invalid", !ok);
      if (!ok) bad = true;
      return { lo, hi };
    });
    if (bad) { toast("Check the highlighted fields — numbers need to be in range and zones ascending."); return; }
    const payload = {
      rider: {
        age: num("#r-age"), weight_kg: num("#r-weight"),
        height_cm: num("#r-height"), resting_hr: num("#r-rest"),
        max_hr: num("#r-maxhr"), bike_type: $("#r-bike").value,
        hr_zones: zones,
      },
      bike: {
        id: bike.id, name: $("#b-name").value, mass_kg: num("#b-mass"),
        crr: num("#b-crr"), cdA: num("#b-cda"),
        drivetrain_efficiency: num("#b-eff"), calibrated: bike.calibrated,
      },
    };
    await api("/api/profile", { method: "PUT", body: payload });
    toast("Profile saved");
  });

  const calibs = await api("/api/calibrations");
  const el = $("#calib-list");
  if (!calibs.length) {
    el.innerHTML = '<div class="empty">No calibrations yet. They run automatically on rides with suitable climbs or descents.</div>';
  } else {
    el.innerHTML = `<div class="table-scroll"><table class="list"><thead><tr><th>Ride</th><th>Type</th><th>Crr</th><th>CdA</th><th>R²</th><th>Segments</th></tr></thead><tbody>
      ${calibs.map((c) => `<tr>
        <td>${c.filename || c.ride_id}</td>
        <td><span class="pill ${c.type === "loop" ? "high" : "med"}">${c.type}</span></td>
        <td>${c.params.crr ? c.params.crr.toFixed(4) : "—"}</td>
        <td>${c.params.cdA ? c.params.cdA.toFixed(2) : "—"}</td>
        <td>${c.r2 != null ? c.r2.toFixed(2) : "—"}</td>
        <td>${c.params.n_segments || "—"}</td>
      </tr>`).join("")}
    </tbody></table></div>`;
  }
}

/* ------------------------------------------------------------------ boot */
init();
