"use strict";

/* VeloTrack browser experience
   The browser owns composition and interaction. The domain remains in Python;
   this file owns one coherent surface for pages, plots, and Ride replay. */

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const view = $("#view");

const state = {
  pageToken: 0,
  charts: new Map(),
  map: null,
  playback: null,
  cursorFrame: null,
  cursor: null,
  overview: null,
  abortController: null,
  jobPoller: null,
  jobPollerIds: new Set(),
  jobStatusSignature: "",
};

/* Chart palette reads the Route Atlas tokens so CSS and SVG never drift apart. */
const GRAPH_CSS = (name, fallback) => {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
};
function hexToRgba(hex, alpha) {
  const cleaned = String(hex || "").trim().replace("#", "");
  if (!/^[0-9a-f]{3}$/i.test(cleaned) && !/^[0-9a-f]{6}$/i.test(cleaned)) return hex;
  const full = cleaned.length === 3 ? cleaned.split("").map((c) => c + c).join("") : cleaned;
  const number = parseInt(full, 16);
  return `rgba(${(number >> 16) & 255},${(number >> 8) & 255},${number & 255},${alpha})`;
}
const GRAPH = {
  green: GRAPH_CSS("--green", "#1b6f4d"),
  greenDeep: GRAPH_CSS("--green-deep", "#11553a"),
  greenSoft: hexToRgba(GRAPH_CSS("--green", "#1b6f4d"), .14),
  blue: GRAPH_CSS("--blue", "#3b6ea5"),
  orange: GRAPH_CSS("--orange", "#a8691f"),
  /* Bands are the honest envelope of every estimate, so their fill needs real
     presence — a faint ghost reads as a rendering bug, not an uncertainty. */
  orangeSoft: hexToRgba(GRAPH_CSS("--orange", "#a8691f"), .22),
  ink: GRAPH_CSS("--ink-soft", "#34423a"),
  muted: GRAPH_CSS("--muted", "#55655c"),
  cursor: GRAPH_CSS("--graph-cursor", "#123a28"),
};
let graphInstanceId = 0;

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

/* ------------------------------------------------------------------ data */

async function api(path, options = {}) {
  const config = { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } };
  config.signal = options.signal || state.abortController?.signal;
  if (options.body && typeof options.body !== "string") config.body = JSON.stringify(options.body);
  const response = await fetch(path, config);
  if (!response.ok) {
    let detail = "";
    try { detail = String((await response.json()).error || "").trim(); } catch (_) {}
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return response.json();
}

async function loadOverview() {
  try {
    return await api("/api/overview");
  } catch (primaryError) {
    if (primaryError?.name === "AbortError") throw primaryError;
    const requests = [
      ["rides", "/api/rides"],
      ["fitness", "/api/trends/fitness"],
      ["records", "/api/records"],
      ["power", "/api/trends/power"],
      ["drift", "/api/trends/cardiac"],
      ["wattsHr", "/api/trends/watts_hr"],
    ];
    const results = await Promise.allSettled(requests.map(([, path]) => api(path)));
    const failures = Object.fromEntries(results.flatMap((result, index) => result.status === "rejected" ? [[requests[index][0], result.reason]] : []));
    const value = (index, fallback) => results[index].status === "fulfilled" ? results[index].value : fallback;
    const rides = value(0, null);
    if (!rides) throw primaryError;
    return {
      rides,
      fitness: value(1, { points: [] }),
      records: value(2, []),
      power: value(3, { series: {}, curves: [] }),
      drift: value(4, { points: [] }),
      wattsHr: value(5, { fixed_hrs: [], series: {} }),
      failures,
    };
  }
}

/* ------------------------------------------------------------------ format */

const dateFormat = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" });
const dateTimeFormat = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const timeFormat = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
}
function finiteValue(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}
function validDate(unix) {
  const value = finiteValue(unix);
  if (value == null || value <= 0) return null;
  const date = new Date(value * 1000);
  return Number.isFinite(date.getTime()) ? date : null;
}
function fmtDate(unix) { const date = validDate(unix); return date ? dateFormat.format(date) : "—"; }
function fmtDateTime(unix) { const date = validDate(unix); return date ? dateTimeFormat.format(date) : "—"; }
function fmtTime(unix) { const date = validDate(unix); return date ? timeFormat.format(date) : "—"; }
function fmtDuration(seconds) {
  const numeric = finiteValue(seconds);
  if (numeric == null) return "—";
  const value = Math.max(0, Math.round(numeric));
  const h = Math.floor(value / 3600), m = Math.floor((value % 3600) / 60), s = value % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}\u2009h` : `${m}:${String(s).padStart(2, "0")}`;
}
function fmtElapsed(seconds) {
  const numeric = finiteValue(seconds);
  if (numeric == null) return "—";
  const value = Math.max(0, Math.round(numeric));
  const h = Math.floor(value / 3600), m = Math.floor((value % 3600) / 60), s = value % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}
function fmtDistance(meters) {
  const numeric = finiteValue(meters);
  if (numeric == null) return "—";
  const km = numeric / 1000;
  return `${km >= 10 ? km.toFixed(1) : km.toFixed(2)} km`;
}
function fmtMeters(meters) { const numeric = finiteValue(meters); return numeric == null ? "—" : `${Math.round(numeric)} m`; }
function fmtSpeed(mps) { const numeric = finiteValue(mps); return numeric == null ? "—" : `${(numeric * 3.6).toFixed(1)} km/h`; }
function fmtWatts(watts) { const numeric = finiteValue(watts); return numeric == null ? "—" : `${Math.round(numeric)} W`; }
function fmtTemp(temp) { const numeric = finiteValue(temp); return numeric == null ? "—" : `${Math.round(numeric)}°`; }
function fmtWind(mps) { const numeric = finiteValue(mps); return numeric == null ? "—" : `${numeric.toFixed(1)} m/s`; }
function fmtNumber(value, digits = 2) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "";
  const normalized = Math.abs(number) < 10 ** (-digits - 1) ? 0 : number;
  return normalized.toFixed(digits).replace(/(\.\d*?[1-9])0+$/, "$1").replace(/\.0+$/, "");
}
function recordDisplay(record) {
  if (record.value_display) return record.value_display;
  const value = record.value;
  if (record.metric === "distance_m") return fmtDistance(value);
  if (["avg_speed_mps", "max_speed_mps"].includes(record.metric)) return fmtSpeed(value);
  if (["avg_watts"].includes(record.metric)) return fmtWatts(value);
  if (record.metric === "vo2max") {
    const numeric = finiteValue(value);
    return numeric == null ? "—" : `${numeric.toFixed(1)} ml/kg/min`;
  }
  return fmtMeters(value);
}

/* ------------------------------------------------------------------ icons */

function icon(name, className = "") {
  return `<svg class="${className}" aria-hidden="true"><use href="#i-${name}"></use></svg>`;
}
function pageHeader(title, action = "") {
  return `<div class="page-head"><div class="page-head__copy"><h1>${esc(title)}</h1></div>${action ? `<div class="page-head__actions">${action}</div>` : ""}</div>`;
}
function emptyState(title, message, action = "") {
  return `<div class="empty-state"><div class="empty-state__mark" aria-hidden="true">${icon("activity")}</div><h2>${esc(title)}</h2><p>${esc(message)}</p>${action}</div>`;
}
function retryAction(label = "Try again", overviewKey = "") {
  const retryAttribute = overviewKey ? `data-retry-overview="${esc(overviewKey)}"` : "data-retry-page";
  return `<button type="button" class="button button--quiet button--small" ${retryAttribute}>${esc(label)}</button>`;
}
function errorState(title, message, action = "") {
  return `<div class="error-state" role="alert"><div class="error-state__mark" aria-hidden="true">!</div><div class="error-state__body"><strong>${esc(title)}</strong><p>${esc(message)}</p>${action}</div></div>`;
}
/* ------------------------------------------------------------------ layout */

function currentRoute() {
  const [name = "dashboard", param] = location.hash.replace(/^#\//, "").split("/");
  return { name: routes[name] ? name : "dashboard", param };
}

function startPage() {
  state.pageToken += 1;
  state.abortController?.abort();
  state.abortController = new AbortController();
  stopPlayback();
  if (state.jobPoller) clearInterval(state.jobPoller);
  state.jobPoller = null;
  state.jobPollerIds.clear();
  state.jobStatusSignature = "";
  if (state.map) { state.map.remove(); state.map = null; }
  state.charts.forEach((chart) => { try { chart.destroy?.(); } catch (_) { /* already removed */ } });
  state.charts.clear();
  state.cursor = null;
  if (state.cursorFrame) cancelAnimationFrame(state.cursorFrame);
  state.cursorFrame = null;
  return state.pageToken;
}
function pageIsCurrent(token) { return token === state.pageToken; }
function closeMobileNav() {
  const menu = $("#mobile-nav"), toggle = $("#mobile-nav-toggle");
  if (!menu || !toggle) return;
  menu.hidden = true;
  toggle.setAttribute("aria-expanded", "false");
  toggle.innerHTML = `${icon("menu")}<span>Open navigation</span>`;
}
function setupMobileNav() {
  const menu = $("#mobile-nav"), toggle = $("#mobile-nav-toggle");
  if (!menu || !toggle) return;
  toggle.addEventListener("click", (event) => {
    event.stopPropagation();
    const open = menu.hidden;
    menu.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
    toggle.innerHTML = `${icon(open ? "close" : "menu")}<span>${open ? "Close navigation" : "Open navigation"}</span>`;
  });
  menu.addEventListener("click", () => closeMobileNav());
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeMobileNav(); });
  document.addEventListener("click", (event) => {
    if (!menu.hidden && !menu.contains(event.target) && !toggle.contains(event.target)) closeMobileNav();
  });
}
function setHeader(name) {
  const navName = name === "ride" ? "rides" : name === "route" ? "routes" : name;
  $$(".primary-nav a, .mobile-nav a").forEach((link) => {
    const active = link.dataset.nav === navName;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
  });
}

let lastFocusedHeading = null;
function focusPageHeading() {
  const heading = view.querySelector("h1");
  if (!heading) return false;
  heading.setAttribute("tabindex", "-1");
  if (heading !== lastFocusedHeading) {
    lastFocusedHeading = heading;
    heading.focus({ preventScroll: true });
  }
  return true;
}

function navigate() {
  const { name, param } = currentRoute();
  const pageNames = { dashboard: "Overview", rides: "Rides", routes: "Routes", route: "Route comparison", ride: "Ride analysis", records: "Records", import: "Import rides", profile: "Profile & bike" };
  document.title = `${pageNames[name] || "Overview"} · VeloTrack`;
  closeMobileNav();
  setHeader(name);
  lastFocusedHeading = null;
  const token = startPage();
  const renderer = routes[name] || renderDashboard;
  renderer(param, token);
  /* Focus the page heading when it lands — including when an async data fill
     replaces the first h1 node. The h1 is only ever swapped by page-level
     re-renders, so re-asserting focus here never steals it from user input. */
  focusPageHeading();
  const observer = new MutationObserver(() => focusPageHeading());
  observer.observe(view, { childList: true, subtree: true });
  setTimeout(() => observer.disconnect(), 4000);
}

window.addEventListener("hashchange", navigate);

/* ------------------------------------------------------------------ graphs */

/*
 * Graph grammar — instrument (rebuilt for flagship polish).
 *
 * THESIS: data reads at a glance; the frame disappears. One faint horizontal
 *   hairline per y-tick, no axis box, no tick marks, sans tabular labels, one
 *   accent per question, dark tooltip cards, a crisp cursor line, and a single
 *   500 ms entrance that makes filter changes feel intentional.
 * OWN-WORLD: white plates, a redraw-friendly SVG scene, drawn-in lines, bars
 *   that rise from their baseline, and a Material-dark tooltip.
 * STORY: hover or seek and the whole surface answers with a highlighted point.
 * FORM: replacement of the graph rendering layer inside the Route Atlas shell
 *   — the SVG-scene architecture and single-cursor-line replay stay; the
 *   grammar, frame, tooltip, hover, and motion are replaced.
 * FINISH: unreviewed and undocumented is unfinished; this build ends with the
 *   finish review, the verdict, DESIGN.md, and every shipping raster carrying
 *   its provenance.
 */

const GRAPH_FORMATS = {
  watts: (value) => `${Math.round(value)} W`,
  bpm: (value) => `${Math.round(value)} bpm`,
  speed: (value) => `${Number(value).toFixed(1)} km/h`,
  percent: (value) => `${Number(value).toFixed(1)}%`,
  meters: (value) => `${Math.round(value)} m`,
  decimal: (value) => Number(value).toFixed(1),
  integer: (value) => `${Math.round(value)}`,
};

function finiteGraphValue(value) {
  return value != null && Number.isFinite(Number(value));
}
function graphNumber(value) {
  return value instanceof Date ? value.getTime() / 1000 : Number(value);
}
function graphFormat(value, format) {
  if (!finiteGraphValue(value)) return "—";
  if (typeof format === "function") return format(Number(value));
  if (GRAPH_FORMATS[format]) return GRAPH_FORMATS[format](Number(value));
  const number = Number(value);
  return Number.isInteger(number) ? `${number}` : number.toFixed(Math.abs(number) < 10 ? 1 : 0);
}
function graphNiceTicks(min, max, count = 5) {
  const range = Math.max(Math.abs(max - min), 1e-9);
  const rough = range / Math.max(1, count - 1);
  const power = 10 ** Math.floor(Math.log10(rough));
  const normalized = rough / power;
  const multiplier = normalized >= 7.5 ? 10 : normalized >= 3.5 ? 5 : normalized >= 1.8 ? 2 : 1;
  const step = multiplier * power;
  const first = Math.floor(min / step) * step;
  const last = Math.ceil(max / step) * step;
  const ticks = [];
  for (let value = first, i = 0; value <= last + step * .001 && i < 30; value += step, i += 1) {
    ticks.push(Math.abs(value) < step * .0001 ? 0 : Number(value.toFixed(10)));
  }
  return ticks.length > 1 ? ticks : [min, max];
}
/* Real 11px axis-text width, measured once per draw against the same canvas
   font the SVG uses, so margins and label gutters never rely on guesswork. */
const graphTextWidth = (() => {
  let context = null;
  return (text) => {
    if (!context) context = document.createElement("canvas").getContext("2d");
    context.font = "500 11px 'IBM Plex Sans', Inter, system-ui, sans-serif";
    return context.measureText(String(text)).width;
  };
})();
/* Day-aligned ticks (local midnight) for time axes that span more than a day
   and a half, so multi-ride trends read as clean calendar dates instead of
   arbitrary seconds-of-the-epoch landing mid-afternoon. Falls back to null
   for intra-day spans, where the caller keeps second-granularity ticks. */
function graphTimeTicks(min, max, count = 6) {
  const span = max - min;
  if (span <= 0 || span < 1.5 * 86400) return null;
  const days = graphNiceTicks(0, span / 86400, count).filter((value) => value > 0);
  if (days.length < 2) return null;
  const stepDays = Math.max(1, days[1] - days[0]);
  const offset = new Date(min * 1000).getTimezoneOffset() * 60;
  const step = stepDays * 86400;
  const first = Math.ceil((min + offset) / step) * step - offset;
  const ticks = [];
  for (let value = first, i = 0; value <= max + step * .01 && i < 40; value += step, i += 1) ticks.push(value);
  return ticks.length > 1 ? ticks : null;
}
function graphQuantile(values, fraction) {
  if (!values.length) return 0;
  const sorted = values.slice().sort((a, b) => a - b);
  const position = (sorted.length - 1) * Math.max(0, Math.min(1, fraction));
  const lower = Math.floor(position), upper = Math.ceil(position);
  if (lower === upper) return sorted[lower];
  return sorted[lower] + (sorted[upper] - sorted[lower]) * (position - lower);
}
function graphAxisDomain(series, axis, options = {}) {
  const config = options || {};
  const values = [];
  series.filter((item) => (item.axis || "left") === axis).forEach((item) => {
    (item.values || []).forEach((value) => { if (finiteGraphValue(value)) values.push(Number(value)); });
    if (item.band) {
      [...(item.band.lo || []), ...(item.band.hi || [])].forEach((value) => { if (finiteGraphValue(value)) values.push(Number(value)); });
    }
  });
  if (!values.length) return { min: 0, max: 1, ticks: [0, 1] };
  const robust = Boolean(config.robust) && values.length > 8;
  let min = robust ? graphQuantile(values, config.robustLow ?? .02) : Math.min(...values);
  let max = robust ? graphQuantile(values, config.robustHigh ?? .98) : Math.max(...values);
  const isBar = series.some((item) => (item.axis || "left") === axis && item.type === "bar");
  if (config.min != null) min = Number(config.min);
  if (config.max != null) max = Number(config.max);
  if (config.includeZero || isBar) {
    min = Math.min(0, min);
    max = Math.max(0, max);
  }
  if (min === max) {
    const pad = Math.max(Math.abs(min) * .08, 1);
    min -= pad;
    max += pad;
  } else if (!config.includeZero && !isBar) {
    const pad = (max - min) * (robust ? .06 : .08);
    min -= pad;
    max += pad;
  }
  const ticks = graphNiceTicks(min, max, config.ticks || 5);
  return { min: ticks[0], max: ticks[ticks.length - 1], ticks };
}
function graphXLabel(value, xSpec, min, max) {
  if (xSpec.type === "category") return String(xSpec.labels?.[Number(value)] ?? "");
  if (xSpec.type === "time") {
    const date = new Date(Number(value) * 1000);
    const span = max - min;
    /* Below a day and a half, ticks carry clock time; at or above it they snap
       to local midnight and read as plain calendar dates (see graphTimeTicks). */
    if (span < 1.5 * 86400) return date.toLocaleDateString(undefined, { day: "numeric", month: "short" }) + ` ${date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}`;
    return date.toLocaleDateString(undefined, { day: "numeric", month: "short" });
  }
  if (xSpec.format) return graphFormat(value, xSpec.format);
  const number = Number(value);
  return Number.isInteger(number) ? `${number}` : number.toFixed(Math.abs(number) < 10 ? 1 : 0);
}
function graphDisplayIndices(values, xValues, maxPoints = 800) {
  const length = Math.min(values?.length || 0, xValues?.length || 0);
  if (length <= maxPoints) return Array.from({ length }, (_, index) => index);
  const buckets = Math.max(1, Math.floor(maxPoints / 2));
  const indices = new Set([0, length - 1]);
  for (let bucket = 0; bucket < buckets; bucket += 1) {
    const start = Math.floor(bucket * length / buckets);
    const end = Math.min(length, Math.floor((bucket + 1) * length / buckets));
    let minIndex = -1, maxIndex = -1, min = Infinity, max = -Infinity;
    for (let index = start; index < end; index += 1) {
      const value = Number(values[index]);
      if (!Number.isFinite(value)) continue;
      if (value < min) { min = value; minIndex = index; }
      if (value > max) { max = value; maxIndex = index; }
    }
    if (minIndex >= 0) indices.add(minIndex);
    if (maxIndex >= 0) indices.add(maxIndex);
  }
  return [...indices].sort((a, b) => a - b);
}
function graphCurvedPath(points, tension = .65) {
  if (points.length < 3) return points.map(([x, y], index) => `${index ? "L" : "M"}${x.toFixed(2)} ${y.toFixed(2)}`).join(" ");
  let d = `M${points[0][0].toFixed(2)} ${points[0][1].toFixed(2)} `;
  for (let i = 0; i < points.length - 1; i += 1) {
    const p0 = points[Math.max(0, i - 1)], p1 = points[i], p2 = points[i + 1], p3 = points[Math.min(points.length - 1, i + 2)];
    const c1x = p1[0] + (p2[0] - p0[0]) / 6 * tension, c1y = p1[1] + (p2[1] - p0[1]) / 6 * tension;
    const c2x = p2[0] - (p3[0] - p1[0]) / 6 * tension, c2y = p2[1] - (p3[1] - p1[1]) / 6 * tension;
    d += `C${c1x.toFixed(2)} ${c1y.toFixed(2)} ${c2x.toFixed(2)} ${c2y.toFixed(2)} ${p2[0].toFixed(2)} ${p2[1].toFixed(2)} `;
  }
  return d.trim();
}
function graphLinePath(item, xValues, xToPx, yToPx, indices, smooth) {
  const sequence = indices || (item.values || []).map((_, index) => index);
  const chunks = [];
  let current = [];
  const flush = () => { if (current.length) { chunks.push(current); current = []; } };
  sequence.forEach((index) => {
    const value = item.values?.[index];
    if (finiteGraphValue(value) && finiteGraphValue(xValues[index])) current.push([xToPx(xValues[index], index), yToPx(value, item.axis || "left")]);
    else flush();
  });
  flush();
  return chunks.map((chunk) => smooth && chunk.length >= 3 ? graphCurvedPath(chunk) : chunk.map(([x, y], index) => `${index ? "L" : "M"}${x.toFixed(2)} ${y.toFixed(2)}`).join(" ")).join(" ");
}
function graphBandPath(series, xValues, xToPx, yToPx, indices = null) {
  const paths = [];
  let upper = [], lower = [];
  const flush = () => {
    if (upper.length >= 2) {
      /* upper runs left→right; lower was pushed in the same order, so reverse
         it once at flush time instead of unshifting per point (O(n²)). */
      paths.push(`M${upper.map(([x, y]) => `${x.toFixed(2)} ${y.toFixed(2)}`).join(" L")} L${lower.reverse().map(([x, y]) => `${x.toFixed(2)} ${y.toFixed(2)}`).join(" L")} Z`);
    } else if (upper.length === 1) {
      /* A lone sample still carries its band: draw a narrow vertical range
         bar so a single-ride trend never silently loses its uncertainty. */
      const [x, hi] = upper[0];
      const [, lo] = lower[0];
      const half = 3;
      paths.push(`M${(x - half).toFixed(2)} ${hi.toFixed(2)} L${(x + half).toFixed(2)} ${hi.toFixed(2)} L${(x + half).toFixed(2)} ${lo.toFixed(2)} L${(x - half).toFixed(2)} ${lo.toFixed(2)} Z`);
    }
    upper = []; lower = [];
  };
  const sequence = indices || (series.band?.hi || []).map((_, index) => index);
  sequence.forEach((index) => {
    const value = series.band?.hi?.[index];
    if (finiteGraphValue(value) && finiteGraphValue(series.band?.lo?.[index]) && finiteGraphValue(xValues[index])) {
      upper.push([xToPx(xValues[index], index), yToPx(value)]);
      lower.push([xToPx(xValues[index], index), yToPx(series.band.lo[index])]);
    } else flush();
  });
  flush();
  return paths.join(" ");
}
function graphAreaPath(series, xValues, xToPx, yToPx, baseline, indices = null) {
  const points = [];
  const sequence = indices || (series.values || []).map((_, index) => index);
  sequence.forEach((index) => {
    const value = series.values?.[index];
    if (finiteGraphValue(value) && finiteGraphValue(xValues[index])) points.push([xToPx(xValues[index], index), yToPx(value)]);
  });
  if (points.length < 2) return "";
  return `M${points.map(([x, y]) => `${x.toFixed(2)} ${y.toFixed(2)}`).join(" L")} L${points[points.length - 1][0].toFixed(2)} ${baseline.toFixed(2)} L${points[0][0].toFixed(2)} ${baseline.toFixed(2)} Z`;
}

function renderGraph(target, spec) {
  const el = typeof target === "string" ? $(target) : target;
  if (!el) return null;
  const previous = state.charts.get(el.id);
  previous?.destroy?.();
  state.charts.delete(el.id);
  if (!spec?.x?.values?.length || !spec.series?.some((item) => (item.values || []).some(finiteGraphValue))) {
    graphEmpty(el, "Not enough data yet", "Keep riding and this view will become more useful.");
    return null;
  }

  const graph = {
    el,
    spec,
    uid: ++graphInstanceId,
    callbacks: [],
    cursorX: null,
    cursorLine: null,
    hoverLine: null,
    tooltip: null,
    hit: null,
    resizeObserver: null,
    xValues: spec.x.values,
    destroy() {
      this.resizeObserver?.disconnect();
      this.callbacks = [];
      if (state.charts.get(el.id) === this) state.charts.delete(el.id);
    },
    xOf(index) { return this.xValues[Math.max(0, Math.min(this.xValues.length - 1, index))]; },
    onClick(callback) { if (typeof callback === "function") this.callbacks.push(callback); },
    setCursor(value) {
      this.cursorX = value;
      if (!this.cursorLine || !this._xToPx || !finiteGraphValue(value)) return;
      const x = this._xToPx(value);
      this.cursorLine.setAttribute("x1", x);
      this.cursorLine.setAttribute("x2", x);
      this.cursorLine.style.display = "block";
      const index = this._nearestIndex?.(x) ?? 0;
      this.cursorDots?.forEach((dot) => {
        const item = this._cursorSeries?.[Number(dot.dataset.cursorSeries)];
        const point = item?.values?.[index];
        if (!item || !finiteGraphValue(point)) { dot.style.display = "none"; return; }
        dot.setAttribute("cx", x);
        dot.setAttribute("cy", this._cursorY(point, item.axis || "left"));
        dot.style.display = "block";
      });
      /* A pinned tooltip rides along with the shared cursor, so click-to-pin
         keeps reading out values while the replay scrubs past it. */
      if (this.pinned && this._showIndex) this._showIndex(index);
    },
  };
  state.charts.set(el.id, graph);
  el.innerHTML = `<div class="graph-shell"><svg class="graph-svg" role="img" aria-label="${esc(spec.ariaLabel || "Interactive graph")}" preserveAspectRatio="none"></svg><div class="graph-legend" aria-hidden="true"></div><div class="graph-tooltip" hidden></div></div>`;
  graph.tooltip = $(".graph-tooltip", el);
  graph.legendEl = $(".graph-legend", el);
  const svg = $(".graph-svg", el);
  graph.svg = svg;

  function draw() {
    if (state.charts.get(el.id) !== graph) return;
    const width = Math.max(280, el.clientWidth || 640);
    const height = Math.max(180, el.clientHeight || 280);
    const xSpec = spec.x;
    const category = xSpec.type === "category";
    const xRaw = category ? xSpec.values.map((_, index) => index) : xSpec.values.map(graphNumber).filter(Number.isFinite);
    if (!xRaw.length) return;
    const transformedX = xRaw.map((value) => xSpec.type === "log" ? Math.log10(Math.max(value, .0001)) : value);
    let xMin = category ? -.5 : Math.min(...transformedX), xMax = category ? xRaw.length - .5 : Math.max(...transformedX);
    if (xMin === xMax) { xMin -= .5; xMax += .5; }
    const hasRight = spec.series.some((item) => item.axis === "right");
    const namedSeries = spec.series.filter((item) => item.name && item.legend !== false);
    const legendItems = spec.legend === false || (spec.legend == null && namedSeries.length < 2) ? [] : namedSeries;
    const leftAxis = graphAxisDomain(spec.series, "left", spec.y);
    const rightAxis = hasRight ? graphAxisDomain(spec.series, "right", spec.yRight) : null;
    /* Reserve measured space for the rotated axis titles AND the tick labels
       in the same gutter, so the two can never overlap (they did before: the
       vertical title crossed the mid-chart label on every chart). */
    const leftWidest = leftAxis.ticks.reduce((widest, tick) => Math.max(widest, graphTextWidth(graphFormat(tick, spec.y?.format))), 0);
    const rightWidest = rightAxis ? rightAxis.ticks.reduce((widest, tick) => Math.max(widest, graphTextWidth(graphFormat(tick, spec.yRight?.format))), 0) : 0;
    const margin = {
      left: Math.max(46, leftWidest + 12),
      right: rightAxis ? Math.max(38, rightWidest + 14) : 14,
      top: legendItems.length ? 42 : 14,
      bottom: 27,
    };
    const yLabelX = margin.left - 12;
    const rightLabelX = width - margin.right + 12;
    const plotWidth = Math.max(80, width - margin.left - margin.right);
    const plotHeight = Math.max(80, height - margin.top - margin.bottom);
    const bottom = margin.top + plotHeight;
    const xToPx = (value, index = 0) => {
      const raw = category ? index : graphNumber(value);
      const transformed = xSpec.type === "log" ? Math.log10(Math.max(raw, .0001)) : raw;
      return margin.left + ((transformed - xMin) / (xMax - xMin)) * plotWidth;
    };
    const yToPx = (value, axis = "left") => {
      const domain = axis === "right" ? rightAxis : leftAxis;
      return bottom - ((Number(value) - domain.min) / (domain.max - domain.min)) * plotHeight;
    };
    const renderLimit = Math.max(240, Math.min(900, Math.round(plotWidth * 2)));
    const displayIndices = new Map(spec.series.map((item) => [item, graphDisplayIndices(item.values || item.band?.hi || [], spec.x.values, renderLimit)]));
    /* Value → first-index lookup, so the replay cursor never rescans the whole
       series for every frame it paints. */
    const valueIndex = new Map();
    spec.x.values.forEach((value, index) => { const key = graphNumber(value); if (!valueIndex.has(key)) valueIndex.set(key, index); });
    graph._xToPx = (value) => xToPx(value, valueIndex.get(graphNumber(value)) ?? 0);
    graph._nearestIndex = (px) => {
      let nearest = 0, distance = Infinity;
      spec.x.values.forEach((value, index) => {
        if (!category && !finiteGraphValue(value)) return;
        const delta = Math.abs(xToPx(value, index) - px);
        if (delta < distance) { nearest = index; distance = delta; }
      });
      return nearest;
    };
    graph._validIndex = (candidate, direction = 1) => {
      const last = spec.x.values.length - 1;
      let index = Math.max(0, Math.min(last, Math.round(candidate)));
      const valid = (value) => category || finiteGraphValue(value);
      if (valid(spec.x.values[index])) return index;
      for (let step = 1; step <= last; step += 1) {
        const next = index + step * direction;
        if (next >= 0 && next <= last && valid(spec.x.values[next])) return next;
      }
      for (let step = 1; step <= last; step += 1) {
        const next = index - step * direction;
        if (next >= 0 && next <= last && valid(spec.x.values[next])) return next;
      }
      return 0;
    };
    graph._formatTooltip = (index) => {
      const xValue = spec.x.values[index];
      let head = graphXLabel(graphNumber(xValue), xSpec, Math.min(...xRaw), Math.max(...xRaw));
      if (xSpec.type !== "time" && xSpec.type !== "category") {
        const unit = String(xSpec.label || "").match(/\(([^)]+)\)/)?.[1] || (xSpec.label === "minutes" ? "min" : "");
        if (unit && !head.includes(unit)) head = `${head} ${unit}`;
      }
      const rows = spec.series.filter((item) => item.tooltip !== false).map((item) => {
        const value = item.values?.[index];
        if (!finiteGraphValue(value)) return "";
        const band = item.band && finiteGraphValue(item.band.lo?.[index]) && finiteGraphValue(item.band.hi?.[index])
          ? `<span class="graph-tooltip__band">${esc(graphFormat(item.band.lo[index], item.format))} – ${esc(graphFormat(item.band.hi[index], item.format))}</span>` : "";
        const marker = Array.isArray(item.pointColors) ? item.pointColors[index] : item.color || GRAPH.green;
        return `<div class="graph-tooltip__row"><i style="--c:${marker}"></i><span>${esc(item.name || "Value")}</span><b>${esc(graphFormat(value, item.format))}</b>${band}</div>`;
      }).filter(Boolean).join("");
      return `<div class="graph-tooltip__head">${esc(head)}</div>${rows}`;
    };

    const yGrid = leftAxis.ticks.map((tick) => `<line class="graph-grid-line" x1="${margin.left}" x2="${margin.left + plotWidth}" y1="${yToPx(tick)}" y2="${yToPx(tick)}"></line>`).join("");
    const yLabels = leftAxis.ticks.map((tick) => `<text class="graph-axis-text graph-axis-text--y" x="${yLabelX}" y="${yToPx(tick) + 3.5}">${esc(graphFormat(tick, spec.y?.format))}</text>`).join("");
    const rightLabels = rightAxis ? rightAxis.ticks.map((tick) => `<text class="graph-axis-text graph-axis-text--right" x="${rightLabelX}" y="${yToPx(tick, "right") + 3.5}">${esc(graphFormat(tick, spec.yRight?.format))}</text>`).join("") : "";
    let xTicks;
    if (category) {
      const step = Math.max(1, Math.ceil(xRaw.length / 7));
      xTicks = xRaw.map((value, index) => ({ value, index })).filter((_, index) => index % step === 0 || index === xRaw.length - 1);
    } else if (xSpec.type === "log") {
      const candidates = xSpec.ticks || [1, 2, 5, 10, 20, 60];
      xTicks = candidates.filter((value) => value > 0 && Math.log10(value) >= xMin && Math.log10(value) <= xMax).map((value) => ({ value, index: 0 }));
    } else if (xSpec.type === "time") {
      const rawMin = Math.min(...xRaw), rawMax = Math.max(...xRaw);
      let values = graphTimeTicks(rawMin, rawMax, 6) || graphNiceTicks(rawMin, rawMax, 6).filter((value) => value >= rawMin && value <= rawMax);
      if (values.length < 2) values = [rawMin, rawMax];
      /* Keep the first and last recorded dates visible even when calendar
         ticks begin at the next midnight. The collision pass below still
         removes any endpoint that would make a narrow chart noisy. */
      values = [...new Set([...values, rawMin, rawMax])].sort((a, b) => a - b);
      /* Calendar labels intentionally omit clock time on long spans. Remove
         duplicate labels created by an endpoint landing on the same day as a
         midnight tick; keeping the earlier tick preserves even spacing. */
      const seenDates = new Set();
      values = values.filter((value) => {
        const label = graphXLabel(value, xSpec, rawMin, rawMax);
        if (seenDates.has(label)) return false;
        seenDates.add(label);
        return true;
      });
      xTicks = values.map((value) => ({ value, index: 0 }));
    } else if (xRaw.length === 1) {
      xTicks = [{ value: xRaw[0], index: 0 }];
    } else {
      const rawMin = Math.min(...xRaw), rawMax = Math.max(...xRaw);
      let values = graphNiceTicks(rawMin, rawMax, 6).filter((value) => value >= rawMin && value <= rawMax);
      if (values.length < 2) values = [rawMin, rawMax];
      xTicks = values.map((value) => ({ value, index: 0 }));
    }
    /* Greedy collision pass: once a label is placed, drop any later label that
       would overlap it, so narrow charts never stack x-ticks on top of each
       other. The first and last labels are always kept as anchors. */
    const seenXTicks = new Set();
    let lastLabelRight = -Infinity;
    const keptTicks = [];
    xTicks.forEach((tick) => {
      const x = xToPx(tick.value, category ? tick.value : tick.index);
      const label = category ? graphXLabel(tick.index, xSpec, xMin, xMax) : graphXLabel(tick.value, xSpec, Math.min(...xRaw), Math.max(...xRaw));
      const half = graphTextWidth(label) / 2;
      const left = x - half, right = x + half;
      const keep = left >= lastLabelRight + 8;
      if (keep) lastLabelRight = right;
      keptTicks.push({ ...tick, x, label, half, keep });
    });
    if (keptTicks.length && !keptTicks[keptTicks.length - 1].keep) {
      const previous = [...keptTicks].reverse().find((item) => item.keep);
      const last = keptTicks[keptTicks.length - 1];
      if (previous && last.x - last.half >= previous.x + previous.half + 8) last.keep = true;
    }
    const xAxis = keptTicks.map(({ x, label, keep }) => {
      const key = `${Math.round(x)}:${label}`;
      if (!keep || seenXTicks.has(key)) return "";
      seenXTicks.add(key);
      return `<text class="graph-axis-text graph-axis-text--x" x="${x}" y="${bottom + 20}">${esc(label)}</text>`;
    }).join("");

    const graphId = String(el.id || "chart").replace(/[^a-z0-9_-]/gi, "-");
    const patternId = `graph-band-${graphId}-${graph.uid}`;
    const clipId = `graph-clip-${graphId}-${graph.uid}`;
    const defs = `<defs><pattern id="${patternId}" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="8" height="8" fill="${hexToRgba(GRAPH.orange, .13)}"></rect><line x1="0" y1="0" x2="0" y2="8" stroke="${GRAPH.orange}" stroke-opacity=".55" stroke-width="1.8"></line></pattern><clipPath id="${clipId}"><rect x="${margin.left}" y="${margin.top}" width="${plotWidth}" height="${plotHeight}"></rect></clipPath></defs>`;
    const bands = spec.series.filter((item) => item.band).map((item, index) => {
      const path = graphBandPath(item, spec.x.values, xToPx, (value) => yToPx(value, item.axis || "left"), displayIndices.get(item));
      return path ? `<path class="graph-band" style="--i:${index}" d="${path}" fill="${item.band.pattern ? `url(#${patternId})` : item.band.color || GRAPH.orangeSoft}"></path>` : "";
    }).join("");
    const areas = spec.series.filter((item) => item.area).map((item, index) => {
      const path = graphAreaPath(item, spec.x.values, xToPx, (value) => yToPx(value, item.axis || "left"), yToPx(item.areaBaseline ?? leftAxis.min, item.axis || "left"), displayIndices.get(item));
      return path ? `<path class="graph-area" style="--i:${index}" d="${path}" fill="${item.areaColor || GRAPH.greenSoft}"></path>` : "";
    }).join("");
    const bars = spec.series.filter((item) => item.type === "bar").map((item) => {
      const points = (displayIndices.get(item) || []).map((index) => ({ value: spec.x.values[index], index, y: item.values?.[index] })).filter((point) => finiteGraphValue(point.y));
      if (!points.length) return "";
      const positions = points.map((point) => xToPx(point.value, point.index));
      /* Same-day rides share an x coordinate. Measuring the raw positions made
         their minimum gap zero, collapsing every bar to a 2px hairline. Use
         distinct positions for the spacing calculation, then offset duplicate
         samples into a compact cluster so each effort stays readable. */
      const groups = new Map();
      positions.forEach((position, pointIndex) => {
        /* Time bars represent rides, so same-calendar-day samples form one
           cluster even when their timestamps are a few pixels apart. */
        const key = xSpec.type === "time"
          ? new Date(graphNumber(points[pointIndex].value) * 1000).toLocaleDateString()
          : position.toFixed(3);
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(pointIndex);
      });
      const groupCenters = [...groups.values()].map((group) => group.reduce((sum, index) => sum + positions[index], 0) / group.length).sort((a, b) => a - b);
      const fallbackGap = plotWidth / Math.max(1, Math.min(spec.x.values.length, 8));
      const gap = groupCenters.length > 1
        ? Math.min(...groupCenters.slice(1).map((value, index) => value - groupCenters[index]))
        : fallbackGap;
      const maxGroup = Math.max(...[...groups.values()].map((group) => group.length));
      const barWidth = Math.max(3, Math.min(48, (Math.max(12, gap) * .78 - 2 * (maxGroup - 1)) / maxGroup));
      return points.map((point, pointIndex) => {
        const key = xSpec.type === "time"
          ? new Date(graphNumber(point.value) * 1000).toLocaleDateString()
          : positions[pointIndex].toFixed(3);
        const group = groups.get(key);
        const center = group.reduce((sum, index) => sum + positions[index], 0) / group.length;
        const offset = (group.indexOf(pointIndex) - (group.length - 1) / 2) * (barWidth + 2);
        const x = center + offset;
        const y = yToPx(point.y, item.axis || "left"), zero = yToPx(0, item.axis || "left");
        const color = Array.isArray(item.pointColors) ? item.pointColors[point.index] : item.color || GRAPH.blue;
        return `<rect class="graph-bar" style="--i:${pointIndex}" x="${x - barWidth / 2}" y="${Math.min(y, zero)}" width="${barWidth}" height="${Math.max(1, Math.abs(zero - y))}" rx="${Math.min(3, barWidth / 2)}" fill="${color}" opacity="${item.opacity ?? .62}"></rect>`;
      }).join("");
    }).join("");
    const lines = spec.series.filter((item) => item.type !== "bar").map((item, seriesIndex) => {
      const sequence = displayIndices.get(item) || [];
      const smooth = item.curve !== false && sequence.length >= 3 && sequence.length <= 220;
      const path = graphLinePath(item, spec.x.values, xToPx, yToPx, displayIndices.get(item), smooth);
      if (!path) return "";
      const points = item.points === false ? "" : sequence.map((index) => {
        const value = item.values?.[index];
        if (!finiteGraphValue(value) || !finiteGraphValue(spec.x.values[index])) return "";
        const color = Array.isArray(item.pointColors) ? item.pointColors[index] : typeof item.pointColor === "function" ? item.pointColor(value, index) : item.color || GRAPH.green;
        return `<circle class="graph-point" cx="${xToPx(spec.x.values[index], index)}" cy="${yToPx(value, item.axis || "left")}" r="${item.pointRadius || 2.6}" fill="${color}"></circle>`;
      }).join("");
      const dash = item.dash ? ` stroke-dasharray="${item.dash}"` : "";
      return `<g class="graph-series" style="--i:${seriesIndex}"><path class="graph-line" d="${path}" stroke="${item.color || GRAPH.green}" stroke-width="${item.width || 2}"${dash}></path>${points}</g>`;
    }).join("");
    const cursorDots = spec.series.map((item, index) => item.type === "bar" ? "" : `<circle class="graph-cursor-dot" data-cursor-series="${index}" r="${Math.max(3.6, (item.pointRadius || 2.6) + 1.4)}" fill="${item.color || GRAPH.green}" style="display:none"></circle>`).join("");
    const hoverPoints = spec.series.map((item, index) => item.type === "bar" ? "" : `<circle class="graph-hover-point" data-hover-series="${index}" r="${Math.max(4.5, (item.pointRadius || 2.6) + 2.4)}" fill="${item.color || GRAPH.green}" style="display:none"></circle>`).join("");
    /* One calm entrance per render: the plot fades in, lines draw left to
       right, bars rise from their baseline. A resize redraw of the same chart
       never replays it (the window has closed); a picker change renders a new
       graph with no start time and gets the moment again. Disabled under
       prefers-reduced-motion. */
    if (graph._enterStarted == null) graph._enterStarted = performance.now();
    const animateEntrance = performance.now() - graph._enterStarted < 700 && !window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const zeroLines = `${leftAxis.min < 0 && leftAxis.max > 0 ? `<line class="graph-zero-line" x1="${margin.left}" x2="${margin.left + plotWidth}" y1="${yToPx(0)}" y2="${yToPx(0)}"></line>` : ""}${rightAxis && rightAxis.min < 0 && rightAxis.max > 0 ? `<line class="graph-zero-line" x1="${margin.left}" x2="${margin.left + plotWidth}" y1="${yToPx(0, "right")}" y2="${yToPx(0, "right")}"></line>` : ""}`;
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.innerHTML = `${defs}<g class="graph-grid">${yGrid}</g><g class="graph-axes">${yLabels}${rightLabels}${zeroLines}${xAxis}</g><g class="graph-plot${animateEntrance ? " graph-enter" : ""}" clip-path="url(#${clipId})"><g class="graph-areas">${areas}</g><g class="graph-bands">${bands}</g><g class="graph-bars">${bars}</g><g class="graph-lines">${lines}</g></g>${hoverPoints}<line class="graph-hover-line" x1="0" x2="0" y1="${margin.top}" y2="${bottom}" style="display:none"></line><line class="graph-cursor-line" x1="0" x2="0" y1="${margin.top}" y2="${bottom}" style="display:none"></line>${cursorDots}<rect class="graph-hit-area" x="${margin.left}" y="${margin.top}" width="${plotWidth}" height="${plotHeight}" tabindex="0" role="button" aria-roledescription="chart" aria-label="Hover or select ${esc(spec.ariaLabel || "graph")}"></rect>`;
    if (animateEntrance) {
      $$(".graph-line", svg).forEach((path) => {
        try {
          const length = path.getTotalLength();
          if (Number.isFinite(length) && length > 0) path.style.setProperty("--len", length);
        } catch (_) { /* empty path */ }
      });
      /* Drop the class once the animation has run so a series' own dash or
         style is never overridden afterwards. */
      setTimeout(() => { const plot = $(".graph-plot", svg); if (plot) plot.classList.remove("graph-enter"); }, 750);
    }
    graph.cursorLine = $(".graph-cursor-line", svg);
    graph.cursorDots = $$(".graph-cursor-dot", svg);
    graph._cursorSeries = spec.series;
    graph._cursorY = (value, axis) => yToPx(value, axis);
    graph.hoverLine = $(".graph-hover-line", svg);
    graph.hoverPoints = $$(".graph-hover-point", svg);
    graph.hit = $(".graph-hit-area", svg);
    graph._margin = margin;
    graph._plotWidth = plotWidth;
    graph._plotHeight = plotHeight;
    graph._xMin = xMin;
    graph._xMax = xMax;
    graph.legendEl.innerHTML = legendItems.length ? legendItems.map((item) => `<span class="graph-legend-item"><i style="--c:${item.color || GRAPH.green}"></i>${esc(item.name)}</span>`).join("") : "";
    if (graph.cursorX != null) graph.setCursor(graph.cursorX);

    const showIndex = (candidate) => {
      const index = graph._validIndex(candidate);
      graph.hoverIndex = index;
      const x = xToPx(spec.x.values[index], index);
      graph.hoverLine.setAttribute("x1", x); graph.hoverLine.setAttribute("x2", x); graph.hoverLine.style.display = "block";
      let anchorY = null;
      graph.hoverPoints.forEach((point) => {
        const item = spec.series[Number(point.dataset.hoverSeries)];
        const value = item?.values?.[index];
        if (!item || !finiteGraphValue(value)) { point.style.display = "none"; return; }
        point.setAttribute("cx", x);
        const py = yToPx(value, item.axis || "left");
        point.setAttribute("cy", py);
        point.style.display = "block";
        if (anchorY == null) anchorY = py;
      });
      graph.tooltip.innerHTML = graph._formatTooltip(index);
      graph.tooltip.hidden = false;
      const towardLeft = x > width * .62;
      const rows = spec.series.filter((item) => item.tooltip !== false && finiteGraphValue(item.values?.[index])).length;
      const tooltipHeight = Math.min(58 + rows * 30, 210);
      graph.tooltip.style.left = `${towardLeft ? Math.max(6, x - 16) : Math.min(x + 16, width - 20)}px`;
      graph.tooltip.style.top = `${anchorY != null ? Math.max(6, Math.min(height - tooltipHeight - 6, anchorY - 10)) : 12}px`;
      graph.tooltip.style.transform = towardLeft ? "translateX(-100%)" : "none";
      return index;
    };
    graph._showIndex = showIndex;
    const pointerToIndex = (event) => {
      const rect = svg.getBoundingClientRect();
      const px = ((event.clientX - rect.left) / Math.max(1, rect.width)) * width;
      return graph._nearestIndex(px);
    };
    let hoverFrame = null;
    graph.hit.addEventListener("pointermove", (event) => {
      if (graph.pinned) return;
      if (hoverFrame) return;
      hoverFrame = requestAnimationFrame(() => {
        hoverFrame = null;
        showIndex(pointerToIndex(event));
      });
    });
    graph.hit.addEventListener("pointerleave", () => {
      graph.hoverLine.style.display = "none";
      graph.hoverPoints.forEach((point) => { point.style.display = "none"; });
      /* A pinned tooltip (from a click) stays put so the values remain
         readable; plain hover leaves with the pointer. */
      if (!graph.pinned) { graph.tooltip.hidden = true; graph.tooltip.style.transform = "none"; }
    });
    graph.hit.addEventListener("click", (event) => {
      /* Seek to exactly where the pointer is, never to a stale hover index —
         clicking without hovering used to jump to the left edge. */
      const index = graph._validIndex(pointerToIndex(event));
      const x = spec.x.values[index];
      graph.pinned = true;
      showIndex(index);
      graph.setCursor(graphNumber(x));
      graph.callbacks.forEach((callback) => callback(graphNumber(x), index));
    });
    graph.hit.addEventListener("keydown", (event) => {
      let index = graph._validIndex(graph.hoverIndex ?? 0);
      if (event.key === "Escape") { graph.pinned = false; graph.tooltip.hidden = true; graph.hoverLine.style.display = "none"; graph.hoverPoints.forEach((point) => { point.style.display = "none"; }); return; }
      if (event.key === "ArrowRight" || event.key === "ArrowDown") index = graph._validIndex(index + 1, 1);
      else if (event.key === "ArrowLeft" || event.key === "ArrowUp") index = graph._validIndex(index - 1, -1);
      else if (event.key === "Home") index = graph._validIndex(0, 1);
      else if (event.key === "End") index = graph._validIndex(spec.x.values.length - 1, -1);
      else if (event.key === "Enter" || event.key === " ") { event.preventDefault(); const selected = showIndex(index); graph.pinned = true; graph.setCursor(graphNumber(spec.x.values[selected])); graph.callbacks.forEach((callback) => callback(graphNumber(spec.x.values[selected]), selected)); return; }
      else return;
      event.preventDefault(); const selected = showIndex(index); graph.callbacks.forEach((callback) => callback(graphNumber(spec.x.values[selected]), selected));
    });
  }

  if (window.ResizeObserver) {
    graph.resizeObserver = new ResizeObserver(() => draw());
    graph.resizeObserver.observe(el);
  }
  draw();
  requestAnimationFrame(() => { if (el.isConnected && state.charts.get(el.id) === graph) draw(); });
  return graph;
}

function scheduleCursor(cursor) {
  state.cursor = cursor;
  if (state.cursorFrame) return;
  const paint = (now) => {
    if (now - (state.cursorPaintAt || 0) < 30) { state.cursorFrame = requestAnimationFrame(paint); return; }
    state.cursorFrame = null;
    state.cursorPaintAt = now;
    if (!state.cursor) return;
    Object.values(state.cursor).forEach((ref) => { if (ref?.el && ref.x != null) ref.el.setCursor?.(graphNumber(ref.x)); });
  };
  state.cursorFrame = requestAnimationFrame(paint);
}
function bindGraphSeek(graph, callback) {
  if (!graph?.onClick) return;
  graph.onClick((value) => callback(value));
}
function graphEmpty(el, title, message) {
  if (!el) return;
  state.charts.get(el.id)?.destroy?.();
  state.charts.delete(el.id);
  el.innerHTML = emptyState(title, message);
}

/* ------------------------------------------------------------------ dashboard */

async function renderDashboard(_, token = startPage()) {
  view.innerHTML = `<div class="skeleton skeleton--hero"></div><div class="summary-strip" aria-hidden="true"><div class="skeleton skeleton--summary"></div><div class="skeleton skeleton--summary"></div><div class="skeleton skeleton--summary"></div><div class="skeleton skeleton--summary"></div></div><div class="skeleton skeleton--chart mt-24"></div>`;
  let data;
  try { data = await loadOverview(); } catch (error) { if (pageIsCurrent(token)) view.innerHTML = errorState("Overview could not load", error.message, retryAction()); return; }
  if (!pageIsCurrent(token)) return;
  state.overview = data;
  const rides = [...(data.rides || [])].sort((a, b) => (b.started_at || 0) - (a.started_at || 0));
  const latest = rides[0];
  const periodAnchor = latest?.started_at || Date.now() / 1000;
  const periodStart = periodAnchor - 30 * 86400;
  const recentRides = rides.filter((ride) => (ride.started_at || 0) >= periodStart);
  const totalDistance = recentRides.reduce((sum, ride) => sum + (ride.distance_m || 0), 0);
  const totalDuration = recentRides.reduce((sum, ride) => sum + (ride.duration_s || 0), 0);
  const totalGain = recentRides.reduce((sum, ride) => sum + (ride.gain_m || 0), 0);
  view.innerHTML = `
    ${pageHeader("Overview", latest ? `<a class="button button--primary button--small" href="#/ride/${latest.id}">Open latest ride ${icon("arrow-up-right")}</a>` : `<a class="button button--primary button--small" href="#/import">Import your first ride ${icon("arrow-up-right")}</a>`)}
    <section class="summary-block" aria-labelledby="period-title"><div id="period-title" class="summary-block__label">Last 30 days</div><div class="summary-strip"><div class="summary-item"><div class="summary-item__label">Rides</div><div class="summary-item__value">${recentRides.length}</div></div><div class="summary-item"><div class="summary-item__label">Distance</div><div class="summary-item__value">${(totalDistance / 1000).toFixed(0)}<small>km</small></div></div><div class="summary-item"><div class="summary-item__label">Time</div><div class="summary-item__value">${esc(fmtDuration(totalDuration))}</div></div><div class="summary-item"><div class="summary-item__label">Climbing</div><div class="summary-item__value">${Math.round(totalGain).toLocaleString()}<small>m</small></div></div></div></section>
    <section class="primary-chart"><article class="card chart-card chart-card--primary"><div class="card-title"><div><h2>Watts at the same heart rate</h2></div><div id="hr-picker" class="segmented" role="group" aria-label="Choose heart rate"></div></div><div id="watts-hr-chart" class="chart chart--large"></div></article></section>
    <section class="dashboard-lists"><article class="card chart-card"><div class="card-title"><div><h2>Personal records</h2></div><a class="button button--quiet button--small" href="#/records">View all ${icon("chevron-right")}</a></div><div id="dash-records" class="dashboard-records"></div></article></section>
    <section class="analysis-section" aria-labelledby="analysis-title"><div class="section-heading"><h2 id="analysis-title">Trends</h2></div><div class="analysis-grid"><article class="card chart-card"><div class="card-title"><div><h2>Fitness &amp; freshness</h2></div></div><div id="fitness-chart" class="chart chart--small"></div></article><article class="card chart-card"><div class="card-title"><div><h2>Cardiac drift</h2></div></div><div id="drift-chart" class="chart chart--small"></div></article><article class="card chart-card"><div class="card-title"><div><h2>Best power over time</h2></div><div id="power-picker" class="segmented" role="group" aria-label="Choose power duration"></div></div><div id="power-trend-chart" class="chart chart--small"></div></article><article class="card chart-card"><div class="card-title"><div><h2>Recent power curves</h2></div></div><div id="power-curves-chart" class="chart chart--small"></div></article></div></section>`;

  renderDashboardRecords(data.records || [], data.failures?.records);
  renderDashboardPlots(data);
}

function overviewError(el, title, message, key) {
  if (!el) return;
  el.innerHTML = errorState(title, message, retryAction("Retry", key));
}
function renderDashboardRecords(records, failure = null) {
  const el = $("#dash-records");
  if (!el) return;
  if (failure) { overviewError(el, "Records unavailable", "Try again to load personal records.", "records"); return; }
  if (!records.length) { el.innerHTML = emptyState("No records yet", "Import a ride and your first best effort will appear here.", `<a class="button button--primary button--small" href="#/import">Import ride</a>`); return; }
  el.innerHTML = records.slice(0, 6).map((record) => `<a class="list-row" href="#/ride/${record.ride_id}" aria-label="Open ride for ${esc(record.label)}"><div class="list-row__main"><strong>${esc(record.label)}</strong><small>${esc(fmtDate(record.started_at))}</small></div><div class="list-row__value">${esc(recordDisplay(record))}${icon("chevron-right")}</div></a>`).join("");
}
function renderDashboardPlots(data) {
  const failures = data.failures || {};
  const watts = data.wattsHr || { fixed_hrs: [], series: {} };
  const hrs = (watts.fixed_hrs || []).map(String);
  let selectedHr = sessionStorage.getItem("velotrack.hr");
  if (!hrs.includes(selectedHr)) selectedHr = hrs.includes("140") ? "140" : hrs[0];
  const hrPicker = $("#hr-picker");
  if (failures.wattsHr) {
    hrPicker.innerHTML = "";
    overviewError($("#watts-hr-chart"), "Main trend unavailable", "Try again to load this chart.", "wattsHr");
  } else {
    hrPicker.innerHTML = hrs.map((hr) => `<button type="button" class="${hr === selectedHr ? "active" : ""}" data-value="${esc(hr)}" aria-pressed="${hr === selectedHr}">${esc(hr)} bpm</button>`).join("");
    $$("button", hrPicker).forEach((button) => button.addEventListener("click", () => { selectedHr = button.dataset.value; sessionStorage.setItem("velotrack.hr", selectedHr); $$("button", hrPicker).forEach((item) => { item.classList.toggle("active", item === button); item.setAttribute("aria-pressed", item === button); }); drawWattsHr(watts, selectedHr); }));
    drawWattsHr(watts, selectedHr);
  }
  if (failures.fitness) overviewError($("#fitness-chart"), "Fitness trend unavailable", "Try again to load this chart.", "fitness"); else drawFitness(data.fitness || { points: [] });
  if (failures.drift) overviewError($("#drift-chart"), "Cardiac drift unavailable", "Try again to load this chart.", "drift"); else drawCardiac(data.drift || { points: [] });

  const powerPicker = $("#power-picker");
  if (failures.power) {
    powerPicker.innerHTML = "";
    overviewError($("#power-trend-chart"), "Power trend unavailable", "Try again to load these charts.", "power");
    overviewError($("#power-curves-chart"), "Power curves unavailable", "Try again to load these charts.", "power");
    return;
  }
  const durations = ["1", "5", "20", "60"].filter((duration) => data.power?.series?.[duration]);
  let selectedDuration = sessionStorage.getItem("velotrack.powerMin");
  if (!durations.includes(selectedDuration)) selectedDuration = durations.includes("5") ? "5" : durations[0];
  powerPicker.innerHTML = durations.map((duration) => `<button type="button" class="${duration === selectedDuration ? "active" : ""}" data-value="${duration}" aria-pressed="${duration === selectedDuration}">${duration} min</button>`).join("");
  $$("button", powerPicker).forEach((button) => button.addEventListener("click", () => { selectedDuration = button.dataset.value; sessionStorage.setItem("velotrack.powerMin", selectedDuration); $$("button", powerPicker).forEach((item) => { item.classList.toggle("active", item === button); item.setAttribute("aria-pressed", item === button); }); drawPowerTrend(data.power, selectedDuration); }));
  drawPowerTrend(data.power || { series: {} }, selectedDuration);
  drawPowerCurves(data.power || { curves: [] });
}
const OVERVIEW_RETRY_ENDPOINTS = {
  wattsHr: "/api/trends/watts_hr",
  fitness: "/api/trends/fitness",
  records: "/api/records",
  drift: "/api/trends/cardiac",
  power: "/api/trends/power",
};
async function retryOverview(key, button) {
  const path = OVERVIEW_RETRY_ENDPOINTS[key];
  if (!path) return;
  const token = state.pageToken;
  button.disabled = true;
  try {
    const result = await api(path);
    if (!pageIsCurrent(token)) return;
    const data = state.overview || {};
    data[key] = result;
    data.failures = { ...(data.failures || {}) };
    delete data.failures[key];
    state.overview = data;
    if (key === "records") renderDashboardRecords(data.records || []);
    else renderDashboardPlots(data);
  } catch (error) {
    if (pageIsCurrent(token)) { button.disabled = false; toast(error.message); }
  }
}
function drawWattsHr(data, selected) {
  const el = $("#watts-hr-chart");
  if (!el) return;
  const points = data.series?.[selected] || [];
  if (!points.length) { graphEmpty(el, "No fixed-heart-rate signal yet", "This needs a ride with heart rate and enough pedalling data to make the estimate useful."); return; }
  renderGraph(el, {
    ariaLabel: `Watts produced at ${selected} beats per minute over the ride history`,
    x: { values: points.map((point) => point.date), type: "time", label: "ride date" },
    y: { format: "watts", includeZero: true, robust: true },
    series: [{
      name: `${selected} bpm`, values: points.map((point) => point.watts), color: GRAPH.green, width: 2.6, pointRadius: 4,
      format: "watts",
      band: { lo: points.map((point) => point.lo ?? point.watts), hi: points.map((point) => point.hi ?? point.watts), pattern: true },
    }],
  });
}
function drawFitness(data) {
  const el = $("#fitness-chart");
  const points = data.points || [];
  if (!el) return;
  if (!points.length) { graphEmpty(el, "Heart rate will build this view", "Import rides with HR data to see fitness and freshness."); return; }
  renderGraph(el, {
    ariaLabel: "Fitness, fatigue, and form over time",
    x: { values: points.map((point) => point.date), type: "time", label: "ride date" },
    y: { format: "decimal", includeZero: true },
    series: [
      { name: "Fitness", values: points.map((point) => point.ctl), color: GRAPH.blue, width: 2.4, pointRadius: 2.8, curve: false, format: "decimal" },
      { name: "Fatigue", values: points.map((point) => point.atl), color: GRAPH.orange, width: 2.1, pointRadius: 2.8, curve: false, format: "decimal" },
      { name: "Form", values: points.map((point) => point.tsb), color: GRAPH.green, width: 2.1, pointRadius: 2.8, curve: false, format: "decimal" },
    ],
  });
}
function drawCardiac(data) {
  const el = $("#drift-chart");
  const points = data.points || [];
  if (!el) return;
  if (!points.length) { graphEmpty(el, "No steady window yet", "Drift stays quiet when the effort is too variable to interpret."); return; }
  renderGraph(el, {
    ariaLabel: "Cardiac drift by ride",
    x: { values: points.map((point) => point.date), type: "time", label: "ride date" },
    y: { format: "decimal", includeZero: true },
    series: [{ name: "Cardiac drift", values: points.map((point) => point.drift_bpm_per_hr), color: GRAPH.orange, pointColors: points.map((point) => point.drift_bpm_per_hr >= 0 ? GRAPH.orange : GRAPH.green), width: 2.2, pointRadius: 3.5, format: (value) => `${Number(value).toFixed(1)} bpm/hr` }],
  });
}
function drawPowerTrend(data, duration) {
  const el = $("#power-trend-chart");
  if (!el) return;
  const points = data.series?.[duration] || [];
  if (!points.length) { graphEmpty(el, "Power trends need more rides", "Best sustained power appears here once a few rides are imported."); return; }
  renderGraph(el, {
    ariaLabel: `Best power for ${duration} ${Number(duration) === 1 ? "minute" : "minutes"} over time`,
    x: { values: points.map((point) => point.date), type: "time", label: "ride date" },
    y: { format: "watts", includeZero: true, robust: true },
    series: [{
      name: `best ${duration} min`, values: points.map((point) => point.watts), color: GRAPH.orange, width: 2.5, pointRadius: 3.5, format: "watts",
      band: { lo: points.map((point) => point.lo ?? point.watts), hi: points.map((point) => point.hi ?? point.watts), pattern: true },
    }],
  });
}
function drawPowerCurves(data) {
  const el = $("#power-curves-chart");
  if (!el) return;
  const curves = data.curves || [];
  if (!curves.length) { graphEmpty(el, "No curves yet", "Your recent rides will layer here as the history grows."); return; }
  const xValues = data.durations || curves[0].points.map((point) => point.min);
  const series = curves.map((curve, index) => {
    const byMinute = Object.fromEntries((curve.points || []).map((point) => [point.min, point.watts]));
    const newest = index === curves.length - 1;
    return { name: fmtDate(curve.date), values: xValues.map((minute) => byMinute[minute]), color: newest ? GRAPH.green : hexToRgba(GRAPH.blue, .25 + index / Math.max(curves.length, 2) * .45), width: newest ? 2.8 : 1.35, pointRadius: newest ? 3.5 : 2, format: "watts" };
  });
  renderGraph(el, {
    ariaLabel: "Power duration curves for recent rides",
    x: { values: xValues, type: "log", ticks: [1, 2, 5, 10, 20, 60], label: "minutes" },
    y: { format: "watts", includeZero: true },
    series,
  });
}

/* ------------------------------------------------------------------ rides */

async function renderRides(_, token = startPage()) {
  view.innerHTML = `<div class="skeleton skeleton--card"></div>`;
  let rides;
  try { rides = await api("/api/rides"); } catch (error) { if (pageIsCurrent(token)) view.innerHTML = errorState("Rides could not load", error.message, retryAction()); return; }
  if (!pageIsCurrent(token)) return;
  const sorted = [...rides].sort((a, b) => (b.started_at || 0) - (a.started_at || 0));
  view.innerHTML = `    ${pageHeader("Rides")}${sorted.length ? `<section class="card ride-list-card"><div class="card-title"><div><h2>Ride history</h2></div></div><div class="toolbar"><label class="search-field">${icon("search")}<input id="ride-search" type="search" placeholder="Search ride files" aria-label="Search rides"></label><select id="ride-sort" class="filter-select" aria-label="Sort rides"><option value="newest">Newest first</option><option value="oldest">Oldest first</option><option value="distance">Longest distance</option><option value="climb">Most climbing</option></select></div><div id="ride-table-wrap"></div></section>` : emptyState("Your ride library is empty", "Import a Wahoo .FIT file to start your history.", `<a class="button button--primary button--small" href="#/import">Import first ride</a>`)}`;
  const search = $("#ride-search"), sort = $("#ride-sort"), table = $("#ride-table-wrap");
  function drawRows() {
    const query = search.value.trim().toLowerCase();
    const mode = sort.value;
    const rows = sorted.filter((ride) => `${ride.filename || ""} ${fmtDateTime(ride.started_at)}`.toLowerCase().includes(query)).sort((a, b) => mode === "oldest" ? a.started_at - b.started_at : mode === "distance" ? (b.distance_m || 0) - (a.distance_m || 0) : mode === "climb" ? (b.gain_m || 0) - (a.gain_m || 0) : b.started_at - a.started_at);
    if (!rows.length) { table.innerHTML = emptyState("No rides match", "Try a different filename, date, or sort."); return; }
    table.innerHTML = `<div class="table-scroll"><table class="ride-table"><thead><tr><th>Date</th><th class="num">Distance</th><th class="num">Time</th><th class="num">Climb</th><th class="num">Power</th></tr></thead><tbody>${rows.map((ride) => `<tr tabindex="0" data-ride="${ride.id}" aria-label="Open ${esc(ride.filename || "ride")}"><td class="mono">${esc(fmtDateTime(ride.started_at))}</td><td class="num">${esc(fmtDistance(ride.distance_m))}</td><td class="num">${esc(fmtDuration(ride.duration_s))}</td><td class="num">${esc(fmtMeters(ride.gain_m))}</td><td class="num">${esc(fmtWatts(ride.avg_watts))}</td></tr>`).join("")}</tbody></table></div>`;
    $$('[data-ride]', table).forEach((row) => { const go = () => { if (!location.hash.includes(`/ride/${row.dataset.ride}`)) location.hash = `#/ride/${row.dataset.ride}`; }; row.addEventListener("click", go); row.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); go(); } }); });
  }
  search.addEventListener("input", drawRows); sort.addEventListener("change", drawRows); drawRows();
}

/* ------------------------------------------------------------------ import */

function renderImport(_, token = startPage()) {
  view.innerHTML = `    ${pageHeader("Import rides")}<section class="import-layout"><div class="card card-pad import-card"><div id="dropzone" class="dropzone" role="button" tabindex="0" aria-describedby="dropzone-help"><div class="dropzone__content"><div class="upload-mark">${icon("upload")}</div><h2>Select FIT files</h2><p id="dropzone-help">Drop files here or click to browse.</p><span class="dropzone__format">FIT files only</span></div></div><input id="file-input" type="file" accept=".fit" multiple hidden aria-label="Choose FIT files"></div></section><section id="queue-card" class="card queue-card" hidden><div class="card-title"><h2>Import queue</h2><span id="queue-count" class="pill pill--muted">Waiting</span></div><div id="jobs"></div><div id="queue-status" class="visually-hidden" role="status" aria-live="polite"></div></section>`;
  const dropzone = $("#dropzone"), input = $("#file-input");
  const choose = () => { input.value = ""; input.click(); };
  let dragDepth = 0;
  dropzone.addEventListener("click", choose);
  dropzone.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); choose(); } });
  dropzone.addEventListener("dragenter", (event) => { event.preventDefault(); dragDepth += 1; dropzone.classList.add("drag"); });
  dropzone.addEventListener("dragover", (event) => { event.preventDefault(); });
  dropzone.addEventListener("dragleave", (event) => { event.preventDefault(); dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) dropzone.classList.remove("drag"); });
  dropzone.addEventListener("drop", (event) => { event.preventDefault(); dragDepth = 0; dropzone.classList.remove("drag"); handleFiles(event.dataTransfer.files, token); });
  input.addEventListener("change", () => { handleFiles(input.files, token).finally(() => { input.value = ""; }); });
  loadJobs(token);
}

async function handleFiles(fileList, token) {
  const files = [];
  for (const file of fileList || []) {
    if (!file.name.toLowerCase().endsWith(".fit")) continue;
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = "";
      for (let offset = 0; offset < bytes.length; offset += 0x8000) binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
      files.push({ name: file.name, data: btoa(binary) });
    } catch (_) {
      toast(`Could not read ${file.name}.`);
    }
  }
  if (!files.length) { toast("Choose one or more .FIT files."); return; }
  try { const result = await api("/api/import", { method: "POST", body: { files } }); if (!pageIsCurrent(token)) return; const jobs = Array.isArray(result.jobs) ? result.jobs : []; if (!jobs.length) { toast("No rides were queued. Check the FIT files."); return; } toast(`Queued ${jobs.length} ride${jobs.length === 1 ? "" : "s"}.`); pollJobs(jobs, token); } catch (error) { if (pageIsCurrent(token)) toast(error.message); }
}
function renderJobs(jobs) {
  const el = $("#jobs"), queueCard = $("#queue-card"); if (!el) return;
  if (queueCard) queueCard.hidden = !jobs.length;
  const count = $("#queue-count");
  if (count) {
    const live = jobs.filter((job) => !["done", "error", "duplicate"].includes(job.status)).length;
    count.textContent = live ? `${live} active` : jobs.length ? `${jobs.length} complete` : "Waiting";
    count.className = `pill ${live ? "pill--orange" : "pill--muted"}`;
  }
  if (!jobs.length) { el.innerHTML = ""; return; }
  const signature = jobs.map((job) => `${job.id}:${job.status}`).join("|");
  const status = $("#queue-status");
  if (status && signature !== state.jobStatusSignature) {
    const active = jobs.filter((job) => !["done", "error", "duplicate"].includes(job.status)).length;
    status.textContent = active ? `${active} import${active === 1 ? "" : "s"} in progress` : jobs.length ? "Import queue complete" : "";
    state.jobStatusSignature = signature;
  }
  const statusLabel = { queued: "Queued", running: "Importing", done: "Complete", error: "Failed", duplicate: "Already imported", unknown: "Unavailable" };
  el.innerHTML = jobs.map((job) => `<div class="job job--${esc(job.status)}"><div class="job__name" title="${esc(job.filename || job.id)}">${esc(job.filename || job.id)}</div><div class="job__status"><i class="job__status-dot"></i>${esc(statusLabel[job.status] || job.status)}</div>${job.status === "running" ? `<div class="progress" role="progressbar" aria-label="Import progress for ${esc(job.filename || job.id)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(job.progress || 0)}"><i style="transform:scaleX(${(Math.max(0, Math.min(1, (job.progress || 0) / 100))).toFixed(3)})"></i></div>` : ""}${job.message ? `<div class="job__message">${esc(job.message)}</div>` : ""}${job.error ? `<div class="job__error">${esc(job.error)}</div>` : ""}${job.status === "done" && job.result ? `<div class="job__message">${esc(fmtDistance(job.result.distance_m))} · ${esc(fmtMeters(job.result.gain_m))} · ${esc(fmtWatts(job.result.avg_watts))}</div>` : ""}</div>`).join("");
}
async function loadJobs(token) {
  try {
    const jobs = await api("/api/jobs");
    if (pageIsCurrent(token)) {
      renderJobs(jobs.slice().reverse());
      const live = jobs.filter((job) => !["done", "error", "duplicate"].includes(job.status)).map((job) => job.id);
      if (live.length) pollJobs(live, token);
    }
  } catch (error) {
    if (pageIsCurrent(token)) {
      const jobs = $("#jobs"), queueCard = $("#queue-card");
      if (queueCard) queueCard.hidden = false;
      if (jobs) jobs.innerHTML = errorState("Import queue unavailable", error.message, retryAction());
    }
  }
}
function pollJobs(ids, token) {
  ids.forEach((id) => state.jobPollerIds.add(id));
  if (state.jobPoller) return;
  const tick = async () => {
    if (!pageIsCurrent(token) || !state.jobPollerIds.size) {
      if (state.jobPoller) clearInterval(state.jobPoller);
      state.jobPoller = null;
      return;
    }
    try {
      const jobs = await api("/api/jobs");
      const visible = jobs.filter((job) => state.jobPollerIds.has(job.id));
      if (pageIsCurrent(token)) renderJobs(visible.slice().reverse());
      visible.filter((job) => ["done", "error", "duplicate"].includes(job.status)).forEach((job) => {
        state.jobPollerIds.delete(job.id);
        if (job.status === "done") toast(`${job.filename || "Ride"} imported.`);
      });
    } catch (_) { /* server may be restarting */ }
    if (!state.jobPollerIds.size && state.jobPoller) {
      clearInterval(state.jobPoller);
      state.jobPoller = null;
    }
  };
  state.jobPoller = setInterval(tick, 800);
  tick();
}

/* ------------------------------------------------------------------ records */

async function renderRecords(_, token = startPage()) {
  view.innerHTML = `<div class="skeleton skeleton--card"></div>`;
  let records;
  try { records = await api("/api/records"); } catch (error) { if (pageIsCurrent(token)) view.innerHTML = errorState("Records could not load", error.message, retryAction()); return; }
  if (!pageIsCurrent(token)) return;
  view.innerHTML = `    ${pageHeader("Records")}${records.length ? `<section class="record-grid">${records.map((record) => `<a class="card record-card" href="#/ride/${record.ride_id}"><div class="record-card__label">${esc(record.label)}</div><div class="record-card__value">${esc(recordDisplay(record))}</div><div class="record-card__foot"><span class="record-card__date">${esc(fmtDate(record.started_at))}</span></div></a>`).join("")}</section>` : emptyState("Your record board is blank", "Import your first ride and the best work will be called out here.", `<a class="button button--primary button--small" href="#/import">Import first ride</a>`)} `;
}

/* ------------------------------------------------------------------ routes */

async function renderRoutes(_, token = startPage()) {
  view.innerHTML = `<div class="skeleton skeleton--card"></div>`;
  let routesList;
  try { routesList = await api("/api/routes"); } catch (error) { if (pageIsCurrent(token)) view.innerHTML = errorState("Routes could not load", error.message, retryAction()); return; }
  if (!pageIsCurrent(token)) return;
  const repeated = routesList.filter((route) => route.n_rides > 1);
  const solo = routesList.filter((route) => route.n_rides === 1);
  view.innerHTML = `    ${pageHeader("Routes")}${routesList.length ? `<section class="route-grid">${[...repeated, ...solo].map((route) => `<a class="card route-card" href="#/route/${route.id}"><h3>${esc(route.name)}</h3><div class="route-card__date">${route.first_at === route.last_at ? esc(fmtDate(route.first_at)) : `${esc(fmtDate(route.first_at))} → ${esc(fmtDate(route.last_at))}`}</div><div class="route-card__stats"><div class="route-card__stat"><small>Length</small><strong>${esc(fmtDistance(route.distance_m))}</strong></div><div class="route-card__stat"><small>Climb</small><strong>${esc(fmtMeters(route.gain_m))}</strong></div><div class="route-card__stat"><small>Recorded</small><strong>${route.n_rides} ride${route.n_rides === 1 ? "" : "s"}</strong></div></div></a>`).join("")}</section>` : emptyState("No routes yet", "Import a history of rides and repeated roads will be grouped automatically.", `<a class="button button--primary button--small" href="#/import">Import history</a>`)} `;
}

async function renderRouteDetail(id, token = startPage()) {
  view.innerHTML = `<div class="skeleton skeleton--card"></div><div class="skeleton skeleton--chart mt-24"></div>`;
  let route;
  try { route = await api(`/api/routes/${id}`); } catch (error) { if (pageIsCurrent(token)) view.innerHTML = errorState("Route not found", error.message, retryAction()); return; }
  if (!pageIsCurrent(token)) return;
  const rides = route.rides || [];
  const w140 = rides.filter((ride) => ride.watts_140 != null);
  const np = rides.filter((ride) => ride.normalized_power != null);
  view.innerHTML = `<div class="detail-top"><a class="detail-back" href="#/routes">${icon("chevron-right")}Back to routes</a><span class="detail-count">${rides.length} ride${rides.length === 1 ? "" : "s"}</span></div><section class="detail-hero route-hero"><div class="detail-hero__title"><h1>${esc(route.name)}</h1><p class="detail-hero__lede">${rides.length > 1 ? "Compare the same road over time." : "Ride this route again to start a comparison."}</p></div><div class="detail-hero__primary"><div class="metric"><div class="metric__label">Route length</div><div class="metric__value">${esc(fmtDistance(route.distance_m))}</div></div><div class="metric"><div class="metric__label">Climbing / ride</div><div class="metric__value">${esc(fmtMeters(route.gain_m))}</div></div><div class="metric"><div class="metric__label">First recorded</div><div class="metric__value metric__value--date">${esc(fmtDate(rides[0]?.started_at))}</div></div><div class="metric"><div class="metric__label">Latest recorded</div><div class="metric__value metric__value--date">${esc(fmtDate(rides[rides.length - 1]?.started_at))}</div></div></div></section>${rides.length > 1 ? `<section class="card card-pad mt-20"><div class="card-title"><div><h2>Ride comparison</h2></div></div><div class="table-scroll"><table class="ride-table"><thead><tr><th>#</th><th>Date</th><th class="num">Time</th><th class="num">Avg speed</th><th class="num">Avg HR</th><th class="num" title="Watts your heart rate predicts at 140 bpm — the honest same-effort comparison">W @ 140</th><th class="num">Power</th><th class="num">Temp</th><th class="num">Wind</th></tr></thead><tbody>${rides.map((ride) => `<tr tabindex="0" data-route-ride="${ride.id}"><td class="mono">${ride.route_n}</td><td class="mono">${esc(fmtDateTime(ride.started_at))}</td><td class="num">${esc(fmtDuration(ride.duration_s))}</td><td class="num">${esc(fmtSpeed(ride.avg_speed_mps))}</td><td class="num">${ride.avg_hr ? `${Math.round(ride.avg_hr)} bpm` : "—"}</td><td class="num">${esc(fmtWatts(ride.watts_140))}</td><td class="num">${esc(fmtWatts(ride.avg_watts))}</td><td class="num">${esc(fmtTemp(ride.temp_c))}</td><td class="num">${esc(fmtWind(ride.wind_mps))}</td></tr>`).join("")}</tbody></table></div></section><section class="card chart-card mt-20"><div class="card-title"><div><h2>Progress on this road</h2></div><div class="chart-legend"><span class="legend-item"><i class="legend-swatch"></i>W @ 140 bpm</span><span class="legend-item"><i class="legend-swatch legend-swatch--blue"></i>Normalized power</span></div></div><div id="route-comparison-chart" class="chart chart--small"></div></section>` : `<section class="card card-pad mt-20"><div class="card-title"><div><h2>Ride it again</h2></div></div><a class="solo-ride" href="#/ride/${rides[0].id}"><span class="solo-ride__date">${esc(fmtDateTime(rides[0].started_at))}</span><span class="solo-ride__stat"><small>Time</small><strong>${esc(fmtDuration(rides[0].duration_s))}</strong></span><span class="solo-ride__stat"><small>Avg speed</small><strong>${esc(fmtSpeed(rides[0].avg_speed_mps))}</strong></span><span class="solo-ride__stat"><small>Avg HR</small><strong>${rides[0].avg_hr ? `${Math.round(rides[0].avg_hr)} bpm` : "—"}</strong></span><span class="solo-ride__cta">Open ride ${icon("chevron-right")}</span></a></section>`}`;
  $$('[data-route-ride]').forEach((row) => { const go = () => { location.hash = `#/ride/${row.dataset.routeRide}`; }; row.addEventListener("click", go); row.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); go(); } }); });
  const xValues = [...new Set([...w140, ...np].map((ride) => ride.started_at))].sort((a, b) => a - b);
  const series = [];
  if (w140.length) {
    const values = Object.fromEntries(w140.map((ride) => [ride.started_at, ride.watts_140]));
    series.push({ name: "W @ 140 bpm", values: xValues.map((date) => values[date]), color: GRAPH.green, width: 2.6, pointRadius: 4, format: "watts" });
  }
  if (np.length) {
    const values = Object.fromEntries(np.map((ride) => [ride.started_at, ride.normalized_power]));
    series.push({ name: "Normalized power", values: xValues.map((date) => values[date]), color: GRAPH.blue, width: 2, pointRadius: 3.5, format: "watts" });
  }
  if (series.length) renderGraph("#route-comparison-chart", { ariaLabel: "Progress on this route over time", x: { values: xValues, type: "time", label: "ride date" }, y: { format: "watts", includeZero: true }, legend: false, series });
  else graphEmpty($("#route-comparison-chart"), "Not enough estimates yet", "Keep riding this route to build a useful comparison.");
}

/* ------------------------------------------------------------------ ride detail */

function fmtRange(lo, hi) {
  const low = finiteValue(lo), high = finiteValue(hi);
  return low == null || high == null ? "" : `${fmtWatts(low)} – ${fmtWatts(high)}`;
}
function metricMarkup(label, value, unit = "", range = "") {
  const hasValue = value !== "—" && value !== "";
  return `<div class="metric"><div class="metric__label">${esc(label)}</div><div class="metric__value">${esc(value)}${hasValue && unit ? `<small>${esc(unit)}</small>` : ""}</div>${range ? `<div class="metric__range">${esc(range)}</div>` : ""}</div>`;
}

async function renderRideDetail(id, token = startPage()) {
  view.innerHTML = `<div class="skeleton skeleton--card"></div><div class="skeleton skeleton--chart mt-24"></div><div class="skeleton skeleton--chart mt-24"></div>`;
  const results = await Promise.allSettled([
    api(`/api/rides/${id}`),
    api(`/api/rides/${id}/series?downsample=1800`),
    api(`/api/rides/${id}/descents`),
  ]);
  const rideResult = results[0], seriesResult = results[1], descentsResult = results[2];
  if (rideResult.status !== "fulfilled") {
    if (pageIsCurrent(token)) view.innerHTML = errorState("Ride could not load", rideResult.reason?.message || "It may have been deleted, or the link is out of date.", retryAction());
    return;
  }
  if (!pageIsCurrent(token)) return;
  const ride = rideResult.value;
  const series = seriesResult.status === "fulfilled" ? seriesResult.value : { gps: [], hr: [], power: [] };
  const descents = descentsResult.status === "fulfilled" ? descentsResult.value : { descents: [] };
  const seriesFailure = seriesResult.status !== "fulfilled";
  const descentsFailure = descentsResult.status !== "fulfilled";
  const metrics = ride.metrics || {};
  const avgPowerRange = fmtRange(metrics.avg_watts_lo, metrics.avg_watts_hi);
  const normalizedPowerRange = fmtRange(metrics.normalized_power_lo, metrics.normalized_power_hi);
  view.innerHTML = `<div class="detail-top"><a class="detail-back" href="#/rides">${icon("chevron-right")}Back to rides</a><div class="detail-actions"><button class="button button--danger button--small" id="delete-ride" type="button">${icon("trash")}Delete ride</button></div></div><section class="detail-hero"><div class="detail-hero__title"><h1>${esc(fmtDate(ride.started_at))}</h1><p class="detail-hero__lede">${esc(fmtTime(ride.started_at))}</p></div><div class="detail-hero__primary">${metricMarkup("Distance", fmtDistance(metrics.distance_m))}${metricMarkup("Moving time", fmtDuration(metrics.duration_s))}${metricMarkup("Climbing", fmtMeters(metrics.elevation_gain_m))}</div><h2 class="detail-hero__subhead">Power</h2><div class="detail-hero__power">${metricMarkup("Average power", metrics.avg_watts != null ? Math.round(metrics.avg_watts) : "—", "W", avgPowerRange)}${metricMarkup("Normalized power", metrics.normalized_power != null ? Math.round(metrics.normalized_power) : "—", "W", normalizedPowerRange)}</div></section><section class="card telemetry-card"><div class="telemetry-head"><div><h2>Ride replay</h2></div><div class="telemetry-tools"><button class="button button--primary button--small" id="replay-play" type="button" aria-pressed="false">${icon("play")}<span>Play replay</span></button></div></div><div class="map-wrap"><div id="ride-map" class="ride-map"></div></div><div class="scrubber"><div class="scrubber__line"><input id="scrub-slider" type="range" min="0" max="0" value="0" aria-label="Scrub through ride"><span id="scrub-time" class="scrubber__time">0:00</span></div><div id="scrub-readout" class="readout"></div></div><details class="descents"><summary class="descents__summary"><span>Descents</span><span id="descents-count" class="detail-count">Loading…</span></summary><div class="descents__body"><p class="descents__hint">Choose a label when the automatic reading is wrong.</p><div id="descents-list" class="list-stack"></div></div></details></section><section class="telemetry-graphs"><article class="card telemetry-graph"><div class="card-title"><div><h2>Elevation &amp; grade</h2></div></div><div id="ch-elev" class="chart chart--small"></div></article><article class="card telemetry-graph"><div class="card-title"><div><h2>Heart rate</h2></div></div><div id="ch-hr" class="chart chart--small"></div></article><article class="card telemetry-graph"><div class="card-title"><div><h2>Power</h2></div></div><div id="ch-power" class="chart chart--small"></div></article><article class="card telemetry-graph"><div class="card-title"><div><h2>Speed</h2></div></div><div id="ch-speed" class="chart chart--small"></div></article></section><article class="card telemetry-graph mt-20"><div class="card-title"><div><h2>Gradient distribution</h2></div></div><div id="ch-grade" class="chart chart--small"></div></article>${metrics.cardiac_drift && metrics.has_hr ? `<article id="drift-card" class="card drift-card"></article>` : ""}`;
  $("#delete-ride").addEventListener("click", async () => { if (!window.confirm("Delete this ride?\n\nThis removes the ride and its derived analysis from the local database.")) return; const button = $("#delete-ride"); button.disabled = true; try { await api(`/api/rides/${id}`, { method: "DELETE" }); if (!pageIsCurrent(token)) return; toast("Ride deleted."); location.hash = "#/rides"; } catch (error) { if (pageIsCurrent(token)) { button.disabled = false; toast(error.message); } } });
  const chartRefs = seriesFailure ? {} : drawTelemetryCharts(series, metrics);
  setupReplay(series, chartRefs, seriesFailure ? [] : descents.descents || [], id);
  if (seriesFailure) {
    const descentCount = $("#descents-count");
    if (descentCount) descentCount.textContent = "Unavailable";
    ["#ride-map", "#ch-elev", "#ch-hr", "#ch-power", "#ch-speed", "#ch-grade", "#descents-list"].forEach((selector) => {
      const target = $(selector);
      if (target) target.innerHTML = errorState("Ride data unavailable", "The timeline could not be loaded.", retryAction());
    });
  } else if (descentsFailure) {
    const descentCount = $("#descents-count");
    if (descentCount) descentCount.textContent = "Unavailable";
    const target = $("#descents-list");
    if (target) target.innerHTML = errorState("Descent review unavailable", "Try again to load the descent list.", retryAction());
  }
  renderDriftCard(metrics.cardiac_drift, metrics.has_hr);
}

function alignNearest(samples, gps) {
  const result = new Array(gps.length).fill(null);
  if (!samples?.length) return result;
  let pointer = 0;
  gps.forEach((point, index) => { while (pointer < samples.length - 1 && Math.abs(samples[pointer + 1].t - point.t) <= Math.abs(samples[pointer].t - point.t)) pointer += 1; if (Math.abs(samples[pointer].t - point.t) < 8) result[index] = samples[pointer]; });
  return result;
}
/* Centred time-windowed mean for display. The per-second power estimate swings
   hard sample-to-sample (measurement noise, not effort), which reads as a
   jagged sawtooth; a ~16 s window makes the effort visible while keeping the
   mean unchanged and the shape intact. Nulls and long pauses never smear. */
function smoothSeries(values, times = null, windowSeconds = 16) {
  const out = new Array(values.length).fill(null);
  const radius = times ? 12 : 8;
  for (let i = 0; i < values.length; i += 1) {
    const value = values[i];
    const t = times?.[i];
    if (!finiteGraphValue(value) || (times && !finiteGraphValue(t))) continue;
    let sum = 0, count = 0;
    for (let j = Math.max(0, i - radius); j <= Math.min(values.length - 1, i + radius); j += 1) {
      const sample = values[j];
      if (!finiteGraphValue(sample)) continue;
      if (times && Math.abs(times[j] - t) > windowSeconds) continue;
      sum += sample; count += 1;
    }
    out[i] = count ? sum / count : value;
  }
  return out;
}
/* Break a telemetry line when consecutive samples are further apart than this
   many seconds — an auto-pause or a recording dropout, not a real signal gap.
   Connecting across it draws a misleading straight line (most visible in HR). */
const TELEMETRY_GAP_S = 5;

function drawTelemetryCharts(series, metrics) {
  const gps = series.gps || [], hr = series.hr || [], power = series.power || [];
  const refs = {};
  const t0 = gps[0]?.t || 0;
  const distance = gps.map((point, index) => point.dist != null ? point.dist / 1000 : index);
  const xLabel = gps.some((point) => point.dist > 0) ? "distance (km)" : "sample";
  const hrAligned = alignNearest(hr, gps);
  const powerAligned = alignNearest(power, gps);
  if (gps.length) {
    refs.elev = renderGraph("#ch-elev", {
      ariaLabel: "Elevation and grade across the ride",
      x: { values: distance, type: "linear", label: xLabel },
      y: { format: "meters" },
      yRight: { format: "percent", includeZero: true },
      series: [
        { name: "Elevation", values: gps.map((point) => point.elev), color: GRAPH.green, width: 2.3, pointRadius: 1.7, points: false, area: true, areaColor: GRAPH.greenSoft, format: "meters" },
        { name: "Grade", values: gps.map((point) => point.grade == null ? null : point.grade * 100), axis: "right", color: GRAPH.orange, width: 1.4, pointRadius: 1.5, points: false, format: "percent" },
      ],
    });
    refs.elev.xOf = (index) => distance[index];
    refs.speed = renderGraph("#ch-speed", {
      ariaLabel: "Speed across the ride",
      x: { values: distance, type: "linear", label: xLabel },
      y: { format: "speed", includeZero: true },
      series: [{ name: "Speed", values: gps.map((point) => point.speed != null ? point.speed * 3.6 : null), color: GRAPH.blue, width: 2.1, pointRadius: 1.7, points: false, format: "speed" }],
    });
    refs.speed.xOf = (index) => distance[index];
  } else {
    graphEmpty($("#ch-elev"), "No GPS track", "This Ride does not contain a usable route.");
    graphEmpty($("#ch-speed"), "No speed track", "This Ride does not contain a usable GPS speed signal.");
  }
  if (hr.length) {
    const hrX = [];
    const hrValues = [];
    for (let i = 0; i < hr.length; i += 1) {
      if (i > 0 && hr[i].t - hr[i - 1].t > TELEMETRY_GAP_S) {
        hrX.push(null);
        hrValues.push(null);
      }
      hrX.push((hr[i].t - t0) / 60);
      hrValues.push(hr[i].hr);
    }
    refs.hr = renderGraph("#ch-hr", {
      ariaLabel: "Heart rate across the ride",
      x: { values: hrX, type: "linear", label: "minutes" },
      y: { format: "bpm" },
      series: [{ name: "Heart rate", values: hrValues, color: GRAPH.ink, width: 1.9, pointRadius: 1.8, points: false, format: "bpm" }],
    });
    refs.hr.xOf = (index) => gps[index] ? (gps[index].t - t0) / 60 : 0;
  } else graphEmpty($("#ch-hr"), "No heart-rate signal", "This Ride was recorded without HR data.");
  if (power.length) {
    /* Display-smooth the estimate and its band together, so the envelope stays
       consistent with the line (raw per-second values remain in the readout's
       band text via the same arrays below). */
    const powerTimes = powerAligned.map((point) => point?.t ?? null);
    const wattsEst = smoothSeries(powerAligned.map((point) => point?.watts_est ?? null), powerTimes);
    const wattsLo = smoothSeries(powerAligned.map((point) => point?.watts_lo ?? null), powerTimes);
    const wattsHi = smoothSeries(powerAligned.map((point) => point?.watts_hi ?? null), powerTimes);
    refs.powerSmoothed = { wattsEst, wattsLo, wattsHi };
    refs.power = renderGraph("#ch-power", {
      ariaLabel: "Power and range across the ride",
      x: { values: distance, type: "linear", label: xLabel },
      y: { format: "watts", includeZero: true, robust: true },
      series: [{
        name: "Power", values: wattsEst, color: GRAPH.orange, width: 2.1, pointRadius: 1.8, points: false, format: "watts",
        band: { lo: wattsLo, hi: wattsHi, pattern: true },
      }],
    });
    refs.power.xOf = (index) => distance[index];
  } else graphEmpty($("#ch-power"), "No power data", "Power points were not stored for this Ride.");
  const distribution = metrics.grade_distribution || [];
  if (distribution.length) renderGraph("#ch-grade", {
    ariaLabel: "Distribution of grade across the ride",
    x: { values: distribution.map((_, index) => index), type: "category", labels: distribution.map((bucket) => `${bucket.from}%`), label: "grade" },
    y: { format: "integer", includeZero: true },
    series: [{ name: "Samples", values: distribution.map((bucket) => bucket.count), type: "bar", pointColors: distribution.map((bucket) => bucket.from >= 3 ? GRAPH.orange : GRAPH.green), opacity: .8, format: "integer" }],
  });
  else graphEmpty($("#ch-grade"), "No grade distribution", "There is not enough elevation data to draw this view.");
  return refs;
}

function descentLabelText(descent) {
  return descent.label === "coast" ? "Freewheeled" : descent.label === "pedal" ? "Pedalled" : descent.label === "brake" ? "Braked" : "To review";
}
function descentColor(label) {
  return label === "coast" ? GRAPH.green : label === "pedal" ? GRAPH.orange : label === "brake" ? GRAPH.ink : GRAPH.muted;
}
function drawDescentHighlights(map, gps, descents) {
  if (!map || !window.L) return null;
  const group = window.L.layerGroup().addTo(map);
  descents.forEach((descent) => {
    const latlngs = [];
    gps.forEach((point) => { if (point.t >= descent.t_start && point.t <= descent.t_end && point.lat != null && point.lon != null) latlngs.push([point.lat, point.lon]); });
    if (latlngs.length > 1) {
      window.L.polyline(latlngs, { color: "#fff", weight: 8, opacity: .85, lineCap: "round" }).addTo(group);
      window.L.polyline(latlngs, { color: descentColor(descent.label), weight: 4, opacity: .95, lineCap: "round" }).addTo(group);
    }
  });
  return group;
}
function descentNeedsReview(descent) {
  return !["coast", "pedal", "brake"].includes(descent.label);
}
function descentActionLabel(value) {
  return value === "clear" ? "Auto" : value === "coast" ? "Freewheel" : value === "pedal" ? "Pedal" : "Brake";
}
function setupDescentTags(rideId, descents, gps, map, seek) {
  const listEl = $("#descents-list");
  if (!listEl) return;
  const pageToken = state.pageToken;
  const t0 = gps[0]?.t || 0;
  let highlight = null;
  let updateVersion = 0;
  let pendingUpdates = 0;

  function orderDescents(items) {
    return [...items].sort((a, b) => Number(descentNeedsReview(b)) - Number(descentNeedsReview(a)) || (a.t_start || 0) - (b.t_start || 0));
  }
  descents = orderDescents(descents);

  async function refresh(version) {
    try {
      const fresh = await api(`/api/rides/${rideId}/descents`);
      if (!pageIsCurrent(pageToken) || version !== updateVersion || pendingUpdates) return false;
      descents = orderDescents(fresh.descents || []);
      redraw();
      return true;
    } catch (error) { if (pageIsCurrent(pageToken) && version === updateVersion) toast(error.message); return false; }
  }

  function redraw() {
    if (highlight) { highlight.remove(); highlight = null; }
    highlight = drawDescentHighlights(map, gps, descents);
    renderList();
  }

  function renderList() {
    const count = $("#descents-count");
    const reviewCount = descents.filter(descentNeedsReview).length;
    if (count) count.textContent = descents.length ? `${reviewCount} to review · ${descents.length} total` : "None detected";
    if (!descents.length) {
      listEl.innerHTML = emptyState("No descents detected", "This ride has no runs below −1% grade.");
      return;
    }
    listEl.innerHTML = descents.map((descent, index) => {
      const rel = (t) => fmtElapsed(Math.max(0, t - t0));
      const current = descent.source === "manual" ? descent.label : "clear";
      const actions = ["clear", "coast", "pedal", "brake"].map((value) => `<button type="button" class="descent-tag${current === value ? " active" : ""}" data-value="${value}" aria-pressed="${current === value}">${descentActionLabel(value)}</button>`).join("");
      return `<div class="list-row"><div class="list-row__main descents__seek" data-index="${index}" role="button" tabindex="0" aria-label="Seek to ${esc(descentLabelText(descent))} descent from ${esc(rel(descent.t_start))} to ${esc(rel(descent.t_end))}"><strong>${esc(descentLabelText(descent))}</strong><small>${esc(rel(descent.t_start))} – ${esc(rel(descent.t_end))}</small></div><div class="descent-control" role="group" aria-label="Classify this descent"><div class="descent-tags">${actions}</div></div></div>`;
    }).join("");

    $$(".descents__seek", listEl).forEach((node) => {
      const descent = descents[+node.dataset.index];
      const activate = () => {
        if (!gps.length) return;
        let nearest = 0;
        gps.forEach((point, i) => { if (Math.abs(point.t - descent.t_start) < Math.abs(gps[nearest].t - descent.t_start)) nearest = i; });
        seek(nearest);
      };
      node.addEventListener("click", activate);
      node.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); activate(); } });
    });

    $$(".descent-tag", listEl).forEach((button) => {
      const row = button.closest(".list-row");
      const descent = descents[+row.querySelector(".descents__seek").dataset.index];
      const group = button.closest(".descent-tags");
      button.addEventListener("click", async () => {
        if (button.dataset.busy) return;
        button.dataset.busy = "1";
        updateVersion += 1;
        pendingUpdates += 1;
        const previous = group.querySelector(".descent-tag.active")?.dataset.value || "clear";
        const value = button.dataset.value;
        const label = value === "clear" ? null : value;
        $$(".descent-tag", group).forEach((item) => { item.disabled = true; item.classList.toggle("active", item === button); item.setAttribute("aria-pressed", String(item === button)); });
        try {
          await api(`/api/rides/${rideId}/coast_segments`, { method: "POST", body: { t_start: descent.t_start, t_end: descent.t_end, label } });
          if (pageIsCurrent(pageToken)) toast(label ? `${descentActionLabel(value)} tag saved.` : "Tag cleared — back to auto.");
        } catch (error) {
          if (pageIsCurrent(pageToken)) {
            $$(".descent-tag", group).forEach((item) => { const active = item.dataset.value === previous; item.disabled = false; item.classList.toggle("active", active); item.setAttribute("aria-pressed", String(active)); });
            toast(error.message);
          }
        }
        if (!pageIsCurrent(pageToken)) return;
        pendingUpdates -= 1;
        delete button.dataset.busy;
        $$(".descent-tag", group).forEach((item) => { item.disabled = false; });
        if (pendingUpdates) return;
        const refreshVersion = updateVersion;
        if (!await refresh(refreshVersion) && pageIsCurrent(pageToken) && refreshVersion === updateVersion && !pendingUpdates) redraw();
      });
    });
  }

  redraw();
}

function setupReplay(series, chartRefs, descents = [], rideId = null) {
  const gps = series.gps || [], hr = series.hr || [], power = series.power || [];
  const slider = $("#scrub-slider"), readout = $("#scrub-readout"), timeLabel = $("#scrub-time"), playButton = $("#replay-play");
  if (!slider || gps.length < 2) {
    if (slider) { slider.disabled = true; slider.setAttribute("aria-valuetext", "Ride replay unavailable"); }
    const count = $("#descents-count"), list = $("#descents-list");
    if (count) count.textContent = "Unavailable";
    if (list) list.innerHTML = errorState("Descent review unavailable", "A ride timeline is needed to place descents.", retryAction());
    return;
  }
  const t0 = gps[0].t, total = Math.max(1, gps[gps.length - 1].t - t0);
  const distances = gps.map((point, index) => point.dist != null ? point.dist / 1000 : index);
  const hrAligned = alignNearest(hr, gps), powerAligned = alignNearest(power, gps);
  const mapPoints = gps.map((point, index) => point.lat != null && point.lon != null ? { index, latlng: [point.lat, point.lon] } : null).filter(Boolean);
  const latlngs = mapPoints.map((point) => point.latlng);
  const mapIndexForGps = new Map(mapPoints.map((point, mapIndex) => [point.index, mapIndex]));
  let playhead = null;
  if (latlngs.length > 1 && window.L) {
    state.map = window.L.map($("#ride-map"), { scrollWheelZoom: false, zoomControl: true });
    /* Voyager keeps the light CARTO family but draws real road contrast;
       light_all was so pale the map nearly vanished against the card. */
    window.L.tileLayer("https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", { attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>', maxZoom: 19, subdomains: "abcd" }).addTo(state.map);
    /* White casing under the route keeps the green readable on any tile. */
    window.L.polyline(latlngs, { color: "#fff", weight: 10, opacity: .92, lineCap: "round", lineJoin: "round" }).addTo(state.map);
    window.L.polyline(latlngs, { color: GRAPH.greenDeep, weight: 5, opacity: 1, lineCap: "round", lineJoin: "round" }).addTo(state.map);
    window.L.circleMarker(latlngs[0], { radius: 5, color: "#fff", weight: 2, fillColor: GRAPH.green, fillOpacity: 1 }).addTo(state.map).bindTooltip("Start");
    window.L.circleMarker(latlngs[latlngs.length - 1], { radius: 5, color: "#fff", weight: 2, fillColor: GRAPH.cursor, fillOpacity: 1 }).addTo(state.map).bindTooltip("Finish");
    playhead = window.L.circleMarker(latlngs[0], { radius: 7, color: "#fff", weight: 3, fillColor: GRAPH.green, fillOpacity: 1 }).addTo(state.map);
    state.map.fitBounds(window.L.latLngBounds(latlngs), { padding: [28, 28] });
    state.map.on("click", (event) => { let closest = 0, best = Infinity; gps.forEach((point, index) => { if (point.lat == null) return; const distance = (point.lat - event.latlng.lat) ** 2 + (point.lon - event.latlng.lng) ** 2; if (distance < best) { best = distance; closest = index; } }); seek(closest); });
  } else $("#ride-map").innerHTML = emptyState("Map route unavailable", "The Ride still has graphs, but there are not enough GPS points to draw a map.");

  function seek(index) {
    const current = Math.max(0, Math.min(gps.length - 1, Math.round(index)));
    slider.value = current;
    const point = gps[current] || {};
    slider.setAttribute("aria-valuetext", `${fmtDuration(point.t - t0)} of ${fmtDuration(total)}`);
    const percent = current / (gps.length - 1) * 100;
    slider.style.setProperty("--progress", `${percent}%`);
    const hrPoint = hrAligned[current];
    const powerPoint = powerAligned[current];
    /* Show the same display-smoothed power the chart draws, so the readout
       matches the line; fall back to the raw point if smoothing is absent. */
    const smoothed = chartRefs.powerSmoothed;
    const wattsEst = smoothed?.wattsEst?.[current] ?? powerPoint?.watts_est;
    const wattsLo = smoothed?.wattsLo?.[current] ?? powerPoint?.watts_lo;
    const wattsHi = smoothed?.wattsHi?.[current] ?? powerPoint?.watts_hi;
    timeLabel.textContent = fmtDuration(point.t - t0);
    /* The note line is always rendered (hidden when empty) so the bar never
       changes height as the band text appears and disappears mid-scrub. */
    readout.innerHTML = [
      ["Time", fmtDuration(point.t - t0), ""],
      ["Distance", fmtDistance(point.dist != null ? point.dist : distances[current] * 1000), ""],
      ["Speed", fmtSpeed(point.speed), ""],
      ["Heart rate", hrPoint?.hr != null ? `${Math.round(hrPoint.hr)} bpm` : "—", ""],
      ["Power", wattsEst != null ? `${Math.round(wattsEst)} W` : "—", wattsHi > 0 ? `${Math.round(wattsLo)}–${Math.round(wattsHi)} W` : ""],
    ].map(([label, value, note]) => `<div class="readout__field"><div class="readout__label">${esc(label)}</div><div class="readout__value">${esc(value)}</div><div class="readout__note${note ? "" : " readout__note--empty"}">${esc(note || "\u00a0")}</div></div>`).join("");
    const mapIndex = mapIndexForGps.get(current);
    if (playhead && mapIndex != null) playhead.setLatLng(latlngs[mapIndex]);
    const cursor = {};
    Object.entries(chartRefs).forEach(([key, ref]) => { if (ref?.el && ref.xOf) cursor[key] = { el: ref, x: ref.xOf(current) }; });
    scheduleCursor(cursor);
    state.replayIndex = current;
  }

  setupDescentTags(rideId, descents, gps, state.map, seek);

  slider.max = gps.length - 1;
  slider.addEventListener("input", () => seek(+slider.value));
  ["elev", "power", "speed", "hr"].forEach((key) => { const ref = chartRefs[key]; if (!ref?.el) return; bindGraphSeek(ref, (value) => { if (key === "hr") { const target = t0 + Number(value) * 60; let nearest = 0; gps.forEach((point, index) => { if (Math.abs(point.t - target) < Math.abs(gps[nearest].t - target)) nearest = index; }); seek(nearest); } else { let nearest = 0; distances.forEach((distance, index) => { if (Math.abs(distance - Number(value)) < Math.abs(distances[nearest] - Number(value))) nearest = index; }); seek(nearest); } }); });
  playButton.addEventListener("click", () => { if (state.playback) { stopPlayback(); setReplayButton(playButton, false); return; } const startIndex = state.replayIndex >= gps.length - 1 ? 0 : state.replayIndex || 0;    const started = performance.now(); const startTime = gps[startIndex].t; const speed = Math.max(1, total / 180); let index = startIndex; function frame(now) { if (!document.body.contains(playButton)) { stopPlayback(); return; } const elapsed = (now - started) / 1000 * speed; const targetTime = startTime + elapsed; while (index < gps.length - 1 && gps[index].t < targetTime) index += 1; seek(index); if (index >= gps.length - 1) { stopPlayback(); setReplayButton(playButton, false); return; } state.playback = requestAnimationFrame(frame); } state.playback = requestAnimationFrame(frame); setReplayButton(playButton, true); });
  seek(0);
}
function setReplayButton(button, playing) {
  if (!button) return;
  button.innerHTML = `${icon(playing ? "pause" : "play")}<span>${playing ? "Pause replay" : "Play replay"}</span>`;
  button.setAttribute("aria-pressed", String(playing));
}
function stopPlayback() { if (state.playback) cancelAnimationFrame(state.playback); state.playback = null; }
function renderDriftCard(drift, hasHr) {
  const el = $("#drift-card"); if (!el) return;
  if (!hasHr || !drift) { el.remove(); return; }
  const positive = drift.drift_bpm_per_hr > 0;
  el.innerHTML = `<div class="card-title"><div><h2>Cardiac drift</h2><p>${esc(drift.duration_min)} min steady effort at approximately ${esc(drift.mean_power)} W</p></div></div><div class="drift-stat-grid"><div class="drift-stat"><div class="label">Drift</div><strong>${positive ? "+" : ""}${esc(drift.drift_bpm_per_hr)} <small>bpm/hr</small></strong></div><div class="drift-stat"><div class="label">Relative</div><strong>${positive ? "+" : ""}${esc(drift.drift_pct_per_hr)} <small>%/hr</small></strong></div><div class="drift-stat"><div class="label">Heart rate</div><strong>${esc(drift.start_hr)} → ${esc(drift.end_hr)} <small>bpm</small></strong></div><div class="drift-stat"><div class="label">Fit r²</div><strong>${Number(drift.r2).toFixed(2)}</strong></div></div><p class="drift-explainer">${positive ? "Heart rate climbed while power stayed steady. Less drift at the same effort over a season is a useful sign of aerobic progress." : "Heart rate fell during this window, the opposite of drift. It may be recovery after a surge or a slowing effort."}</p>`;
}

/* ------------------------------------------------------------------ profile */

async function renderProfile(_, token = startPage()) {
  view.innerHTML = `<div class="skeleton skeleton--card"></div>`;
  const [profileResult, calibrationsResult] = await Promise.allSettled([api("/api/profile"), api("/api/calibrations")]);
  if (profileResult.status !== "fulfilled") {
    if (pageIsCurrent(token)) view.innerHTML = errorState("Profile could not load", profileResult.reason?.message || "Try again to load your profile.", retryAction());
    return;
  }
  if (!pageIsCurrent(token)) return;
  const profile = profileResult.value;
  const calibrations = calibrationsResult.status === "fulfilled" ? calibrationsResult.value : [];
  const calibrationsFailure = calibrationsResult.status === "rejected" ? calibrationsResult.reason : null;
  const rider = profile.rider || {}, bike = profile.bike || {}, zones = rider.hr_zones || [];
  view.innerHTML = `    ${pageHeader("Profile & bike")}<section class="form-layout"><article class="card form-card"><div class="form-section"><h3>Rider</h3><div class="form-grid"><label class="form-field"><span>Age</span><input id="r-age" type="number" value="${esc(fmtNumber(rider.age ?? 40, 0))}"></label><label class="form-field"><span>Weight <em>kg</em></span><input id="r-weight" type="number" step=".5" value="${esc(fmtNumber(rider.weight_kg ?? 75, 1))}"></label><label class="form-field"><span>Height <em>cm</em></span><input id="r-height" type="number" step=".5" value="${esc(fmtNumber(rider.height_cm ?? 178, 1))}"></label><label class="form-field"><span>Resting HR <em>bpm</em></span><input id="r-rest" type="number" value="${esc(fmtNumber(rider.resting_hr ?? 55, 0))}"></label><label class="form-field"><span>Max HR <em>bpm</em></span><input id="r-maxhr" type="number" value="${esc(fmtNumber(rider.max_hr ?? 180, 0))}"></label><label class="form-field">Bike type<select id="r-bike">${["road", "gravel", "mountain", "hybrid", "tt"].map((type) => `<option value="${type}" ${rider.bike_type === type ? "selected" : ""}>${type}</option>`).join("")}</select></label><label class="form-field">Sex<select id="r-sex"><option value="" ${rider.sex ? "" : "selected"}>Not set</option><option value="male" ${rider.sex === "male" ? "selected" : ""}>Male</option><option value="female" ${rider.sex === "female" ? "selected" : ""}>Female</option></select><small class="form-field__hint">Drives the heart-rate calorie estimate and TRIMP load.</small></label></div></div><div class="form-section"><h3>Heart-rate zones</h3><p>Edit the boundaries if you know your own zones. They drive TRIMP and fitness/freshness.</p><div class="zone-grid">${[0,1,2,3,4].map((index) => { const zone = zones[index] || { lo: 0, hi: 0 }; const prevHi = index === 0 ? null : (zones[index - 1] || { hi: 0 }).hi; return `<div class="zone"><strong>Z${index + 1}</strong>${index === 0 ? `<input id="z0-lo" type="number" min="30" max="250" value="${esc(fmtNumber(zone.lo, 0))}" aria-label="Zone 1 lower">` : `<span class="zone__inherit" aria-hidden="true">${fmtNumber(prevHi, 0)}</span>`}<span class="zone__dash" aria-hidden="true">to</span><input id="z${index}-hi" type="number" min="30" max="250" value="${esc(fmtNumber(zone.hi, 0))}" aria-label="Zone ${index + 1} upper${index === 0 ? "" : ` (from ${fmtNumber(prevHi, 0)})`}"></div>`; }).join("")}</div></div></article><article class="card form-card"><div class="form-section"><h3>Bike</h3><div class="form-grid form-grid--two"><label class="form-field"><span>Name</span><input id="b-name" type="text" value="${esc(bike.name || "Road bike")}"></label><label class="form-field"><span>Mass <em>kg</em></span><input id="b-mass" type="number" step=".1" value="${esc(fmtNumber(bike.mass_kg ?? 9, 1))}"></label><label class="form-field">Rolling resistance<input id="b-crr" type="number" step=".0001" value="${esc(fmtNumber(bike.crr ?? .005, 4))}" title="Crr — how much the tyres resist rolling. Lower is faster on flats."></label><label class="form-field">Drag area CdA<input id="b-cda" type="number" step=".01" value="${esc(fmtNumber(bike.cdA ?? .35, 2))}" title="CdA = drag coefficient × frontal area. Lower means more aero."></label><label class="form-field">Drivetrain efficiency<input id="b-eff" type="number" step=".01" value="${esc(fmtNumber(bike.drivetrain_efficiency ?? .97, 2))}"></label></div></div><div class="form-section"><div class="calibration-note"><strong>${bike.calibrated ? "Bike calibration is active" : "Default assumptions are active"}</strong>${bike.calibrated ? "Your recent calibration is active." : "A closed loop with coasting descents can fit the bike to your riding."}</div><div class="save-row"><button class="button button--primary" id="save-profile">Save changes</button></div></div></article></section><section class="card card-pad mt-20"><div class="card-title"><div><h2>Calibration</h2></div><button class="button button--primary button--small" id="run-pooled">Run pooled calibration</button></div><details class="calibration-disclosure"><summary>View calibration history</summary><div id="calibration-list"></div></details></section>`;
  const fieldIds = ["r-age", "r-weight", "r-height", "r-rest", "r-maxhr", "b-name", "b-mass", "b-crr", "b-cda", "b-eff", "z0-lo", ...[0,1,2,3,4].map((index) => `z${index}-hi`)];
  fieldIds.forEach((id) => $(`#${id}`)?.addEventListener("input", () => {
    const field = $(`#${id}`);
    field.classList.remove("invalid");
    field.setAttribute("aria-invalid", "false");
  }));
  $("#save-profile").addEventListener("click", async () => {
    const num = (id) => Number($(`#${id}`).value);
    const bikeName = $("#b-name").value.trim();
    const checks = [["r-age", num("r-age") >= 13 && num("r-age") <= 100], ["r-weight", num("r-weight") >= 30 && num("r-weight") <= 200], ["r-height", num("r-height") >= 100 && num("r-height") <= 250], ["r-rest", num("r-rest") >= 30 && num("r-rest") <= 120], ["r-maxhr", num("r-maxhr") > num("r-rest") && num("r-maxhr") <= 240], ["b-name", bikeName.length >= 1 && bikeName.length <= 80], ["b-mass", num("b-mass") > 0 && num("b-mass") <= 50], ["b-crr", num("b-crr") > 0 && num("b-crr") <= .05], ["b-cda", num("b-cda") > 0 && num("b-cda") <= 1.5], ["b-eff", num("b-eff") > 0 && num("b-eff") <= 1]];
    let invalid = false; checks.forEach(([id, valid]) => {
      const field = $(`#${id}`);
      field.classList.toggle("invalid", !valid);
      field.setAttribute("aria-invalid", String(!valid));
      invalid ||= !valid;
    });
    const hrZones = [];
    for (let index = 0; index < 5; index++) {
      const lo = index === 0 ? num("z0-lo") : hrZones[index - 1].hi;
      const hi = num(`z${index}-hi`);
      const valid = Number.isFinite(lo) && Number.isFinite(hi) && lo >= 30 && hi <= 250 && lo < hi;
      if (index === 0) {
        $("#z0-lo").classList.toggle("invalid", !valid);
        $("#z0-lo").setAttribute("aria-invalid", String(!valid));
      }
      const upper = $(`#z${index}-hi`);
      upper.classList.toggle("invalid", !valid);
      upper.setAttribute("aria-invalid", String(!valid));
      invalid ||= !valid;
      hrZones.push({ lo, hi });
    }
    if (invalid) { toast("Check the highlighted fields."); return; }
    const saveButton = $("#save-profile"), saveLabel = saveButton.textContent;
    saveButton.disabled = true; saveButton.textContent = "Saving…";
    try { await api("/api/profile", { method: "PUT", body: { rider: { age: num("r-age"), weight_kg: num("r-weight"), height_cm: num("r-height"), resting_hr: num("r-rest"), max_hr: num("r-maxhr"), bike_type: $("#r-bike").value, sex: $("#r-sex").value || null, hr_zones: hrZones }, bike: { id: bike.id, name: bikeName, mass_kg: num("b-mass"), crr: num("b-crr"), cdA: num("b-cda"), drivetrain_efficiency: num("b-eff"), calibrated: bike.calibrated } } }); if (pageIsCurrent(token)) toast("Profile saved and rides recalculated."); } catch (error) { if (pageIsCurrent(token)) toast(error.message); }
    if (pageIsCurrent(token)) { saveButton.disabled = false; saveButton.textContent = saveLabel; }
  });
  renderCalibrationList(calibrations, calibrationsFailure);
  $("#run-pooled")?.addEventListener("click", async () => {
    const button = $("#run-pooled");
    const label = button.textContent;
    button.disabled = true; button.textContent = "Fitting…";
    try {
      const result = await api("/api/calibrate/pooled", { method: "POST", body: {} });
      if (!pageIsCurrent(token)) return;
      const c = result.calibration || {};
      toast(`Pooled calibration applied — ${c.n_rides} rides, ${c.n_segments} segments.`);
      const [calibrations] = await Promise.all([api("/api/calibrations"), refreshBikeFields(bike, token)]);
      if (!pageIsCurrent(token)) return;
      renderCalibrationList(calibrations);
    } catch (error) {
      if (pageIsCurrent(token)) toast(error.message);
    }
    if (pageIsCurrent(token)) { button.disabled = false; button.textContent = label; }
  });
}

async function refreshBikeFields(bikeRef, token) {
  /* After a pooled calibration the bike's Crr/CdA and calibrated flag change
     server-side; keep the Bike form, status note, and the in-memory bike
     object (used by the save handler) in step without losing unsaved edits in
     the other fields. */
  try {
    const { bike } = await api("/api/profile");
    if (!pageIsCurrent(token)) return;
    const crr = $("#b-crr"), cda = $("#b-cda");
    if (crr) crr.value = fmtNumber(bike.crr, 4);
    if (cda) cda.value = fmtNumber(bike.cdA, 2);
    if (bikeRef) {
      bikeRef.crr = bike.crr;
      bikeRef.cdA = bike.cdA;
      bikeRef.calibrated = bike.calibrated;
    }
    const note = document.querySelector(".calibration-note");
    if (note) {
      note.innerHTML = `<strong>${bike.calibrated ? "Bike calibration is active" : "Default assumptions are active"}</strong>${bike.calibrated ? "Your recent calibration is active." : "A closed loop with coasting descents can fit the bike to your riding."}`;
    }
  } catch (_) { /* non-fatal: the form is still correct on next page load */ }
}

function renderCalibrationList(calibrations, failure = null) {
  const list = $("#calibration-list");
  if (!list) return;
  if (failure) {
    list.innerHTML = errorState("Calibration history unavailable", "Try again to load the calibration history.", retryAction());
    return;
  }
  if (!calibrations.length) {
    list.innerHTML = emptyState("No calibration yet", "Ride a closed loop with coasting descents and the engine will fit Crr and CdA here. Climbs are recorded for visibility only.");
    return;
  }
  list.innerHTML = `<div class="table-scroll calibration-table"><table class="ride-table"><thead><tr><th>Ride</th><th>Type</th><th title="Rolling-resistance coefficient">Crr</th><th title="Drag area — coefficient of drag × frontal area">CdA</th><th title="Fit quality of the calibration">R²</th><th>Segments</th></tr></thead><tbody>${calibrations.map((calibration) => {
    const typeLabel = calibration.type === "loop" ? "loop" : calibration.type === "pooled" ? "pooled" : "climb · diagnostic";
    const ride = calibration.type === "pooled"
      ? `${calibration.params?.n_rides || "—"} rides`
      : esc(calibration.filename || calibration.ride_id);
    const segments = calibration.params?.n_rides
      ? `${calibration.params.n_segments || "—"} · ${calibration.params.n_rides} rides`
      : (calibration.params?.n_segments || "—");
    return `<tr><td>${ride}</td><td>${esc(typeLabel)}</td><td class="mono">${calibration.params?.crr ? Number(calibration.params.crr).toFixed(4) : "—"}</td><td class="mono">${calibration.params?.cdA ? Number(calibration.params.cdA).toFixed(2) : "—"}</td><td class="mono">${calibration.r2 != null ? Number(calibration.r2).toFixed(2) : "—"}</td><td class="mono">${segments}</td></tr>`;
  }).join("")}</tbody></table></div>`;
}

/* ------------------------------------------------------------------ shell */

function toast(message) { const el = $("#toast"); el.textContent = message; el.hidden = false; clearTimeout(el._timer); el._timer = setTimeout(() => { el.hidden = true; }, 3800); }
function init() {
  setupMobileNav();
  document.addEventListener("click", (event) => {
    const overviewRetry = event.target.closest("[data-retry-overview]");
    if (overviewRetry) { event.preventDefault(); retryOverview(overviewRetry.dataset.retryOverview, overviewRetry); return; }
    const retry = event.target.closest("[data-retry-page]");
    if (retry) { event.preventDefault(); navigate(); }
  });
  navigate();
}
init();
