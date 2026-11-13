// ppedit browser editor
"use strict";

const S = {
  meta: null,          // columns, tables, builtin cases
  state: null,         // editor state from the server
  view: "network",
  selected: { bus: new Set(), gen: new Set(), branch: new Set(), gencost: new Set(), dcline: new Set(), dclinecost: new Set(), areas: new Set() },
  filter: "",
  pos: {},             // bus_i -> {x, y}
  zoom: { k: 1, x: 0, y: 0 },
  pick: null,          // {kind: "bus"|"branch"|"dcline", index}
  style: (() => { try { return localStorage.getItem("ppedit.diagram") || "simple"; } catch (_) { return "simple"; } })(),
  layout: (() => { try { return localStorage.getItem("ppedit.layout") || "force"; } catch (_) { return "force"; } })(),
  areas: (() => { try { return localStorage.getItem("ppedit.areas") === "1"; } catch (_) { return false; } })(), // color buses by area
  map: (() => {
    const def = { imagery: false, roads: false, states: true, stateNames: true, counties: false, countyNames: false };
    try { return { ...def, ...JSON.parse(localStorage.getItem("ppedit.map") || "{}") }; } catch (_) { return def; }
  })(),
};

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const h = (tag, attrs = {}, ...kids) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined) el.append(kid);
  return el;
};
const SVGNS = "http://www.w3.org/2000/svg";
const sv = (tag, attrs = {}) => {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
};
const TABLE_TITLES = { bus: "bus", gen: "generator", branch: "branch", gencost: "generator cost", dcline: "DC line", dclinecost: "DC line cost", areas: "area" };
const fmt = (v, d = 3) => (v === null || v === undefined) ? "–" : (typeof v === "number" ? v.toFixed(d) : v);

// ---------------------------------------------------------------- server

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-PPEdit": "1" },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
  if (!res.ok || data.error) {
    const err = new Error(data.error || `HTTP ${res.status}`);
    err.confirm = !!data.confirm;
    throw err;
  }
  return data;
}

async function act(path, body, { quiet = false } = {}) {
  try {
    const data = await api(path, body ?? {});
    setState(data);
    return data;
  } catch (err) {
    if (!quiet) toast(err.message, true);
    throw err;
  }
}

let toastTimer = null;
function toast(msg, error = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (error ? " error" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), error ? 5000 : 2500);
}

// ---------------------------------------------------------------- state

function setState(st) {
  const prev = S.state;
  S.state = st;
  const c = st.case;
  // drop selections that no longer exist
  for (const t of S.meta.tables) {
    S.selected[t] = new Set([...S.selected[t]].filter(i => i < c.tables[t].length));
  }
  if (S.pick && S.pick.index >= c.tables[S.pick.kind].length) S.pick = null;
  if (!prev || prev.case.name !== c.name || st.cascade || prev.path !== st.path) {
    // nothing extra; full render below
  }
  renderChrome();
  renderView();
}

function issuesFor(table) {
  const map = new Map();
  for (const is of S.state.issues) {
    if (is.table !== table || is.row === null) continue;
    const key = `${is.row}:${is.column ?? ""}`;
    const cur = map.get(key);
    if (!cur || (cur.level === "warning" && is.level === "error")) map.set(key, is);
    const rowKey = `${is.row}:*`;
    const r = map.get(rowKey);
    if (!r || (r.level === "warning" && is.level === "error")) map.set(rowKey, is);
  }
  return map;
}

// ---------------------------------------------------------------- chrome

function renderChrome() {
  const st = S.state, c = st.case;
  const name = $("#case-name");
  if (document.activeElement !== name) name.value = c.name;
  $("#dirty").hidden = !st.dirty;
  $("#case-path").textContent = st.path || "not saved to a file";
  $("#case-path").title = st.path || "";
  $("#save-btn").disabled = !st.path;
  $("#undo-btn").disabled = !st.can_undo;
  $("#redo-btn").disabled = !st.can_redo;
  for (const el of [$("#baseMVA"), $("#version")]) {
    if (document.activeElement !== el) el.value = el.id === "baseMVA" ? c.baseMVA : c.version;
  }
  for (const t of S.meta.tables) $(`[data-count="${t}"]`).textContent = c.tables[t].length;
  const errs = st.issues.filter(i => i.level === "error").length;
  const warns = st.issues.length - errs;
  const ic = $("#issue-count");
  ic.textContent = st.issues.length || "";
  ic.className = "count issues" + (errs ? " has-err" : warns ? " has-warn" : "");
  ic.title = `${errs} error(s), ${warns} warning(s)`;
  const dot = $("#result-dot");
  dot.className = "status-dot" + (st.result ? (st.result.success ? " ok" : " fail") : "");
  document.title = `${st.dirty ? "• " : ""}${c.name} — ppedit`;
  $$("#tabs > button").forEach(b => b.classList.toggle("active", b.dataset.view === S.view));
}

function show(view) {
  S.view = view;
  renderChrome();
  renderView();
}

function renderView() {
  const isTable = S.meta.tables.includes(S.view);
  $$(".view").forEach(v => v.classList.remove("active"));
  if (isTable) { $("#view-table").classList.add("active"); renderTable(S.view); }
  else if (S.view === "network") { $("#view-network").classList.add("active"); renderNetwork(); }
  else if (S.view === "results") { $("#view-results").classList.add("active"); renderResults(); }
  else { $("#view-issues").classList.add("active"); renderIssues(); }
}

// ---------------------------------------------------------------- tables

function cellEditor(table, row, col, value) {
  const spec = S.meta.columns[table][col];
  const attrs = { "data-row": row, "data-col": spec.name, "aria-label": `${spec.name} row ${row + 1}` };
  if (spec.kind === "enum") {
    const sel = h("select", attrs,
      Object.entries(spec.options).map(([k, v]) => h("option", { value: k, selected: String(value) === k }, `${k} ${v}`)));
    return sel;
  }
  return h("input", { ...attrs, value: value, class: spec.kind === "text" ? "text" : null, spellcheck: "false" });
}

function rowLabel(table, i) {
  const t = S.state.case.tables;
  if (table === "gencost") {
    const ngen = t.gen.length;
    if (!ngen) return `${i + 1}`;
    const g = i % ngen, q = i >= ngen;
    return `${q ? "Q " : ""}gen ${g + 1}${t.gen[g] ? ` @${t.gen[g][0]}` : ""}`;
  }
  if (table === "dclinecost") {
    const d = t.dcline[i];
    return `dcline ${i + 1}${d ? ` ${d[0]}→${d[1]}` : ""}`;
  }
  return `${i + 1}`;
}

function renderTable(table) {
  const grid = $("#grid");
  const cols = S.meta.columns[table];
  const rows = S.state.case.tables[table];
  const active = document.activeElement;
  const focus = active && active.dataset && active.dataset.row !== undefined && grid.contains(active)
    ? { row: active.dataset.row, col: active.dataset.col, start: active.selectionStart, end: active.selectionEnd } : null;
  const issues = issuesFor(table);
  const filt = S.filter.trim().toLowerCase();
  const sel = S.selected[table];

  const head = h("thead", {}, h("tr", {},
    h("th", { class: "rowhead" }, h("input", {
      type: "checkbox", title: "Select all",
      checked: rows.length > 0 && sel.size === rows.length,
      onchange: e => { S.selected[table] = new Set(e.target.checked ? rows.map((_, i) => i) : []); renderTable(table); },
    })),
    cols.map(c => h("th", { title: c.desc }, c.name, h("span", { class: "unit" }, c.unit || " ")))));
  const body = h("tbody");
  rows.forEach((r, i) => {
    if (filt && !r.join(" ").toLowerCase().includes(filt) && !rowLabel(table, i).toLowerCase().includes(filt)) return;
    const rowIssue = issues.get(`${i}:*`);
    const tr = h("tr", { class: sel.has(i) ? "selected" : null, "data-index": i },
      h("td", { class: "rowhead", title: rowIssue ? rowIssue.message : null },
        h("label", {}, rowLabel(table, i), h("input", {
          type: "checkbox", checked: sel.has(i),
          onchange: e => { e.target.checked ? sel.add(i) : sel.delete(i); tr.classList.toggle("selected", e.target.checked); updateTableHint(); },
        }))));
    cols.forEach((c, k) => {
      const is = issues.get(`${i}:${c.name}`);
      tr.append(h("td", { class: is ? (is.level === "error" ? "err" : "warn") : null, title: is ? is.message : null },
        cellEditor(table, i, k, r[k])));
    });
    body.append(tr);
  });
  grid.replaceChildren(head, body);
  if (!rows.length) {
    grid.append(h("tbody", {}, h("tr", {}, h("td", { colspan: cols.length + 1, class: "empty" },
      h("div", { class: "big" }, `No ${TABLE_TITLES[table]} rows`),
      table === "bus" ? "Add a bus to start a model, or open a built-in case from New." : "Use + Add row."))));
  }
  if (focus) {
    const el = grid.querySelector(`[data-row="${focus.row}"][data-col="${focus.col}"]`);
    if (el) { el.focus(); try { el.setSelectionRange(focus.start, focus.end); } catch (_) {} }
  }
  updateTableHint();
}

function updateTableHint() {
  const t = S.view, n = S.selected[t]?.size || 0;
  $("#table-hint").textContent = n ? `${n} selected` : "Enter or Tab commits an edit · hover headers for descriptions";
  $("[data-action=del-rows]").disabled = !n;
  $("[data-action=dup-row]").disabled = !n;
}

async function commitCell(el) {
  const table = S.view;
  const row = +el.dataset.row, column = el.dataset.col;
  const k = S.meta.columns[table].findIndex(c => c.name === column);
  const old = S.state.case.tables[table][row][k];
  if (String(old) === el.value) return;
  try {
    const st = await act("/api/cell", { table, row, column, value: el.value });
    if (st.cascade) {
      const msg = table === "bus" && column === "bus_i" ? "Bus renumbered; generators, branches, DC lines and areas updated" :
        table === "areas" && column === "area" ? "Area renumbered; its buses moved with it" :
        table === "gencost" || table === "dclinecost" ? "ncost updated to match the cost data" : "Related values updated";
      toast(msg);
    }
  } catch (_) {
    el.value = old;
    el.parentElement.classList.add("bad");
    setTimeout(() => el.parentElement && el.parentElement.classList.remove("bad"), 1500);
  }
}

// ---------------------------------------------------------------- rows

async function addRow() {
  const table = S.view;
  const st = await act("/api/rows", { table });
  flashRow(st.row);
}

async function duplicateRows() {
  const table = S.view;
  const rows = [...S.selected[table]].sort((a, b) => a - b);
  let last = null;
  for (const i of rows) {
    const values = [...S.state.case.tables[table][i]];
    if (table === "bus" || table === "areas") values[0] = Math.max(0, ...S.state.case.tables[table].map(r => r[0])) + 1;
    const st = await act("/api/rows", { table, values, after: S.state.case.tables[table].length - 1 });
    last = st.row;
  }
  if (last !== null) flashRow(last);
}

async function deleteRows() {
  const table = S.view;
  const rows = [...S.selected[table]];
  if (!rows.length) return;
  let cascade = false;
  if (table === "bus") {
    const nums = new Set(rows.map(i => S.state.case.tables.bus[i][0]));
    const ng = S.state.case.tables.gen.filter(g => nums.has(g[0])).length;
    const nb = S.state.case.tables.branch.filter(b => nums.has(b[0]) || nums.has(b[1])).length;
    const nd = S.state.case.tables.dcline.filter(b => nums.has(b[0]) || nums.has(b[1])).length;
    const na = S.state.case.tables.areas.filter(a => nums.has(a[1])).length;
    if (ng || nb || nd || na) {
      if (!confirm(`Deleting ${rows.length} bus(es) also deletes ${ng} generator(s), ${nb} branch(es) and ${nd} DC line(s) connected to them` +
        (na ? `, and ${na} area row(s) that use them as price reference bus` : "") + ". Continue?")) return;
      cascade = true;
    }
  }
  const st = await act("/api/rows/delete", { table, rows, cascade });
  S.selected[table].clear();
  renderTable(table);
  const r = st.removed;
  toast(Object.entries(r).filter(([, v]) => v).map(([k, v]) => `${v} ${k}`).join(", ") + " deleted (Ctrl+Z to undo)");
}

function flashRow(i) {
  requestAnimationFrame(() => {
    const tr = $(`#grid tr[data-index="${i}"]`);
    if (!tr) return;
    tr.scrollIntoView({ block: "center" });
    $$("td", tr).forEach(td => td.classList.add("flash"));
    const first = $("input:not([type=checkbox]), select", tr);
    if (first) first.focus();
  });
}

// ---------------------------------------------------------------- network

function vColor(vm) {
  // diverging: low (red) .. 1.0 (neutral) .. high (blue)
  const t = Math.max(-1, Math.min(1, (vm - 1) / 0.06));
  const lerp = (a, b, u) => Math.round(a + (b - a) * u);
  const mid = [222, 226, 221], lo = [193, 54, 38], hi = [36, 99, 170];
  const end = t < 0 ? lo : hi, u = Math.abs(t);
  return `rgb(${lerp(mid[0], end[0], u)},${lerp(mid[1], end[1], u)},${lerp(mid[2], end[2], u)})`;
}
const TYPE_COLOR = { 1: "#7d8883", 2: "#2e6fb0", 3: "#1d2321", 4: "#d0d4cf" };

function layout(force = false) {
  const c = S.state.case;
  const buses = c.tables.bus.map(b => b[0]);
  const ids = new Set(buses);
  const edges = [...c.tables.branch, ...c.tables.dcline].filter(b => ids.has(b[0]) && ids.has(b[1]) && b[0] !== b[1]).map(b => [b[0], b[1]]);
  const adj = new Map(buses.map(b => [b, []]));
  edges.forEach(([a, b]) => { adj.get(a).push(b); adj.get(b).push(a); });
  const missing = buses.filter(b => force || !S.pos[b]);
  if (!missing.length) return;
  const n = buses.length;
  const R = 60 * Math.sqrt(n);
  missing.forEach((b, i) => {
    const nb = adj.get(b).map(x => S.pos[x]).filter(Boolean);
    if (nb.length && !force) {
      const x = nb.reduce((s, p) => s + p.x, 0) / nb.length, y = nb.reduce((s, p) => s + p.y, 0) / nb.length;
      S.pos[b] = { x: x + (Math.random() - .5) * 40, y: y + (Math.random() - .5) * 40 };
    } else {
      const a = 2 * Math.PI * i / Math.max(1, missing.length);
      S.pos[b] = { x: R * Math.cos(a) + (Math.random() - .5) * 10, y: R * Math.sin(a) + (Math.random() - .5) * 10 };
    }
  });
  // force-directed refinement (Fruchterman-Reingold style)
  const moving = new Set(force ? buses : missing.length > n / 3 ? buses : missing);
  const P = buses.map(b => S.pos[b]);
  const idx = new Map(buses.map((b, i) => [b, i]));
  const E = edges.map(([a, b]) => [idx.get(a), idx.get(b)]);
  const k = 55, iters = n > 200 ? 220 : 400;
  let temp = R / 4;
  for (let it = 0; it < iters; it++) {
    const D = P.map(() => ({ x: 0, y: 0 }));
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
      let dx = P[i].x - P[j].x, dy = P[i].y - P[j].y;
      let d2 = dx * dx + dy * dy;
      if (d2 < 0.01) { dx = Math.random(); dy = Math.random(); d2 = 1; }
      if (d2 > 160000) continue;
      const f = k * k / d2;
      D[i].x += dx * f; D[i].y += dy * f; D[j].x -= dx * f; D[j].y -= dy * f;
    }
    for (const [a, b] of E) {
      const dx = P[a].x - P[b].x, dy = P[a].y - P[b].y;
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01, f = d / k;
      D[a].x -= dx * f; D[a].y -= dy * f; D[b].x += dx * f; D[b].y += dy * f;
    }
    for (let i = 0; i < n; i++) {
      if (!moving.has(buses[i])) continue;
      D[i].x -= P[i].x * 0.01; D[i].y -= P[i].y * 0.01; // gentle gravity
      const d = Math.hypot(D[i].x, D[i].y) || 1;
      const m = Math.min(d, temp);
      P[i].x += D[i].x / d * m; P[i].y += D[i].y / d * m;
    }
    temp *= 0.985;
  }
}

function fitView() {
  const svg = $("#net");
  const P = positions();
  const pts = S.style === "oneline" ? onelineLayout().corners : S.state.case.tables.bus.map(b => P[b[0]]).filter(Boolean);
  if (!pts.length) { S.zoom = { k: 1, x: svg.clientWidth / 2, y: svg.clientHeight / 2 }; return; }
  const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const W = svg.clientWidth - (S.pick ? 330 : 0), H = svg.clientHeight;
  const kmax = useGeo() ? 0.05 : 4; // geodata: metres, at most 20 m per pixel
  const k = Math.min(kmax, Math.min((W - 80) / Math.max(1, x1 - x0), (H - 80) / Math.max(1, y1 - y0)));
  S.zoom = { k, x: W / 2 - k * (x0 + x1) / 2, y: H / 2 - k * (y0 + y1) / 2 };
}

let needFit = true;
// classic style: blue = low, red = high (voltage and loading)
function vColorClassic(vm) { return vColor(2 - vm); }
function loadColor(L) { return vColor(1 - (Math.min(L, 150) - 50) / 50 * 0.06); }
const NEUTRAL_LINE = "#8d9792";
const DC_COLOR = "#7b4fa0"; // matches --dc in style.css
const AREA_COLORS = ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2", "#edc948", "#b07aa1", "#9c755f", "#ff9da7", "#8cd17d"];

function areaList() {
  // area numbers used by buses or listed in the areas table, in order
  const c = S.state.case;
  return [...new Set([...c.tables.bus.map(b => b[6]), ...c.tables.areas.map(a => a[0])])].sort((a, b) => a - b);
}
function areaColors() {
  // area number -> color when area coloring is on and there is more than one area, else null
  const list = areaList();
  if (!S.areas || list.length < 2) return null;
  return new Map(list.map((a, i) => [a, AREA_COLORS[i % AREA_COLORS.length]]));
}
function areaLegend(colors) {
  if (!colors) return;
  const refs = new Map(S.state.case.tables.areas.map(a => [a[0], a[1]]));
  const L = $("#legend");
  L.append(h("div", {}, "Areas (halo color; ★ price reference bus)"),
    h("div", { class: "row wrap" }, [...colors].map(([a, col]) =>
      h("span", { class: "area-key", title: refs.has(a) ? `area ${a}, price reference bus ${refs.get(a)}` : `area ${a} (no areas row)` },
        h("span", { class: "sw", style: `background:${col}` }), `${a}`))));
}
function setAreas(on) {
  S.areas = on;
  try { localStorage.setItem("ppedit.areas", on ? "1" : "0"); } catch (_) { /* storage unavailable */ }
  renderNetwork();
}

function busSummary() {
  // per-bus generation, load, voltage and connectivity for labels and shapes
  const c = S.state.case, res = S.state.result && S.state.result.success ? S.state.result : null;
  const gen = new Map(), ngen = new Map(), conn = new Set();
  c.tables.gen.forEach((g, i) => {
    ngen.set(g[0], (ngen.get(g[0]) || 0) + 1);
    if (g[7] !== 1) return;
    const pg = res && res.gen[i] ? res.gen[i].Pg : g[1];
    gen.set(g[0], (gen.get(g[0]) || 0) + pg);
  });
  c.tables.branch.forEach(b => { if (b[10] === 1 && b[0] !== b[1]) { conn.add(b[0]); conn.add(b[1]); } });
  const volt = new Map(c.tables.bus.map(b => [b[0], { Vm: b[7], Va: b[8] }]));
  if (res) res.bus.forEach(b => volt.set(b.bus_i, { Vm: b.Vm, Va: b.Va }));
  return { gen, ngen, conn, volt, res };
}

function renderNetwork() {
  updateNetTools();
  if (S.style === "oneline") return renderOneline();
  const svg = $("#net");
  const c = S.state.case, res = S.state.result && S.state.result.success ? S.state.result : null;
  const classic = S.style === "classic";
  const geo = useGeo();
  if (!geo) layout();
  if (needFit && svg.clientWidth) { fitView(); needFit = false; }
  $("#net-style").value = S.style;
  svg.classList.toggle("classic", classic);
  svg.classList.toggle("geo", geo);
  svg.classList.toggle("imagery", geo && S.map.imagery);
  // draw in screen coordinates so symbols and text keep their size when zooming
  const { k, x, y } = S.zoom;
  const POS = positions(), G = geo ? geoLayout() : null;
  const W = id => { const p = POS[id]; return p ? { x: p.x * k + x, y: p.y * k + y } : null; };
  const g = sv("g");
  const ids = new Set(c.tables.bus.map(b => b[0]));
  const sum = busSummary();
  const errBus = new Set(S.state.issues.filter(i => i.table === "bus" && i.level === "error" && i.row !== null).map(i => i.row));
  const areaCol = areaColors(), priceRef = new Set(c.tables.areas.map(a => a[1]));
  const labels = sv("g"); // line labels drawn above lines, below nodes
  // classic style: arrow in the direction of real power flow and |MW| label at mid-line
  const flowMark = (p, q, mw, color) => {
    const dx = q.x - p.x, dy = q.y - p.y, len = Math.hypot(dx, dy) || 1;
    if (len <= 30) return;
    const ux = dx / len, uy = dy / len;
    const fwd = mw >= 0;
    const t = 0.5;
    const ax = p.x + dx * t, ay = p.y + dy * t;
    const sx = fwd ? ux : -ux, sy = fwd ? uy : -uy;
    const a = 4;
    labels.append(sv("path", {
      d: `M${ax + sx * a},${ay + sy * a} L${ax - sx * a - sy * a * 0.8},${ay - sy * a + sx * a * 0.8} L${ax - sx * a + sy * a * 0.8},${ay - sy * a - sx * a * 0.8} Z`,
      class: "flow-arrow", fill: color || NEUTRAL_LINE,
    }));
    let ang = Math.atan2(dy, dx) * 180 / Math.PI;
    if (ang > 90) ang -= 180; else if (ang < -90) ang += 180;
    const nx = -uy, ny = ux, off = 7;
    const lx = ax + nx * off * (ny < 0 ? -1 : 1), ly = ay + ny * off * (ny < 0 ? -1 : 1);
    const txt = sv("text", { x: lx, y: ly, class: "flow-label", "text-anchor": "middle", "dominant-baseline": "middle", transform: `rotate(${ang} ${lx} ${ly})` });
    txt.textContent = `${Math.abs(mw).toFixed(1)} MW`;
    labels.append(txt);
  };
  // edges
  c.tables.branch.forEach((b, i) => {
    const p = W(b[0]), q = W(b[1]);
    if (!ids.has(b[0]) || !ids.has(b[1]) || !p || !q) return;
    const xfmr = (b[8] && b[8] !== 0) || b[9];
    const cls = ["edge"];
    if (xfmr && !classic) cls.push("xfmr");
    if (b[10] === 0) cls.push("out");
    if (S.pick && S.pick.kind === "branch" && S.pick.index === i) cls.push("sel");
    const line = sv("line", { x1: p.x, y1: p.y, x2: q.x, y2: q.y, class: cls.join(" ") });
    const rb = res && res.branch[i] && b[10] === 1 ? res.branch[i] : null;
    if (rb && rb.loading_pct !== null && !cls.includes("sel")) {
      const L = rb.loading_pct;
      if (classic) {
        line.style.stroke = loadColor(L);
        line.style.strokeWidth = L > 100 ? 3.5 : 2.4;
      } else {
        line.style.stroke = L > 100 ? "#b42318" : L > 80 ? "#c27c0e" : "#5f8f80";
        line.style.strokeWidth = 1.2 + Math.min(L, 120) / 30;
      }
    } else if (classic && b[10] === 1 && !cls.includes("sel")) {
      line.style.strokeWidth = 2;
    }
    const hit = sv("line", { x1: p.x, y1: p.y, x2: q.x, y2: q.y, class: "edge-hit" });
    const title = sv("title");
    title.textContent = `branch ${i + 1}: ${b[0]} → ${b[1]}` + (rb ? `\nPF ${fmt(rb.PF, 1)} MW, QF ${fmt(rb.QF, 1)} MVAr, loading ${rb.loading_pct === null ? "unrated" : fmt(rb.loading_pct, 0) + "%"}` : "");
    hit.append(title);
    hit.addEventListener("click", e => { e.stopPropagation(); pick("branch", i); });
    g.append(line, hit);
    if (classic) {
      const dx = q.x - p.x, dy = q.y - p.y, len = Math.hypot(dx, dy) || 1;
      const ux = dx / len, uy = dy / len;
      if (xfmr) { // two interlocking circles a quarter of the way from the from bus
        const mx = p.x + dx * 0.25, my = p.y + dy * 0.25;
        for (const s of [-3, 3]) g.append(sv("circle", { cx: mx + ux * s, cy: my + uy * s, r: 4.5, class: "xfmr-sym" }));
      }
      if (rb) flowMark(p, q, rb.PF, line.style.stroke);
    }
  });
  // DC lines: dash-dot lines with a "DC" converter tag near the from bus
  c.tables.dcline.forEach((d, i) => {
    const p = W(d[0]), q = W(d[1]);
    if (!ids.has(d[0]) || !ids.has(d[1]) || !p || !q || d[0] === d[1]) return;
    const on = d[2] === 1;
    const rd = res && res.dcline && res.dcline[i] && res.dcline[i].solved ? res.dcline[i] : null;
    const cls = ["edge", "dcline"];
    if (!on) cls.push("out");
    if (S.pick && S.pick.kind === "dcline" && S.pick.index === i) cls.push("sel");
    g.append(sv("line", { x1: p.x, y1: p.y, x2: q.x, y2: q.y, class: cls.join(" ") }));
    const dx = q.x - p.x, dy = q.y - p.y;
    if (Math.hypot(dx, dy) > 40) {
      const mx = p.x + dx * 0.25, my = p.y + dy * 0.25;
      labels.append(sv("rect", { x: mx - 8, y: my - 5.5, width: 16, height: 11, rx: 2, class: "dc-sym" }));
      const tx = sv("text", { x: mx, y: my + 0.5, class: "dc-sym-text", "text-anchor": "middle", "dominant-baseline": "middle" });
      tx.textContent = "DC";
      labels.append(tx);
    }
    if (rd && classic) flowMark(p, q, rd.Pf, DC_COLOR);
    const hit = sv("line", { x1: p.x, y1: p.y, x2: q.x, y2: q.y, class: "edge-hit" });
    const title = sv("title");
    title.textContent = `DC line ${i + 1}: ${d[0]} → ${d[1]}${on ? "" : " OUT"}` +
      (rd ? `\nPf ${fmt(rd.Pf, 1)} MW, Pt ${fmt(rd.Pt, 1)} MW, loss ${fmt(rd.loss_MW, 2)} MW` : `\nPf ${fmt(d[3], 1)} MW (case value)`);
    hit.append(title);
    hit.addEventListener("click", e => { e.stopPropagation(); pick("dcline", i); });
    g.append(hit);
  });
  g.append(labels);
  // nodes
  c.tables.bus.forEach((b, i) => {
    const p = W(b[0]);
    if (!p) return;
    const cls = ["node"];
    if (S.pick && S.pick.kind === "bus" && S.pick.index === i) cls.push("sel");
    if (errBus.has(i)) cls.push("err");
    if (G && G.estimated.has(b[0])) cls.push("nogeo");
    const node = sv("g", { class: cls.join(" "), transform: `translate(${p.x},${p.y})`, "data-bus": b[0] });
    const v = sum.volt.get(b[0]);
    const hasGen = sum.ngen.get(b[0]) > 0, hasLoad = b[2] !== 0 || b[3] !== 0;
    if (areaCol) {
      node.append(sv("circle", { r: 15, class: "area-halo", fill: areaCol.get(b[6]) }));
      if (priceRef.has(b[0])) {
        const star = sv("text", { x: -15, y: -9, class: "area-star", "text-anchor": "middle" });
        star.textContent = "★";
        node.append(star);
      }
    }
    let r;
    if (classic) {
      const isolated = b[1] === 4 || (c.tables.bus.length > 1 && !sum.conn.has(b[0]));
      if (isolated) cls.push("isolated");
      const fill = isolated ? "none" : vColorClassic(v.Vm);
      let shape;
      if (hasGen) { r = 8; shape = sv("circle", { r, class: "body", fill }); }
      else if (hasLoad) { r = 9; shape = sv("path", { d: `M0,${-r} L${r * 0.95},${r * 0.7} L${-r * 0.95},${r * 0.7} Z`, class: "body", fill }); }
      else { r = 6.5; shape = sv("rect", { x: -r, y: -r, width: 2 * r, height: 2 * r, class: "body", fill }); }
      node.setAttribute("class", cls.join(" "));
      node.append(shape);
      if (b[1] === 3) node.append(sv("circle", { r: r + 4, class: "ref-ring" }));
      const lines = [[`${b[0]}`, "num"], [`${v.Va.toFixed(2)}°`, ""]];
      if (hasGen) lines.push([`G ${(sum.gen.get(b[0]) || 0).toFixed(1)} MW`, "gen-mw"]);
      if (b[2] !== 0) lines.push([`L ${b[2].toFixed(1)} MW`, "load-mw"]);
      const label = sv("text", { x: r + 5, y: -r + 2, class: "ol-label" });
      lines.forEach(([t, c2], j) => {
        const ts = sv("tspan", { x: r + 5, dy: j ? "1.15em" : 0, class: c2 });
        ts.textContent = t;
        label.append(ts);
      });
      node.append(label);
    } else {
      r = b[1] === 3 ? 9 : 7;
      if (hasGen) node.append(sv("circle", { r: r + 4, class: "gen" }));
      node.append(sv("circle", { r, class: "body", fill: sum.res ? vColor(v.Vm) : TYPE_COLOR[b[1]] || "#999" }));
      if (b[2] > 0) node.append(sv("path", { d: `M${r + 2},${r + 1} l6,6 m-3,0 h3 v-3`, class: "load" }));
      const label = sv("text", { x: r + 5, y: -r - 1 });
      label.textContent = b[0];
      node.append(label);
    }
    const title = sv("title");
    title.textContent = `bus ${b[0]} (${S.meta.columns.bus[1].options[b[1]]}), area ${b[6]}${priceRef.has(b[0]) ? " (price reference bus)" : ""}\nPd ${b[2]} MW, Qd ${b[3]} MVAr\nVm ${fmt(v.Vm, 4)} pu, Va ${fmt(v.Va, 2)}°${sum.res ? " (solution)" : " (case values)"}`;
    node.append(title);
    node.addEventListener("pointerdown", e => startDrag(e, b[0], i));
    g.append(node);
  });
  svg.classList.remove("oneline");
  svg.replaceChildren(...(geo ? [renderMap(svg.clientWidth, svg.clientHeight)] : []), g);
  renderLegend(!!res, classic);
  areaLegend(areaCol);
  if (G && (G.estimated.size || G.missing)) {
    const L = $("#legend");
    if (G.estimated.size) L.append(h("div", {}, `Dashed: ${G.estimated.size} bus(es) without geodata, placed near neighbors`));
    if (G.missing) L.append(h("div", {}, `${G.missing} bus(es) without geodata not shown`));
  }
  renderInspector();
  if (!c.tables.bus.length) {
    const t = sv("text", { x: svg.clientWidth / 2, y: svg.clientHeight / 2, "text-anchor": "middle", fill: "#69726e" });
    t.textContent = "Empty case — add buses in the Buses tab, or open a built-in case from New ▾";
    svg.append(t);
  }
}

// ---------------------------------------------------------------- one-line style
//
// Layered orthogonal layout in the manner of a utility one-line diagram: bus bars
// are horizontal and branches vertical. Rows are the hop distance from the nearest
// generator bus (sources on top), split so that no branch joins two buses of the
// same row; buses are ordered within rows by barycenter sweeps (crossing reduction).
// Every generator, load, branch and DC line gets its own column and each bus bar spans its
// own columns; bars that would overlap in a row move to an extra track. The diagram
// is drawn in layout units under one transform, so it scales as a whole.

const OL = { dx: 34, gen: 112, load: 112, bare: 30, gap: 84, label: 80, pad: 12 };
let olCache = null;

function onelineLayout() {
  const T = S.state.case.tables;
  const buses = T.bus.map(b => b[0]);
  const ids = new Set(buses);
  const brs = T.branch.map((b, i) => [b, i]).filter(([b]) => ids.has(b[0]) && ids.has(b[1]) && b[0] !== b[1]);
  const dcs = T.dcline.map((d, i) => [d, i]).filter(([d]) => ids.has(d[0]) && ids.has(d[1]) && d[0] !== d[1]);
  const sig = JSON.stringify([buses, T.bus.map(b => b[2] !== 0 || b[3] !== 0), brs.map(([b]) => [b[0], b[1]]), T.gen.map(g => g[0]), dcs.map(([d]) => [d[0], d[1]])]);
  if (olCache && olCache.sig === sig) return olCache;
  const adj = new Map(buses.map(b => [b, new Set()]));
  [...brs, ...dcs].forEach(([b]) => { adj.get(b[0]).add(b[1]); adj.get(b[1]).add(b[0]); });
  // levels: hops from the nearest generator bus; an island without one starts at REF or its best-connected bus
  const level = new Map();
  const bfs = starts => {
    let q = starts.filter(s => !level.has(s));
    q.forEach(s => level.set(s, 0));
    while (q.length) {
      const nq = [];
      for (const a of q) for (const b of adj.get(a)) if (!level.has(b)) { level.set(b, level.get(a) + 1); nq.push(b); }
      q = nq;
    }
  };
  const genBus = new Set(T.gen.map(g => g[0]).filter(b => ids.has(b)));
  bfs(buses.filter(b => genBus.has(b)));
  const typ = new Map(T.bus.map(b => [b[0], b[1]]));
  [...buses].sort((a, b) => (typ.get(b) === 3) - (typ.get(a) === 3) || adj.get(b).size - adj.get(a).size)
    .forEach(b => { if (!level.has(b)) bfs([b]); });
  // rows: split each level by greedy coloring so every branch joins two different rows
  const nlev = Math.max(-1, ...level.values()) + 1;
  const rows = [];
  for (let lv = 0; lv < nlev; lv++) {
    const members = buses.filter(b => level.get(b) === lv).sort((a, b) => adj.get(b).size - adj.get(a).size);
    const color = new Map();
    for (const b of members) {
      const used = new Set([...adj.get(b)].filter(v => color.has(v)).map(v => color.get(v)));
      let k = 0;
      while (used.has(k)) k++;
      color.set(b, k);
    }
    const nk = members.length ? Math.max(...color.values()) + 1 : 0;
    for (let k = 0; k < nk; k++) rows.push(members.filter(b => color.get(b) === k).sort((a, b) => a - b));
  }
  // order within rows: alternating barycenter sweeps
  const rowOf = new Map();
  rows.forEach((r, i) => r.forEach(b => rowOf.set(b, i)));
  const u = new Map();
  const place = r => r.forEach((b, i) => u.set(b, (i + 0.5) / r.length));
  rows.forEach(place);
  for (let it = 0; it < 12; it++) {
    const down = it % 2 === 0;
    const order = rows.map((_, i) => down ? i : rows.length - 1 - i);
    for (const ri of order) {
      const key = new Map(rows[ri].map(b => {
        let nb = [...adj.get(b)].filter(v => down ? rowOf.get(v) < ri : rowOf.get(v) > ri);
        if (!nb.length) nb = [...adj.get(b)];
        return [b, nb.length ? nb.reduce((s, v) => s + u.get(v), 0) / nb.length : u.get(b)];
      }));
      rows[ri].sort((a, b) => key.get(a) - key.get(b) || u.get(a) - u.get(b));
      place(rows[ri]);
    }
  }
  // columns: one per generator, load, branch and DC line (lines sit between their two buses)
  const slots = [], own = new Map(buses.map(b => [b, []]));
  const add = (key, kind, ref, ...on) => {
    const s = { key, kind, ref, x: 0 };
    slots.push(s);
    on.forEach(b => own.get(b).push(s));
    return s;
  };
  T.gen.forEach((g, i) => { if (ids.has(g[0])) add(u.get(g[0]) - 1e-9, "gen", i, g[0]); });
  T.bus.forEach(b => { if (b[2] !== 0 || b[3] !== 0) add(u.get(b[0]) + 1e-9, "load", b[0], b[0]); });
  brs.forEach(([b, i]) => add((u.get(b[0]) + u.get(b[1])) / 2, "branch", i, b[0], b[1]));
  dcs.forEach(([d, i]) => add((u.get(d[0]) + u.get(d[1])) / 2, "dcline", i, d[0], d[1]));
  buses.forEach(b => { if (!own.get(b).length) add(u.get(b), "bare", b, b); });
  slots.sort((a, b) => a.key - b.key);
  slots.forEach((s, i) => (s.x = i * OL.dx));
  const bar = new Map(buses.map(b => {
    const xs = own.get(b).map(s => s.x);
    return [b, { x0: Math.min(...xs) - OL.pad, x1: Math.max(...xs) + OL.pad, y: 0 }];
  }));
  // tracks: bars (with their labels at the left end) that overlap in a row get their own band
  const bands = [];
  for (const r of rows) {
    const ends = [], track = new Map();
    [...r].sort((a, b) => bar.get(a).x0 - bar.get(b).x0).forEach(b => {
      const B = bar.get(b);
      let t = ends.findIndex(e => e < B.x0 - OL.label);
      if (t < 0) { t = ends.length; ends.push(0); }
      ends[t] = B.x1 + 8;
      track.set(b, t);
    });
    ends.forEach((_, t) => bands.push(r.filter(b => track.get(b) === t)));
  }
  let y = 0;
  for (const band of bands) {
    const has = kind => band.some(b => own.get(b).some(s => s.kind === kind));
    y += has("gen") ? OL.gen : OL.bare;
    band.forEach(b => (bar.get(b).y = y));
    y += (has("load") ? OL.load : OL.bare) + OL.gap;
  }
  const xs0 = [...bar.values()].map(B => B.x0 - OL.label), xs1 = [...bar.values()].map(B => B.x1);
  const corners = buses.length ? [{ x: Math.min(...xs0), y: 0 }, { x: Math.max(...xs1), y: y - OL.gap }] : [];
  const col = kind => new Map(slots.filter(s => s.kind === kind).map(s => [s.ref, s.x]));
  const ys = [...new Set([...bar.values()].map(B => B.y))].sort((a, b) => a - b);
  olCache = { sig, bar, ys, corners, gen: col("gen"), load: col("load"), branch: col("branch"), dcline: col("dcline") };
  return olCache;
}

function renderOneline() {
  const svg = $("#net");
  const c = S.state.case, T = c.tables, res = S.state.result && S.state.result.success ? S.state.result : null;
  const L = onelineLayout();
  if (needFit && svg.clientWidth) { fitView(); needFit = false; }
  $("#net-style").value = S.style;
  svg.classList.remove("classic");
  svg.classList.add("oneline");
  const { k, x, y } = S.zoom;
  const g = sv("g", { transform: `matrix(${k},0,0,${k},${x},${y})` });
  const wires = sv("g"), bars = sv("g"), lines = sv("g"), text = sv("g");
  const showText = k >= 0.35; // labels would be unreadably small
  const sum = busSummary();
  const errBus = new Set(S.state.issues.filter(i => i.table === "bus" && i.level === "error" && i.row !== null).map(i => i.row));
  const busIndex = new Map(T.bus.map((b, i) => [b[0], i]));
  const num = v => (Math.abs(v) < 0.05 ? 0 : v).toFixed(1);
  const tip = (el, s) => { const t = sv("title"); t.textContent = s; el.append(t); return el; };
  const onPick = (el, kind, index) => {
    el.addEventListener("pointerdown", e => e.stopPropagation());
    el.addEventListener("click", e => { e.stopPropagation(); pick(kind, index); });
    return el;
  };
  // MW to the left and MVAr to the right of a vertical line, centered at yc, reading upward
  const flowLabels = (xx, yc, p, q) => {
    if (!showText) return;
    const a = sv("text", { x: xx - 4, y: yc, class: "ol-flow", "text-anchor": "middle", transform: `rotate(-90 ${xx - 4} ${yc})` });
    a.textContent = `${num(p)} MW`;
    const b = sv("text", { x: xx + 4, y: yc, class: "ol-flow q", "text-anchor": "middle", "dominant-baseline": "hanging", transform: `rotate(-90 ${xx + 4} ${yc})` });
    b.textContent = `${num(q)} MVAr`;
    text.append(a, b);
  };
  // disconnect switch between ya (pivot, bus side) and yb; open blades swing out 35 degrees
  const discSwitch = (xx, ya, yb, closed) => {
    const sgn = Math.sign(yb - ya), len = Math.abs(yb - ya);
    const ex = closed ? xx : xx + len * Math.sin(0.61), ey = closed ? yb : ya + sgn * len * Math.cos(0.61);
    const grp = sv("g", { class: "ol-switch" + (closed ? "" : " open") });
    grp.append(sv("line", { x1: xx, y1: ya, x2: ex, y2: ey }),
      sv("circle", { cx: xx, cy: ya, r: 1.8 }), sv("circle", { cx: xx, cy: yb, r: 1.8 }));
    if (!closed) grp.append(sv("line", { x1: xx - 4, y1: yb, x2: xx + 4, y2: yb }));
    wires.append(grp);
  };
  const breaker = (xx, yc, closed) =>
    lines.append(sv("rect", { x: xx - 5, y: yc - 5, width: 10, height: 10, class: "ol-breaker" + (closed ? "" : " open") }));
  const arrow = (xx, yc, downward) => {
    const s = downward ? 1 : -1;
    lines.append(sv("path", { d: `M${xx - 4},${yc - 4 * s} L${xx + 4},${yc - 4 * s} L${xx},${yc + 4 * s} Z`, class: "ol-arrow" }));
  };
  // generators: circle with a sine wave above the bar, joined by a pseudo-line and pseudo-switch
  T.gen.forEach((gr, i) => {
    const B = L.bar.get(gr[0]), xx = L.gen.get(i);
    if (!B || xx === undefined) return;
    const on = gr[7] === 1, yb = B.y, gc = yb - OL.gen + 16;
    const p = on ? (res && res.gen[i] ? res.gen[i].Pg : gr[1]) : 0, q = on ? (res && res.gen[i] ? res.gen[i].Qg : gr[2]) : 0;
    wires.append(sv("line", { x1: xx, y1: yb, x2: xx, y2: yb - 12, class: "ol-pseudo" }),
      sv("line", { x1: xx, y1: yb - 28, x2: xx, y2: gc + 12, class: "ol-pseudo" + (on ? "" : " dead") }));
    discSwitch(xx, yb - 12, yb - 28, on);
    const sym = onPick(sv("g", { class: "ol-gen" + (on ? "" : " dead") }), "bus", busIndex.get(gr[0]));
    sym.append(sv("circle", { cx: xx, cy: gc, r: 12 }),
      sv("path", { d: `M${xx - 7},${gc} c2,-6 5,-6 7,0 s5,6 7,0`, class: "wave" }));
    tip(sym, `gen ${i + 1} at bus ${gr[0]} (${on ? "in service" : "out of service"})\nPg ${num(p)} MW, Qg ${num(q)} MVAr`);
    wires.append(sym);
    flowLabels(xx, yb - 58, p, q);
    if (showText) {
      const t = sv("text", { x: xx, y: gc - 16, class: "ol-name", "text-anchor": "middle" });
      t.textContent = `G${i + 1}`;
      text.append(t);
    }
  });
  // loads: arrow below the bar, joined by a pseudo-line and pseudo-switch (open when the bus is NONE)
  T.bus.forEach((b, i) => {
    const xx = L.load.get(b[0]);
    if (xx === undefined) return;
    const on = b[1] !== 4, yb = L.bar.get(b[0]).y, ya = yb + OL.load - 26;
    const p = on ? b[2] : 0, q = on ? b[3] : 0;
    wires.append(sv("line", { x1: xx, y1: yb, x2: xx, y2: yb + 12, class: "ol-pseudo" }),
      sv("line", { x1: xx, y1: yb + 28, x2: xx, y2: ya, class: "ol-pseudo" + (on ? "" : " dead") }));
    discSwitch(xx, yb + 12, yb + 28, on);
    const sym = onPick(sv("path", { d: `M${xx - 7},${ya} L${xx + 7},${ya} L${xx},${ya + 13} Z`, class: "ol-load" + (on ? "" : " dead") }), "bus", i);
    tip(sym, `load at bus ${b[0]}${on ? "" : " (bus NONE: disconnected)"}\nPd ${num(p)} MW, Qd ${num(q)} MVAr`);
    wires.append(sym);
    flowLabels(xx, yb + 57, p, q);
  });
  // branches: vertical lines between bars, breakers at both ends, transformer symbol, flow labels
  T.branch.forEach((b, i) => {
    const xx = L.branch.get(i);
    if (xx === undefined) return;
    const A = L.bar.get(b[0]), Z = L.bar.get(b[1]);
    const fromTop = A.y <= Z.y, ya = Math.min(A.y, Z.y), yb = Math.max(A.y, Z.y), len = yb - ya;
    const on = b[10] === 1, xfmr = (b[8] && b[8] !== 0) || b[9];
    const rb = res && res.branch[i] && on ? res.branch[i] : null;
    const cls = ["ol-branch"];
    if (!on) cls.push("out");
    const sel = S.pick && S.pick.kind === "branch" && S.pick.index === i;
    if (sel) cls.push("sel");
    lines.append(sv("line", { x1: xx, y1: ya + 6, x2: xx, y2: yb - 6, class: "ol-halo" }));
    const line = sv("line", { x1: xx, y1: ya, x2: xx, y2: yb, class: cls.join(" ") });
    if (rb && rb.loading_pct !== null && !sel) line.style.stroke = loadColor(rb.loading_pct);
    lines.append(line,
      sv("circle", { cx: xx, cy: ya, r: 2.8, class: "ol-dot" }), sv("circle", { cx: xx, cy: yb, r: 2.8, class: "ol-dot" }));
    breaker(xx, ya + 20, on);
    breaker(xx, yb - 20, on);
    // symbols and labels go in the first gap below the upper bar, clear of any bar the line crosses
    const seg = Math.min(len, (L.ys.find(v => v > ya + 1) ?? yb) - ya);
    if (xfmr) {
      const ym = ya + seg * 0.3;
      for (const d of [-5, 5]) lines.append(sv("circle", { cx: xx, cy: ym + d, r: 7, class: "ol-xfmr-bg" }));
      for (const d of [-5, 5]) lines.append(sv("circle", { cx: xx, cy: ym + d, r: 7, class: "ol-xfmr" }));
    }
    const yl = ya + seg * (xfmr ? 0.62 : 0.5);
    if (rb) {
      // signed flow from the upper bus toward the lower bus
      const p = fromTop ? rb.PF : rb.PT, q = fromTop ? rb.QF : rb.QT;
      flowLabels(xx, yl, p, q);
      if (Math.abs(p) >= 0.05) arrow(xx, yl, p > 0);
    }
    const hit = onPick(sv("line", { x1: xx, y1: ya, x2: xx, y2: yb, class: "edge-hit" }), "branch", i);
    tip(hit, `branch ${i + 1}: ${b[0]} → ${b[1]}${xfmr ? " (transformer)" : ""}${on ? "" : " OUT"}` +
      (rb ? `\nPF ${fmt(rb.PF, 1)} MW, QF ${fmt(rb.QF, 1)} MVAr, loading ${rb.loading_pct === null ? "unrated" : fmt(rb.loading_pct, 0) + "%"}` : ""));
    lines.append(hit);
  });
  // DC lines: dash-dot vertical lines with a converter (box with a diagonal) at each end
  const converter = (xx, yc, on) => {
    lines.append(sv("rect", { x: xx - 6, y: yc - 6, width: 12, height: 12, class: "ol-conv" + (on ? "" : " dead") }),
      sv("line", { x1: xx - 6, y1: yc + 6, x2: xx + 6, y2: yc - 6, class: "ol-conv" + (on ? "" : " dead") }));
  };
  T.dcline.forEach((d, i) => {
    const xx = L.dcline.get(i);
    if (xx === undefined) return;
    const A = L.bar.get(d[0]), Z = L.bar.get(d[1]);
    const fromTop = A.y <= Z.y, ya = Math.min(A.y, Z.y), yb = Math.max(A.y, Z.y), len = yb - ya;
    const on = d[2] === 1;
    const rd = res && res.dcline && res.dcline[i] && res.dcline[i].solved ? res.dcline[i] : null;
    const cls = ["ol-dcline"];
    if (!on) cls.push("out");
    if (S.pick && S.pick.kind === "dcline" && S.pick.index === i) cls.push("sel");
    lines.append(sv("line", { x1: xx, y1: ya + 6, x2: xx, y2: yb - 6, class: "ol-halo" }),
      sv("line", { x1: xx, y1: ya, x2: xx, y2: yb, class: cls.join(" ") }),
      sv("circle", { cx: xx, cy: ya, r: 2.8, class: "ol-dot" }), sv("circle", { cx: xx, cy: yb, r: 2.8, class: "ol-dot" }));
    converter(xx, ya + 20, on);
    converter(xx, yb - 20, on);
    const seg = Math.min(len, (L.ys.find(v => v > ya + 1) ?? yb) - ya);
    const yl = ya + seg * 0.5;
    if (rd) {
      // signed flow from the upper bus toward the lower bus (Qf, Qt are injections into the buses)
      const p = fromTop ? rd.Pf : -rd.Pt, q = fromTop ? -rd.Qf : -rd.Qt;
      flowLabels(xx, yl, p, q);
      if (Math.abs(p) >= 0.05) arrow(xx, yl, p > 0);
    }
    const hit = onPick(sv("line", { x1: xx, y1: ya, x2: xx, y2: yb, class: "edge-hit" }), "dcline", i);
    tip(hit, `DC line ${i + 1}: ${d[0]} → ${d[1]}${on ? "" : " OUT"}` +
      (rd ? `\nPf ${fmt(rd.Pf, 1)} MW, Pt ${fmt(rd.Pt, 1)} MW, loss ${fmt(rd.loss_MW, 2)} MW` : ""));
    lines.append(hit);
  });
  // bus bars with number and voltage at the left end (on an area-colored band when areas are shown)
  const areaCol = areaColors(), priceRef = new Set(T.areas.map(a => a[1]));
  T.bus.forEach((b, i) => {
    const B = L.bar.get(b[0]), v = sum.volt.get(b[0]);
    if (areaCol) bars.append(sv("rect", { x: B.x0 - 6, y: B.y - 9, width: B.x1 - B.x0 + 12, height: 18, rx: 4, class: "area-band", fill: areaCol.get(b[6]) }));
    const cls = ["ol-bar"];
    if (b[1] === 4 || (T.bus.length > 1 && !sum.conn.has(b[0]))) cls.push("isolated");
    if (errBus.has(i)) cls.push("err");
    if (S.pick && S.pick.kind === "bus" && S.pick.index === i) cls.push("sel");
    bars.append(sv("line", { x1: B.x0, y1: B.y, x2: B.x1, y2: B.y, class: cls.join(" ") }));
    const hit = onPick(sv("rect", { x: B.x0 - 4, y: B.y - 7, width: B.x1 - B.x0 + 8, height: 14, class: "ol-bar-hit" }), "bus", i);
    tip(hit, `bus ${b[0]} (${S.meta.columns.bus[1].options[b[1]]}), area ${b[6]}${priceRef.has(b[0]) ? " (price reference bus)" : ""}\nVm ${fmt(v.Vm, 4)} pu, Va ${fmt(v.Va, 2)}°${sum.res ? " (solution)" : " (case values)"}`);
    bars.append(hit);
    if (showText) {
      const t = sv("text", { x: B.x0 - 6, y: B.y - 3, class: "ol-bus", "text-anchor": "end" });
      const t1 = sv("tspan", { x: B.x0 - 6, class: "num" });
      t1.textContent = `${areaCol && priceRef.has(b[0]) ? "★ " : ""}${b[1] === 3 ? "REF " : ""}${b[0]}`;
      const t2 = sv("tspan", { x: B.x0 - 6, dy: "1.2em" });
      t2.textContent = `${v.Vm.toFixed(3)}∠${v.Va.toFixed(1)}°`;
      t.append(t1, t2);
      text.append(t);
    }
  });
  g.append(wires, bars, lines, text);
  svg.replaceChildren(g);
  renderLegend(!!res, false, true);
  areaLegend(areaCol);
  renderInspector();
  if (!T.bus.length) {
    const t = sv("text", { x: svg.clientWidth / 2, y: svg.clientHeight / 2, "text-anchor": "middle", fill: "#69726e" });
    t.textContent = "Empty case — add buses in the Buses tab, or open a built-in case from New ▾";
    svg.append(t);
  }
}

function renderLegend(solved, classic, oneline = false) {
  const L = $("#legend");
  if (oneline) {
    const shape = (svgInner) => {
      const s = sv("svg", { width: 18, height: 18, viewBox: "-9 -9 18 18" });
      s.innerHTML = svgInner;
      return s;
    };
    const st = 'stroke="#3d4744" stroke-width="1.3"';
    L.replaceChildren(
      h("div", { class: "row" }, shape(`<circle r="7" fill="#fff" ${st}/><path d="M-4.5,0 c1.3,-4 3.2,-4 4.5,0 s3.2,4 4.5,0" fill="none" ${st}/>`), "generator"),
      h("div", { class: "row" }, shape(`<path d="M-6,-4 L6,-4 L0,6 Z" fill="#3d4744"/>`), "load"),
      h("div", { class: "row" }, shape(`<circle cy="-3.5" r="5" fill="none" ${st}/><circle cy="3.5" r="5" fill="none" ${st}/>`), "transformer"),
      h("div", { class: "row" }, shape(`<rect x="-4.5" y="-4.5" width="9" height="9" fill="#b42318" stroke="#7a1810"/>`), "breaker closed",
        shape(`<rect x="-4.5" y="-4.5" width="9" height="9" fill="#fff" stroke="#2e7d4f" stroke-width="1.6"/>`), "open"),
      h("div", { class: "row" }, shape(`<line x1="0" y1="-8" x2="0" y2="8" ${st} stroke-dasharray="3 2"/><line x1="0" y1="-5" x2="0" y2="5" ${st}/>`), "switch closed",
        shape(`<line x1="0" y1="-5" x2="5" y2="2" stroke="#2e7d4f" stroke-width="1.4"/><line x1="-3" y1="5" x2="3" y2="5" stroke="#2e7d4f" stroke-width="1.4"/>`), "open"),
      h("div", { class: "row" }, shape(`<line x1="-8" y1="0" x2="8" y2="0" stroke="#1d2321" stroke-width="4"/>`), "bus: number, Vm∠Va"),
      h("div", { class: "row" }, shape(`<line x1="0" y1="-9" x2="0" y2="9" stroke="${DC_COLOR}" stroke-width="2" stroke-dasharray="5 2 1.5 2"/><rect x="-4.5" y="-4.5" width="9" height="9" fill="#fff" stroke="${DC_COLOR}" stroke-width="1.2"/><line x1="-4.5" y1="4.5" x2="4.5" y2="-4.5" stroke="${DC_COLOR}" stroke-width="1"/>`), "DC line with converters"),
      h("div", {}, "Dashed: generator/load connection (pseudo-line)"),
      h("div", {}, "Flows MW | MVAr, + = downward (into the bus from a"),
      h("div", {}, "generator, out of it to a load); arrow = MW direction"),
      h("div", {}, solved ? "Line loading (% of rateA)" : "Branch flows and loading shown after a solve"),
      ...(solved ? [h("div", { class: "row" }, "0%", h("span", { class: "bar", style: `background:linear-gradient(90deg,${loadColor(0)},${loadColor(50)},${loadColor(100)})` }), "100%+")] : []));
    return;
  }
  if (classic) {
    const shape = (svgInner) => {
      const s = sv("svg", { width: 16, height: 16, viewBox: "-8 -8 16 16" });
      s.innerHTML = svgInner;
      return s;
    };
    const st = 'stroke="#4b5652" stroke-width="1.3"';
    L.replaceChildren(
      h("div", { class: "row" }, shape(`<circle r="6" fill="#dee2dd" ${st}/>`), "generator"),
      h("div", { class: "row" }, shape(`<path d="M0,-6.5 L6,4.5 L-6,4.5 Z" fill="#dee2dd" ${st}/>`), "load"),
      h("div", { class: "row" }, shape(`<rect x="-5" y="-5" width="10" height="10" fill="#dee2dd" ${st}/>`), "bus"),
      h("div", { class: "row" }, shape(`<rect x="-5" y="-5" width="10" height="10" fill="none" ${st}/>`), "disconnected"),
      h("div", { class: "row" }, shape(`<circle r="3" fill="#dee2dd" ${st}/><circle r="6.5" fill="none" ${st}/>`), "reference bus"),
      h("div", {}, `Voltage magnitude (pu)${solved ? "" : " — case values; run PF for solution"}`),
      h("div", { class: "row" }, "0.94", h("span", { class: "bar", style: `background:linear-gradient(90deg,${vColorClassic(.94)},${vColorClassic(1)},${vColorClassic(1.06)})` }), "1.06"),
      h("div", {}, solved ? "Line loading (% of rateA), MW flow labels" : "Line loading and MW flows shown after a solve"),
      h("div", { class: "row" }, "0%", h("span", { class: "bar", style: `background:linear-gradient(90deg,${loadColor(0)},${loadColor(50)},${loadColor(100)})` }), "100%+"),
      h("div", { class: "row" }, h("span", { style: `color:${DC_COLOR}` }, "–·– DC"), "DC line"),
      h("div", {}, "Labels: bus, angle, G = generation, L = load"));
  } else if (solved) {
    L.replaceChildren(
      h("div", {}, "Voltage magnitude (pu)"),
      h("div", { class: "row" }, "0.94", h("span", { class: "bar", style: `background:linear-gradient(90deg,${vColor(.94)},${vColor(1)},${vColor(1.06)})` }), "1.06"),
      h("div", { class: "row" }, "Line loading: ", h("span", { style: "color:#5f8f80" }, "■ <80%"), h("span", { style: "color:#c27c0e" }, "■ 80–100%"), h("span", { style: "color:#b42318" }, "■ >100%")),
      h("div", { class: "row" }, h("span", { style: `color:${DC_COLOR}` }, "–·– DC"), "DC line"));
  } else {
    L.replaceChildren(
      ...[[3, "REF"], [2, "PV"], [1, "PQ"], [4, "isolated"]].map(([t, n]) =>
        h("div", { class: "row" }, h("span", { class: "sw", style: `background:${TYPE_COLOR[t]}` }), n)),
      h("div", { class: "row" }, h("span", { class: "sw", style: "border:1.6px solid #8a5a12;background:none" }), "has generator"),
      h("div", { class: "row" }, "– – transformer"),
      h("div", { class: "row" }, h("span", { style: `color:${DC_COLOR}` }, "–·– DC"), "DC line"));
  }
}

// ---------------------------------------------------------------- geodata layout and map
//
// Bus positions come from the case geodata projected to Web Mercator (EPSG:3857)
// metres with y pointing down, so S.zoom.k is screen pixels per metre. Map layers
// are drawn under the network: USGS imagery tiles, a TIGERweb roads image exported
// for the current view, and TIGERweb state/county polygons queried for the view
// (drawn in world units under one transform) with names at their interior points.

const EARTH_R = 6378137, WORLD = 2 * Math.PI * EARTH_R;
const IMAGERY = { tile: (z, y, x) => `https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/${z}/${y}/${x}`, maxZoom: 16 };
const ROADS = { url: "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Transportation/MapServer/export", layers: "show:0,1,2,3,4,5,6,7,8" };
const TIGER_BOUNDS = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer";
const BOUNDS = {
  // maxRes, nameRes, abbrRes: coarsest metres per pixel at which polygons, names, abbreviated names show
  states: { layer: 0, fields: "NAME,STUSAB,INTPTLAT,INTPTLON", maxRes: 20000, nameRes: 3000, abbrRes: 20000, cls: "state" },
  counties: { layer: 1, fields: "NAME,INTPTLAT,INTPTLON", maxRes: 1500, nameRes: 800, abbrRes: 0, cls: "county" },
};
const CREDITS = { imagery: "Imagery: USGS The National Map", roads: "Roads", bounds: "Boundaries" };

function mercator(lat, lon) {
  const phi = Math.max(-85.0511, Math.min(85.0511, lat)) * Math.PI / 180;
  return { x: EARTH_R * lon * Math.PI / 180, y: -EARTH_R * Math.log(Math.tan(Math.PI / 4 + phi / 2)) };
}
function hasGeo() { return !!(S.state && S.state.case.geodata && S.state.case.geodata.length); }
function useGeo() { return S.layout === "geodata" && S.style !== "oneline" && hasGeo(); }
function positions() { return useGeo() ? geoLayout().pos : S.pos; }

let geoCache = null;
function geoLayout() {
  // located buses from geodata (first row per bus); buses without geodata are
  // placed near their located neighbors (marked estimated) or left out
  const c = S.state.case;
  const key = JSON.stringify([c.geodata.map(r => r.slice(0, 3)), c.tables.bus.map(b => b[0]), [...c.tables.branch, ...c.tables.dcline].map(b => [b[0], b[1]])]);
  if (geoCache && geoCache.key === key) return geoCache;
  const where = new Map();
  for (const r of c.geodata) if (!where.has(r[0])) where.set(r[0], r);
  const ids = c.tables.bus.map(b => b[0]);
  const pos = {};
  for (const b of ids) if (where.has(b)) pos[b] = mercator(where.get(b)[1], where.get(b)[2]);
  const pts = Object.values(pos);
  const span = pts.length > 1 ? Math.max(Math.max(...pts.map(p => p.x)) - Math.min(...pts.map(p => p.x)),
    Math.max(...pts.map(p => p.y)) - Math.min(...pts.map(p => p.y))) : 0;
  const offset = Math.max(200, span * 0.02);
  const adj = new Map(ids.map(b => [b, []]));
  [...c.tables.branch, ...c.tables.dcline].forEach(b => {
    if (adj.has(b[0]) && adj.has(b[1]) && b[0] !== b[1]) { adj.get(b[0]).push(b[1]); adj.get(b[1]).push(b[0]); }
  });
  const estimated = new Set();
  for (let changed = true; changed;) {
    changed = false;
    ids.forEach((b, i) => {
      if (pos[b]) return;
      const nb = adj.get(b).map(n => pos[n]).filter(Boolean);
      if (!nb.length) return;
      const a = i * 2.39996; // golden angle keeps siblings apart
      pos[b] = { x: nb.reduce((s, p) => s + p.x, 0) / nb.length + offset * Math.cos(a), y: nb.reduce((s, p) => s + p.y, 0) / nb.length + offset * Math.sin(a) };
      estimated.add(b);
      changed = true;
    });
  }
  geoCache = { key, pos, where, estimated, missing: ids.filter(b => !pos[b]).length };
  return geoCache;
}

function setLayout(value) {
  const geo = useGeo();
  S.layout = value;
  try { localStorage.setItem("ppedit.layout", value); } catch (_) { /* storage unavailable */ }
  if (useGeo() !== geo) needFit = true;
  renderNetwork();
}

let space = null; // coordinate space of the last drawing: force, geo or oneline
function updateNetTools() {
  const has = hasGeo(), oneline = S.style === "oneline";
  const now = oneline ? "oneline" : useGeo() ? "geo" : "force";
  if (now !== space) { needFit = true; space = now; }
  const sel = $("#net-layout");
  sel.querySelector('[value="geodata"]').disabled = !has;
  sel.value = useGeo() ? "geodata" : "force";
  sel.disabled = oneline;
  sel.title = oneline ? "The one-line style computes its own layout"
    : has ? "Bus positions" : "Geodata layout: load a CSV of bus locations (Map ▾) or open a case with geodata";
  $("[data-action=relayout]").disabled = useGeo();
  const na = areaList().length, ab = $("#net-areas");
  ab.checked = S.areas;
  ab.disabled = na < 2;
  ab.closest("label").title = na < 2 ? "Color buses by area: the case has only one area" : `Color buses by area (${na} areas)`;
  $("#geodata-export").disabled = !has;
  $("#geodata-clear").disabled = !has;
  for (const cb of $$("#map-menu [data-layer]")) {
    cb.checked = !!S.map[cb.dataset.layer];
    cb.closest("label").classList.toggle("disabled", !useGeo());
  }
  const credit = $("#map-credit");
  const geo = useGeo(), m = S.map;
  const parts = [];
  if (geo && m.imagery) parts.push(CREDITS.imagery);
  const census = [geo && m.roads && CREDITS.roads, geo && (m.states || m.stateNames || m.counties || m.countyNames) && CREDITS.bounds].filter(Boolean);
  if (census.length) parts.push(`${census.join(", ")}: U.S. Census Bureau TIGERweb`);
  credit.textContent = parts.join(" · ");
  credit.hidden = !parts.length;
}

function viewBox(W, H) {
  const { k, x, y } = S.zoom;
  return [-x / k, -y / k, (W - x) / k, (H - y) / k]; // world metres: x0, y0, x1, y1
}

const tileCache = new Map();
function tileLayer(W, H) {
  const g = sv("g");
  const { k, x, y } = S.zoom;
  const z = Math.max(0, Math.min(IMAGERY.maxZoom, Math.round(Math.log2(k * WORLD / 256))));
  const n = 2 ** z, size = WORLD / n;
  const [X0, Y0, X1, Y1] = viewBox(W, H);
  const tx0 = Math.max(0, Math.floor((X0 + WORLD / 2) / size)), tx1 = Math.min(n - 1, Math.floor((X1 + WORLD / 2) / size));
  const ty0 = Math.max(0, Math.floor((Y0 + WORLD / 2) / size)), ty1 = Math.min(n - 1, Math.floor((Y1 + WORLD / 2) / size));
  if ((tx1 - tx0 + 1) * (ty1 - ty0 + 1) > 400) return g;
  const used = new Set();
  const place = (tz, tx, ty) => {
    const url = IMAGERY.tile(tz, ty, tx), tsize = WORLD / 2 ** tz;
    if (used.has(url)) return;
    let img = tileCache.get(url);
    if (!img) { img = sv("image", { href: url, class: "map-tile", preserveAspectRatio: "none" }); tileCache.set(url, img); }
    const px = (tx * tsize - WORLD / 2) * k + x, py = (ty * tsize - WORLD / 2) * k + y;
    img.setAttribute("x", px.toFixed(1)); img.setAttribute("y", py.toFixed(1));
    img.setAttribute("width", (tsize * k + 0.5).toFixed(1)); img.setAttribute("height", (tsize * k + 0.5).toFixed(1));
    g.append(img);
    used.add(url);
  };
  // coarser tiles underneath fill in while finer ones load or where they are missing
  const up = Math.min(3, z), f = 2 ** up;
  if (up) for (let ty = Math.floor(ty0 / f); ty <= Math.floor(ty1 / f); ty++) for (let tx = Math.floor(tx0 / f); tx <= Math.floor(tx1 / f); tx++) place(z - up, tx, ty);
  for (let ty = ty0; ty <= ty1; ty++) for (let tx = tx0; tx <= tx1; tx++) place(z, tx, ty);
  for (const url of tileCache.keys()) { // keep the cache bounded
    if (tileCache.size <= 600) break;
    if (!used.has(url)) tileCache.delete(url);
  }
  return g;
}

const exportCache = {};
function exportLayer(name, spec, W, H) {
  // one transparent image of the whole view from an ArcGIS MapServer export,
  // fetched shortly after the view settles; the last image stays in place meanwhile
  const o = exportCache[name] || (exportCache[name] = { img: sv("image", { class: "map-overlay", preserveAspectRatio: "none" }), box: null, want: "" });
  const box = viewBox(W, H);
  const want = box.map(v => Math.round(v)).join(",") + `|${W}x${H}`;
  if (o.want !== want && W > 0 && H > 0) {
    o.want = want;
    clearTimeout(o.timer);
    o.timer = setTimeout(() => {
      const w = Math.min(4096, Math.round(W)), hgt = Math.min(4096, Math.round(H));
      const url = `${spec.url}?bbox=${box[0].toFixed(0)},${(-box[3]).toFixed(0)},${box[2].toFixed(0)},${(-box[1]).toFixed(0)}`
        + `&bboxSR=3857&imageSR=3857&size=${w},${hgt}&dpi=96&format=png32&transparent=true&layers=${encodeURIComponent(spec.layers)}&f=image`;
      const pre = new Image();
      pre.onload = () => { if (o.want !== want) return; o.img.setAttribute("href", url); o.box = box; scheduleRender(); };
      pre.src = url;
    }, 350);
  }
  if (!o.box) return null;
  const { k, x, y } = S.zoom;
  o.img.setAttribute("x", (o.box[0] * k + x).toFixed(1)); o.img.setAttribute("y", (o.box[1] * k + y).toFixed(1));
  o.img.setAttribute("width", ((o.box[2] - o.box[0]) * k).toFixed(1)); o.img.setAttribute("height", ((o.box[3] - o.box[1]) * k).toFixed(1));
  return o.img;
}

const boundCache = {};
function boundaryLayer(name, W, H) {
  // state or county polygons for (a margin around) the view, generalized to the
  // current resolution; refetched when the view leaves the box or zooms in 2x
  const spec = BOUNDS[name];
  const b = boundCache[name] || (boundCache[name] = { feats: [], box: null, res: 0, origin: [0, 0], g: sv("g"), want: "" });
  const res = 1 / S.zoom.k;
  if (res > spec.maxRes) return b;
  const view = viewBox(W, H);
  const covered = b.box && view[0] >= b.box[0] && view[1] >= b.box[1] && view[2] <= b.box[2] && view[3] <= b.box[3] && res >= b.res / 2;
  if (!covered && W > 0) {
    const mx = (view[2] - view[0]) / 2, my = (view[3] - view[1]) / 2;
    const box = [view[0] - mx, view[1] - my, view[2] + mx, view[3] + my];
    const want = box.map(v => Math.round(v)).join(",");
    if (b.want !== want) {
      b.want = want;
      clearTimeout(b.timer);
      b.timer = setTimeout(() => fetchBounds(name, box, res, want), 400);
    }
  }
  return b;
}

async function fetchBounds(name, box, res, want) {
  const spec = BOUNDS[name], b = boundCache[name];
  if (b.ctl) b.ctl.abort();
  b.ctl = new AbortController();
  const q = new URLSearchParams({
    where: "1=1", geometry: `${box[0].toFixed(0)},${(-box[3]).toFixed(0)},${box[2].toFixed(0)},${(-box[1]).toFixed(0)}`,
    geometryType: "esriGeometryEnvelope", inSR: "3857", spatialRel: "esriSpatialRelIntersects",
    outFields: spec.fields, returnGeometry: "true", outSR: "3857",
    maxAllowableOffset: String(Math.max(1, Math.round(res))), geometryPrecision: "0", f: "json",
  });
  try {
    const data = await (await fetch(`${TIGER_BOUNDS}/${spec.layer}/query?${q}`, { signal: b.ctl.signal })).json();
    if (data.error) throw new Error(data.error.message || "query failed");
    if (b.want !== want) return;
    const ox = box[0], oy = box[1]; // coordinates relative to the box keep SVG numbers small
    const g = sv("g");
    b.feats = (data.features || []).map(f => {
      const d = (f.geometry && f.geometry.rings || []).map(ring =>
        "M" + ring.map(([px, py]) => `${Math.round(px - ox)},${Math.round(-py - oy)}`).join("L") + "Z").join("");
      if (d) g.append(sv("path", { d, class: `map-bound ${spec.cls}` }));
      const a = f.attributes || {};
      return { name: a.NAME, abbr: a.STUSAB, label: a.INTPTLAT ? mercator(+a.INTPTLAT, +a.INTPTLON) : null };
    });
    Object.assign(b, { g, box, res, origin: [ox, oy] });
    scheduleRender();
  } catch (err) {
    if (err.name !== "AbortError") console.warn(`ppedit: ${name} boundaries unavailable: ${err.message}`);
  }
}

function renderMap(W, H) {
  const m = S.map, root = sv("g", { class: "map" });
  const { k, x, y } = S.zoom;
  if (m.imagery) root.append(tileLayer(W, H));
  if (m.roads) { const img = exportLayer("roads", ROADS, W, H); if (img) root.append(img); }
  const labels = sv("g");
  for (const [name, show, names] of [["counties", m.counties, m.countyNames], ["states", m.states, m.stateNames]]) {
    if (!show && !names) continue;
    const spec = BOUNDS[name], b = boundaryLayer(name, W, H);
    if (1 / k > spec.maxRes || !b.box) continue;
    if (show) {
      b.g.setAttribute("transform", `matrix(${k},0,0,${k},${x + k * b.origin[0]},${y + k * b.origin[1]})`);
      root.append(b.g);
    }
    const full = 1 / k <= spec.nameRes;
    if (names && (full || 1 / k <= spec.abbrRes)) {
      for (const f of b.feats) {
        const text = full ? f.name : f.abbr;
        if (!f.label || !text) continue;
        const px = f.label.x * k + x, py = f.label.y * k + y;
        if (px < -50 || py < -20 || px > W + 50 || py > H + 20) continue;
        const t = sv("text", { x: px.toFixed(1), y: py.toFixed(1), class: `map-label ${spec.cls}` });
        t.textContent = text;
        labels.append(t);
      }
    }
  }
  root.append(labels);
  return root;
}

function setMapLayer(layer, on) {
  S.map[layer] = on;
  try { localStorage.setItem("ppedit.map", JSON.stringify(S.map)); } catch (_) { /* storage unavailable */ }
  renderNetwork();
}

async function loadGeodata(file) {
  const text = await file.text();
  await act("/api/geodata", { filename: file.name, text });
  const n = S.state.case.geodata.length, tags = S.state.case.geotags;
  if (S.style === "oneline") S.style = "simple";
  setLayout("geodata");
  needFit = true;
  show("network");
  toast(`Loaded ${n} bus location(s)${tags.length ? ` with ${tags.join(", ")}` : ""} from ${file.name}`);
}

function setStyle(style) {
  const geo = useGeo();
  if ((style === "oneline") !== (S.style === "oneline")) needFit = true; // different coordinate spaces
  S.style = style;
  if (useGeo() !== geo) needFit = true;
  try { localStorage.setItem("ppedit.diagram", style); } catch (_) { /* storage unavailable */ }
  renderNetwork();
}

function pick(kind, index) {
  S.pick = kind ? { kind, index } : null;
  renderNetwork();
}

function renderInspector() {
  const box = $("#inspector");
  if (!S.pick) { box.hidden = true; return; }
  box.hidden = false;
  const c = S.state.case, { kind, index } = S.pick;
  const row = c.tables[kind][index];
  const cols = S.meta.columns[kind];
  const res = S.state.result && S.state.result.success ? S.state.result : null;
  const field = (k) => {
    const spec = cols[k];
    const ed = spec.kind === "enum"
      ? h("select", {}, Object.entries(spec.options).map(([a, b]) => h("option", { value: a, selected: String(row[k]) === a }, `${a} ${b}`)))
      : h("input", { value: row[k], spellcheck: "false" });
    ed.addEventListener("change", async () => {
      try { await act("/api/cell", { table: kind, row: index, column: spec.name, value: ed.value }); }
      catch (_) { ed.value = row[k]; }
    });
    if (ed.tagName === "INPUT") ed.addEventListener("keydown", e => { if (e.key === "Enter") ed.blur(); });
    return [h("dt", { title: spec.desc }, spec.name + (spec.unit ? ` [${spec.unit}]` : "")), h("dd", {}, ed)];
  };
  const kids = [h("button", { class: "close", title: "Close", onclick: () => pick(null) }, "×")];
  if (kind === "bus") {
    const num = row[0];
    kids.push(h("h3", {}, `Bus ${num}`), h("div", { class: "sub" }, `row ${index + 1} · ${cols[1].options[row[1]]}`));
    if (res) {
      const rb = res.bus.find(b => b.bus_i === num);
      if (rb) kids.push(h("div", { class: "sub" }, `Solution: Vm ${fmt(rb.Vm, 4)} pu, Va ${fmt(rb.Va, 2)}°` + (rb.lam_P !== null ? `, λP ${fmt(rb.lam_P, 2)} $/MWh` : "")));
    }
    kids.push(h("dl", {}, [1, 2, 3, 4, 5, 9, 7, 8, 11, 12, 6, 10].flatMap(field)));
    const ai = c.tables.areas.findIndex(a => a[0] === row[6]);
    const refOf = c.tables.areas.map((a, i) => [a, i]).filter(([a], i) => a[1] === num && i !== ai);
    kids.push(h("div", { class: "sub" }, ai < 0 ? `Area ${row[6]} has no row in the areas table` :
      h("span", {}, h("a", { onclick: () => goto("areas", ai) }, `Area ${row[6]}`),
        c.tables.areas[ai][1] === num ? " · this bus is its price reference bus" : ` · price reference bus ${c.tables.areas[ai][1]}`),
      ...refOf.map(([a, i]) => h("span", {}, " · ", h("a", { onclick: () => goto("areas", i) }, `price reference bus of area ${a[0]}`)))));
    if (hasGeo()) {
      const geo = c.geodata.find(r => r[0] === num);
      kids.push(h("h4", {}, "Location"));
      if (geo) {
        kids.push(h("dl", {},
          h("dt", {}, "LAT [deg]"), h("dd", {}, fmt(geo[1], 6)),
          h("dt", {}, "LON [deg]"), h("dd", {}, fmt(geo[2], 6)),
          ...c.geotags.flatMap((t, j) => [h("dt", {}, t), h("dd", {}, String(geo[3 + j] ?? ""))])));
      } else {
        kids.push(h("div", { class: "sub" }, "No geodata for this bus" + (useGeo() && geoLayout().estimated.has(num) ? " (drawn near its neighbors)" : "")));
      }
    }
    const gens = c.tables.gen.map((g, i) => [g, i]).filter(([g]) => g[0] === num);
    kids.push(h("h4", {}, `Generators (${gens.length})`), h("ul", {}, gens.map(([g, i]) =>
      h("li", {}, h("a", { onclick: () => goto("gen", i) }, `gen ${i + 1}`), `${fmt(g[1], 1)} MW · ${S.meta.columns.gen[7].options[g[7]]}`))));
    const brs = c.tables.branch.map((b, i) => [b, i]).filter(([b]) => b[0] === num || b[1] === num);
    kids.push(h("h4", {}, `Branches (${brs.length})`), h("ul", {}, brs.map(([b, i]) =>
      h("li", {}, h("a", { onclick: () => pick("branch", i) }, `${b[0]} → ${b[1]}`), `x ${b[3]}${b[10] ? "" : " · OUT"}`))));
    const dcs = c.tables.dcline.map((d, i) => [d, i]).filter(([d]) => d[0] === num || d[1] === num);
    if (dcs.length) kids.push(h("h4", {}, `DC lines (${dcs.length})`), h("ul", {}, dcs.map(([d, i]) =>
      h("li", {}, h("a", { onclick: () => pick("dcline", i) }, `${d[0]} → ${d[1]}`), `Pf ${fmt(d[3], 1)} MW${d[2] ? "" : " · OUT"}`))));
    kids.push(h("div", { class: "btns" },
      h("button", { onclick: () => addConnected("gen", num) }, "+ Generator"),
      h("button", { onclick: () => addConnected("branch", num) }, "+ Branch from here"),
      h("button", { onclick: () => addConnected("dcline", num) }, "+ DC line from here"),
      h("button", { onclick: () => addConnected("bus", num) }, "+ New bus linked here"),
      h("button", { onclick: () => goto("bus", index) }, "Show in table")));
  } else if (kind === "dcline") {
    kids.push(h("h3", {}, `DC line ${row[0]} → ${row[1]}`), h("div", { class: "sub" }, `row ${index + 1}` + (row[2] ? "" : " · out of service")));
    const rd = res && res.dcline && res.dcline[index];
    if (rd && rd.solved) {
      kids.push(h("div", { class: "sub" }, `Solution: Pf ${fmt(rd.Pf, 2)} MW, Pt ${fmt(rd.Pt, 2)} MW, loss ${fmt(rd.loss_MW, 2)} MW; ` +
        `Qf ${fmt(rd.Qf, 2)}, Qt ${fmt(rd.Qt, 2)} MVAr` + (rd.mu_Pmax ? `, μPmax ${fmt(rd.mu_Pmax, 2)} $/MWh` : "") + (rd.mu_Pmin ? `, μPmin ${fmt(rd.mu_Pmin, 2)} $/MWh` : "")));
    } else if (rd) kids.push(h("div", { class: "sub" }, "Not solved (out of service or isolated terminal)"));
    kids.push(h("dl", {}, cols.map((_, k) => k).flatMap(field)));
    const cost = c.tables.dclinecost[index];
    kids.push(h("div", { class: "btns" }, h("button", { onclick: () => goto("dcline", index) }, "Show in table"),
      cost ? h("button", { onclick: () => goto("dclinecost", index) }, "Show cost") : null));
  } else {
    kids.push(h("h3", {}, `Branch ${row[0]} → ${row[1]}`), h("div", { class: "sub" }, `row ${index + 1}` + (row[8] ? " · transformer" : "")));
    if (res && res.branch[index]) {
      const rb = res.branch[index];
      kids.push(h("div", { class: "sub" }, `Solution: ${fmt(rb.PF, 1)} + j${fmt(rb.QF, 1)} MVA from, loading ${fmt(rb.loading_pct, 0)}%`));
    }
    kids.push(h("dl", {}, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].flatMap(field)));
    kids.push(h("div", { class: "btns" }, h("button", { onclick: () => goto("branch", index) }, "Show in table")));
  }
  box.replaceChildren(...kids);
}

async function addConnected(what, bus) {
  const c = S.state.case;
  if (what === "gen") {
    await act("/api/rows", { table: "gen", values: { bus } });
    toast(`Generator added at bus ${bus}`);
  } else if (what === "branch") {
    const other = c.tables.bus.find(b => b[0] !== bus);
    if (!other) return toast("Add another bus first", true);
    const st = await act("/api/rows", { table: "branch", values: { fbus: bus, tbus: other[0] } });
    pick("branch", st.row);
    toast("Branch added — set its to bus in the panel");
  } else if (what === "dcline") {
    const other = c.tables.bus.find(b => b[0] !== bus);
    if (!other) return toast("Add another bus first", true);
    const st = await act("/api/rows", { table: "dcline", values: { fbus: bus, tbus: other[0] } });
    pick("dcline", st.row);
    toast("DC line added — set its to bus and limits in the panel");
  } else {
    const st = await act("/api/rows", { table: "bus" });
    const nb = st.case.tables.bus[st.row][0];
    const p = S.pos[bus];
    if (p) S.pos[nb] = { x: p.x + 60, y: p.y + 30 };
    await act("/api/rows", { table: "branch", values: { fbus: bus, tbus: nb } });
    pick("bus", st.row);
  }
}

function goto(table, row, column) {
  S.filter = "";
  $("#filter").value = "";
  show(table);
  requestAnimationFrame(() => {
    const tr = $(`#grid tr[data-index="${row}"]`);
    if (!tr) return;
    tr.scrollIntoView({ block: "center" });
    const el = column ? $(`[data-col="${column}"]`, tr) : $("input:not([type=checkbox]), select", tr);
    if (el) { el.focus(); el.parentElement.classList.add("flash"); }
  });
}

// pan, zoom, drag
let renderPending = false;
function scheduleRender() {
  if (renderPending) return;
  renderPending = true;
  requestAnimationFrame(() => { renderPending = false; renderNetwork(); });
}
let drag = null;
function startDrag(e, busId, index) {
  e.stopPropagation();
  const p = positions()[busId];
  drag = { busId, index, sx: e.clientX, sy: e.clientY, px: p.x, py: p.y, moved: false, fixed: useGeo() };
  $("#net").setPointerCapture(e.pointerId);
}
function setupNetworkEvents() {
  const svg = $("#net");
  let pan = null;
  svg.addEventListener("pointerdown", e => {
    if (drag) return;
    pan = { sx: e.clientX, sy: e.clientY, x: S.zoom.x, y: S.zoom.y, moved: false };
    svg.classList.add("panning");
    svg.setPointerCapture(e.pointerId);
  });
  svg.addEventListener("pointermove", e => {
    if (drag) {
      const dx = (e.clientX - drag.sx) / S.zoom.k, dy = (e.clientY - drag.sy) / S.zoom.k;
      if (Math.abs(dx * S.zoom.k) + Math.abs(dy * S.zoom.k) > 2) drag.moved = true;
      if (drag.fixed) return; // geodata positions are data, not dragged
      S.pos[drag.busId] = { x: drag.px + dx, y: drag.py + dy };
      scheduleRender();
    } else if (pan) {
      const dx = e.clientX - pan.sx, dy = e.clientY - pan.sy;
      if (Math.abs(dx) + Math.abs(dy) > 2) pan.moved = true;
      S.zoom.x = pan.x + dx; S.zoom.y = pan.y + dy;
      scheduleRender();
    }
  });
  svg.addEventListener("pointerup", () => {
    if (drag) { if (!drag.moved) pick("bus", drag.index); drag = null; }
    else if (pan) { if (!pan.moved && S.pick) pick(null); pan = null; }
    svg.classList.remove("panning");
  });
  svg.addEventListener("wheel", e => {
    e.preventDefault();
    const r = svg.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    const f = Math.exp(-e.deltaY * 0.0015);
    const k = useGeo() ? Math.max(1e-5, Math.min(20, S.zoom.k * f)) : Math.max(0.05, Math.min(20, S.zoom.k * f));
    S.zoom.x = mx - (mx - S.zoom.x) * k / S.zoom.k;
    S.zoom.y = my - (my - S.zoom.y) * k / S.zoom.k;
    S.zoom.k = k;
    scheduleRender();
  }, { passive: false });
}

// ---------------------------------------------------------------- results

function renderResults() {
  const box = $("#view-results");
  const r = S.state.result;
  if (!r) {
    box.replaceChildren(h("div", { class: "empty" }, h("div", { class: "big" }, "No solution yet"),
      "Run a power flow (Run PF) or optimal power flow (Run OPF). Any edit clears the solution."));
    return;
  }
  const c = S.state.case;
  const kind = r.kind === "pf" ? "Power flow" : "Optimal power flow";
  const totalPg = r.gen.reduce((s, g) => s + g.Pg, 0);
  const totalPd = c.tables.bus.reduce((s, b) => s + b[2], 0);
  const cards = h("div", { class: "cards" },
    card(kind, r.success ? "Converged" : "Failed", r.success ? "ok" : "fail"),
    r.success ? card("Generation", `${fmt(totalPg, 1)} MW`) : null,
    r.success ? card("Load", `${fmt(totalPd, 1)} MW`) : null,
    r.success ? card("Losses", `${fmt(r.losses_MW, 2)} MW` + (r.dcline && r.dcline.length ? ` (DC ${fmt(r.dcline_losses_MW, 2)})` : "")) : null,
    r.objective !== null && r.success ? card("Cost", `${fmt(r.objective, 2)} $/h`) : null,
    r.success ? card("Vm range", `${fmt(Math.min(...r.bus.map(b => b.Vm)), 3)} – ${fmt(Math.max(...r.bus.map(b => b.Vm)), 3)}`) : null);
  const kids = [cards];
  if (r.success) {
    kids.push(h("div", { style: "margin-bottom:12px" },
      h("button", { onclick: () => act("/api/apply").then(() => toast("Solution copied into bus Vm/Va, generator Pg/Qg and DC line flows")) },
        "Apply solution to case"),
      h("span", { class: "hint", style: "margin-left:10px" }, "copies Vm, Va, Pg, Qg and DC line Pf, Pt, Qf, Qt into the case (undoable)")));
    const busRows = r.bus.map(b => [b.bus_i, fmt(b.Vm, 4), fmt(b.Va, 3)].concat(r.kind === "opf" ? [fmt(b.lam_P, 3)] : []));
    const busCls = r.bus.map((b, i) => {
      const spec = c.tables.bus[i];
      return spec && (b.Vm > spec[11] + 1e-6 || b.Vm < spec[12] - 1e-6) ? [null, "hi"] : [];
    });
    kids.push(h("div", { class: "res-grid" },
      block("Bus voltages", ["bus", "Vm pu", "Va deg"].concat(r.kind === "opf" ? ["λP $/MWh"] : []), busRows, busCls),
      block("Generator dispatch", ["gen", "bus", "Pg MW", "Qg MVAr"], r.gen.map((g, i) => [i + 1, g.bus, fmt(g.Pg, 2), fmt(g.Qg, 2)])),
      block("Branch flows", ["#", "from", "to", "P from MW", "Q from MVAr", "loading"],
        r.branch.map((b, i) => [i + 1, b.fbus, b.tbus, fmt(b.PF, 2), fmt(b.QF, 2), loadCell(b.loading_pct)]),
        r.branch.map(b => [null, null, null, null, null, b.loading_pct > 100 ? "hi" : b.loading_pct > 80 ? "mid" : null])),
      r.dcline && r.dcline.length ? block("DC line flows", ["#", "from", "to", "Pf MW", "Pt MW", "loss MW", "Qf MVAr", "Qt MVAr"]
        .concat(r.kind === "opf" ? ["μPmax $/MWh"] : []),
        r.dcline.map((d, i) => [i + 1, d.fbus, d.tbus].concat(d.solved
          ? [fmt(d.Pf, 2), fmt(d.Pt, 2), fmt(d.loss_MW, 2), fmt(d.Qf, 2), fmt(d.Qt, 2)].concat(r.kind === "opf" ? [fmt(d.mu_Pmax, 3)] : [])
          : ["–", "–", "–", "–", "–"].concat(r.kind === "opf" ? ["–"] : [])))) : null,
      r.areas && (r.areas.length > 1 || c.tables.areas.length) ? block("Areas", ["area", "buses", "Pg MW", "Pd MW", "shunt MW", "loss MW", "export MW", "ref bus"]
        .concat(r.kind === "opf" ? ["price $/MWh"] : []),
        r.areas.map(a => [a.area, a.buses, fmt(a.Pg, 2), fmt(a.Pd, 2), fmt(a.shunt_MW, 2), fmt(a.losses_MW, 2), fmt(a.export_MW, 2), a.price_ref_bus ?? "–"]
          .concat(r.kind === "opf" ? [fmt(a.lam_P, 3)] : [])), [], "wide") : null));
  }
  kids.push(h("details", { open: !r.success }, h("summary", {}, "Solver report"), h("pre", { class: "log" }, r.log || "(no output)")));
  box.replaceChildren(...kids);
}
function card(k, v, cls) { return h("div", { class: "card" }, h("div", { class: "k" }, k), h("div", { class: "v " + (cls || "") }, v)); }
function loadCell(L) {
  if (L === null) return "unrated";
  const span = h("span", {}, h("span", { class: "loadbar", style: `width:${Math.min(L, 120) * 0.5}px;background:${L > 100 ? "#b42318" : L > 80 ? "#c27c0e" : "#5f8f80"}` }), `${fmt(L, 0)}%`);
  return span;
}
function block(title, head, rows, cls = [], extra = "") {
  return h("div", { class: "res-block" + (extra ? ` ${extra}` : "") }, h("h3", {}, title), h("div", { class: "scroll" },
    h("table", {}, h("thead", {}, h("tr", {}, head.map(x => h("th", {}, x)))),
      h("tbody", {}, rows.map((r, i) => h("tr", {}, r.map((v, j) => h("td", { class: (cls[i] || [])[j] || null }, v))))))));
}

// ---------------------------------------------------------------- issues

function renderIssues() {
  const box = $("#view-issues");
  const st = S.state;
  const kids = [];
  if (st.case.notes && st.case.notes.length) kids.push(h("div", { class: "notes" }, st.case.notes.map(n => h("div", {}, "Note: " + n))));
  if (!st.issues.length) kids.push(h("div", { class: "empty" }, h("div", { class: "big" }, "No issues found"), "The case passes all checks."));
  const sorted = [...st.issues].sort((a, b) => (a.level === b.level ? 0 : a.level === "error" ? -1 : 1));
  for (const is of sorted) {
    const where = is.table + (is.row !== null ? ` ${rowLabel(is.table, is.row)}` : "") + (is.column ? ` · ${is.column}` : "");
    kids.push(h("div", { class: "issue", onclick: () => is.row !== null ? goto(is.table, is.row, is.column) : show(is.table) },
      h("span", { class: `lvl ${is.level}` }, is.level), h("span", { class: "where" }, where), h("span", {}, is.message)));
  }
  box.replaceChildren(...kids);
}

// ---------------------------------------------------------------- actions

async function openFile(file) {
  const text = await file.text();
  await act("/api/open", { filename: file.name, text });
  afterOpen();
  if (S.state.case.notes.length) toast(S.state.case.notes[0]);
  else toast(`Opened ${file.name} — use Save as to keep changes`);
}

function afterOpen() {
  S.pos = {}; S.pick = null; needFit = true;
  for (const t of S.meta.tables) S.selected[t].clear();
  renderView();
}

function confirmDiscard() {
  return !S.state.dirty || confirm("Discard unsaved changes?");
}

async function onAction(btn) {
  const a = btn.dataset.action;
  closeMenus();
  try {
    if (a === "new-empty") { if (confirmDiscard()) { await act("/api/new", {}); afterOpen(); show("bus"); } }
    else if (a === "builtin") { if (confirmDiscard()) { await act("/api/new", { builtin: btn.dataset.name }); afterOpen(); } }
    else if (a === "open") { if (confirmDiscard()) $("#file-input").click(); }
    else if (a === "save") await saveTo(null);
    else if (a === "save-as") {
      const def = S.state.path || `${S.state.case.name}.glm`;
      const path = prompt("Save to this path on the server machine (.py, .json or .glm):", def);
      if (path) await saveTo(path);
    }
    else if (a === "download") { window.location = `/api/export?format=${btn.dataset.format}`; }
    else if (a === "undo") await act("/api/undo");
    else if (a === "redo") await act("/api/redo");
    else if (a === "solve") {
      btn.disabled = true;
      try {
        await act("/api/solve", { kind: btn.dataset.kind, algorithm: $("#algorithm").value });
        const r = S.state.result;
        toast(r.success ? `${r.kind.toUpperCase()} converged` : `${r.kind.toUpperCase()} did not converge — see Results`, !r.success);
        if (S.view !== "network") show("results"); else renderNetwork();
      } finally { btn.disabled = false; }
    }
    else if (a === "add-row") await addRow();
    else if (a === "dup-row") await duplicateRows();
    else if (a === "del-rows") await deleteRows();
    else if (a === "relayout") { if (S.style === "oneline") olCache = null; else layout(true); fitView(); renderNetwork(); }
    else if (a === "fit") { fitView(); renderNetwork(); }
    else if (a === "geodata-load") $("#geodata-input").click();
    else if (a === "geodata-export") { window.location = "/api/export?format=csv"; }
    else if (a === "geodata-clear") {
      if (confirm("Remove the geodata (bus locations) from this case? Undo restores it.")) { await act("/api/geodata", { clear: true }); needFit = true; renderNetwork(); }
    }
    else if (a === "exit") await exitServer();
  } catch (_) { /* toast already shown */ }
}

async function saveTo(path) {
  const body = path ? { path } : {};
  try {
    await api("/api/save", body).then(setState);
  } catch (err) {
    if (!err.confirm) return toast(err.message, true);
    if (!confirm(`${err.message}.\n\nOverwrite the file anyway? Use Save as to keep the original.`)) return;
    await act("/api/save", { ...body, force: true });
  }
  toast(`Saved ${S.state.path}`);
}

async function exitServer() {
  const msg = S.state.dirty
    ? "The case has unsaved changes that will be lost.\n\nStop the ppedit server anyway?"
    : "Stop the ppedit server? The editor will stop working until you run ppedit serve again.";
  if (!confirm(msg)) return;
  try { await api("/api/exit", {}); }
  catch (err) { return toast(err.message, true); }
  document.body.replaceChildren(h("div", { class: "stopped" },
    h("h1", {}, "ppedit server stopped"),
    h("p", {}, "You can close this tab. Run ppedit serve to start the editor again.")));
  document.title = "ppedit (stopped)";
}

function closeMenus() { $$(".dropdown.open").forEach(d => d.classList.remove("open")); }

async function init() {
  S.meta = await api("/api/meta");
  $("#builtin-list").replaceChildren(...S.meta.builtin.map(n => h("button", { "data-action": "builtin", "data-name": n }, n)));
  document.addEventListener("click", e => {
    const m = e.target.closest("[data-menu]");
    if (m) { const d = $("#" + m.dataset.menu); const open = d.classList.contains("open"); closeMenus(); if (!open) d.classList.add("open"); return; }
    const b = e.target.closest("[data-action]");
    if (b) return onAction(b);
    const tab = e.target.closest("[data-view]");
    if (tab) return show(tab.dataset.view);
    if (!e.target.closest(".dropdown")) closeMenus();
  });
  $("#net-style").addEventListener("change", e => setStyle(e.target.value));
  $("#net-layout").addEventListener("change", e => setLayout(e.target.value));
  $("#net-areas").addEventListener("change", e => setAreas(e.target.checked));
  $("#map-menu").addEventListener("change", e => { if (e.target.dataset.layer) setMapLayer(e.target.dataset.layer, e.target.checked); });
  $("#geodata-input").addEventListener("change", e => {
    const f = e.target.files[0]; e.target.value = "";
    if (f) loadGeodata(f).catch(() => { /* toast already shown */ });
  });
  $("#file-input").addEventListener("change", e => { const f = e.target.files[0]; e.target.value = ""; if (f) openFile(f); });
  $("#grid").addEventListener("change", e => { if (e.target.dataset.col) commitCell(e.target); });
  $("#grid").addEventListener("keydown", e => {
    const el = e.target;
    if (!el.dataset.col) return;
    if (e.key === "Enter") {
      e.preventDefault();
      const next = $(`[data-row="${+el.dataset.row + (e.shiftKey ? -1 : 1)}"][data-col="${el.dataset.col}"]`);
      if (next) next.focus(); else el.blur();
    } else if (e.key === "Escape") {
      const k = S.meta.columns[S.view].findIndex(c => c.name === el.dataset.col);
      el.value = S.state.case.tables[S.view][+el.dataset.row][k];
      el.blur();
    }
  });
  $("#filter").addEventListener("input", e => { S.filter = e.target.value; renderTable(S.view); });
  const globalField = (id, key) => {
    const el = $(id);
    el.addEventListener("change", () => act("/api/global", { key, value: el.value }).catch(() => renderChrome()));
    el.addEventListener("keydown", e => { if (e.key === "Enter") el.blur(); });
  };
  globalField("#baseMVA", "baseMVA"); globalField("#version", "version"); globalField("#case-name", "name");
  document.addEventListener("keydown", e => {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === "s") { e.preventDefault(); onAction(S.state.path ? $("[data-action=save]") : $("[data-action=save-as]")); }
    const inField = ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName);
    if (mod && e.key.toLowerCase() === "z" && !inField) { e.preventDefault(); act(e.shiftKey ? "/api/redo" : "/api/undo").catch(() => {}); }
  });
  window.addEventListener("beforeunload", e => { if (S.state && S.state.dirty) { e.preventDefault(); e.returnValue = ""; } });
  window.addEventListener("resize", () => { if (S.view === "network") renderNetwork(); });
  setupNetworkEvents();
  setState(await api("/api/state"));
  if (!S.state.case.tables.bus.length) show("network");
}

init().catch(err => toast(err.message, true));
