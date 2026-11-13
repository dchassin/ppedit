// ppedit browser editor
"use strict";

const S = {
  meta: null,          // columns, tables, builtin cases
  state: null,         // editor state from the server
  view: "network",
  selected: { bus: new Set(), gen: new Set(), branch: new Set(), gencost: new Set() },
  filter: "",
  pos: {},             // bus_i -> {x, y}
  zoom: { k: 1, x: 0, y: 0 },
  pick: null,          // {kind: "bus"|"branch", index}
  style: (() => { try { return localStorage.getItem("ppedit.style") || "classic"; } catch (_) { return "classic"; } })(),
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
const TABLE_TITLES = { bus: "bus", gen: "generator", branch: "branch", gencost: "generator cost" };
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
      const msg = table === "bus" && column === "bus_i" ? "Bus renumbered; generators and branches updated" :
        table === "gencost" ? "ncost updated to match the cost data" : "Related values updated";
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
    if (table === "bus") values[0] = Math.max(0, ...S.state.case.tables.bus.map(r => r[0])) + 1;
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
    if (ng || nb) {
      if (!confirm(`Deleting ${rows.length} bus(es) also deletes ${ng} generator(s) and ${nb} branch(es) connected to them. Continue?`)) return;
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
  const edges = c.tables.branch.filter(b => ids.has(b[0]) && ids.has(b[1]) && b[0] !== b[1]).map(b => [b[0], b[1]]);
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
  const pts = S.state.case.tables.bus.map(b => S.pos[b[0]]).filter(Boolean);
  if (!pts.length) { S.zoom = { k: 1, x: svg.clientWidth / 2, y: svg.clientHeight / 2 }; return; }
  const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const W = svg.clientWidth - (S.pick ? 330 : 0), H = svg.clientHeight;
  const k = Math.min(4, Math.min((W - 80) / Math.max(1, x1 - x0), (H - 80) / Math.max(1, y1 - y0)));
  S.zoom = { k, x: W / 2 - k * (x0 + x1) / 2, y: H / 2 - k * (y0 + y1) / 2 };
}

let needFit = true;
// one-line style: blue = low, red = high (voltage and loading)
function vColorOneLine(vm) { return vColor(2 - vm); }
function loadColor(L) { return vColor(1 - (Math.min(L, 150) - 50) / 50 * 0.06); }
const NEUTRAL_LINE = "#8d9792";

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
  const svg = $("#net");
  const c = S.state.case, res = S.state.result && S.state.result.success ? S.state.result : null;
  const oneline = S.style === "oneline";
  layout();
  if (needFit && svg.clientWidth) { fitView(); needFit = false; }
  $$("[data-style]").forEach(b => b.classList.toggle("on", b.dataset.style === S.style));
  svg.classList.toggle("oneline", oneline);
  // draw in screen coordinates so symbols and text keep their size when zooming
  const { k, x, y } = S.zoom;
  const W = id => { const p = S.pos[id]; return p ? { x: p.x * k + x, y: p.y * k + y } : null; };
  const g = sv("g");
  const ids = new Set(c.tables.bus.map(b => b[0]));
  const sum = busSummary();
  const errBus = new Set(S.state.issues.filter(i => i.table === "bus" && i.level === "error" && i.row !== null).map(i => i.row));
  const labels = sv("g"); // line labels drawn above lines, below nodes
  // edges
  c.tables.branch.forEach((b, i) => {
    const p = W(b[0]), q = W(b[1]);
    if (!ids.has(b[0]) || !ids.has(b[1]) || !p || !q) return;
    const xfmr = (b[8] && b[8] !== 0) || b[9];
    const cls = ["edge"];
    if (xfmr && !oneline) cls.push("xfmr");
    if (b[10] === 0) cls.push("out");
    if (S.pick && S.pick.kind === "branch" && S.pick.index === i) cls.push("sel");
    const line = sv("line", { x1: p.x, y1: p.y, x2: q.x, y2: q.y, class: cls.join(" ") });
    const rb = res && res.branch[i] && b[10] === 1 ? res.branch[i] : null;
    if (rb && rb.loading_pct !== null && !cls.includes("sel")) {
      const L = rb.loading_pct;
      if (oneline) {
        line.style.stroke = loadColor(L);
        line.style.strokeWidth = L > 100 ? 3.5 : 2.4;
      } else {
        line.style.stroke = L > 100 ? "#b42318" : L > 80 ? "#c27c0e" : "#5f8f80";
        line.style.strokeWidth = 1.2 + Math.min(L, 120) / 30;
      }
    } else if (oneline && b[10] === 1 && !cls.includes("sel")) {
      line.style.strokeWidth = 2;
    }
    const hit = sv("line", { x1: p.x, y1: p.y, x2: q.x, y2: q.y, class: "edge-hit" });
    const title = sv("title");
    title.textContent = `branch ${i + 1}: ${b[0]} → ${b[1]}` + (rb ? `\nPF ${fmt(rb.PF, 1)} MW, QF ${fmt(rb.QF, 1)} MVAr, loading ${rb.loading_pct === null ? "unrated" : fmt(rb.loading_pct, 0) + "%"}` : "");
    hit.append(title);
    hit.addEventListener("click", e => { e.stopPropagation(); pick("branch", i); });
    g.append(line, hit);
    if (oneline) {
      const dx = q.x - p.x, dy = q.y - p.y, len = Math.hypot(dx, dy) || 1;
      const ux = dx / len, uy = dy / len;
      if (xfmr) { // two interlocking circles a quarter of the way from the from bus
        const mx = p.x + dx * 0.25, my = p.y + dy * 0.25;
        for (const s of [-3, 3]) g.append(sv("circle", { cx: mx + ux * s, cy: my + uy * s, r: 4.5, class: "xfmr-sym" }));
      }
      if (rb && len > 30) {
        // flow arrow and MW label at mid-line, arrow pointing in the direction of real power flow
        const fwd = rb.PF >= 0;
        const t = 0.5;
        const ax = p.x + dx * t, ay = p.y + dy * t;
        const sx = fwd ? ux : -ux, sy = fwd ? uy : -uy;
        const a = 4;
        labels.append(sv("path", {
          d: `M${ax + sx * a},${ay + sy * a} L${ax - sx * a - sy * a * 0.8},${ay - sy * a + sx * a * 0.8} L${ax - sx * a + sy * a * 0.8},${ay - sy * a - sx * a * 0.8} Z`,
          class: "flow-arrow", fill: line.style.stroke || NEUTRAL_LINE,
        }));
        let ang = Math.atan2(dy, dx) * 180 / Math.PI;
        if (ang > 90) ang -= 180; else if (ang < -90) ang += 180;
        const nx = -uy, ny = ux, off = 7;
        const lx = ax + nx * off * (ny < 0 ? -1 : 1), ly = ay + ny * off * (ny < 0 ? -1 : 1);
        const txt = sv("text", { x: lx, y: ly, class: "flow-label", "text-anchor": "middle", "dominant-baseline": "middle", transform: `rotate(${ang} ${lx} ${ly})` });
        txt.textContent = `${Math.abs(rb.PF).toFixed(1)} MW`;
        labels.append(txt);
      }
    }
  });
  g.append(labels);
  // nodes
  c.tables.bus.forEach((b, i) => {
    const p = W(b[0]);
    if (!p) return;
    const cls = ["node"];
    if (S.pick && S.pick.kind === "bus" && S.pick.index === i) cls.push("sel");
    if (errBus.has(i)) cls.push("err");
    const node = sv("g", { class: cls.join(" "), transform: `translate(${p.x},${p.y})`, "data-bus": b[0] });
    const v = sum.volt.get(b[0]);
    const hasGen = sum.ngen.get(b[0]) > 0, hasLoad = b[2] !== 0 || b[3] !== 0;
    let r;
    if (oneline) {
      const isolated = b[1] === 4 || (c.tables.bus.length > 1 && !sum.conn.has(b[0]));
      if (isolated) cls.push("isolated");
      const fill = isolated ? "none" : vColorOneLine(v.Vm);
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
    title.textContent = `bus ${b[0]} (${S.meta.columns.bus[1].options[b[1]]})\nPd ${b[2]} MW, Qd ${b[3]} MVAr\nVm ${fmt(v.Vm, 4)} pu, Va ${fmt(v.Va, 2)}°${sum.res ? " (solution)" : " (case values)"}`;
    node.append(title);
    node.addEventListener("pointerdown", e => startDrag(e, b[0], i));
    g.append(node);
  });
  svg.replaceChildren(g);
  renderLegend(!!res, oneline);
  renderInspector();
  if (!c.tables.bus.length) {
    const t = sv("text", { x: svg.clientWidth / 2, y: svg.clientHeight / 2, "text-anchor": "middle", fill: "#69726e" });
    t.textContent = "Empty case — add buses in the Buses tab, or open a built-in case from New ▾";
    svg.append(t);
  }
}

function renderLegend(solved, oneline) {
  const L = $("#legend");
  if (oneline) {
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
      h("div", { class: "row" }, "0.94", h("span", { class: "bar", style: `background:linear-gradient(90deg,${vColorOneLine(.94)},${vColorOneLine(1)},${vColorOneLine(1.06)})` }), "1.06"),
      h("div", {}, solved ? "Line loading (% of rateA), MW flow labels" : "Line loading and MW flows shown after a solve"),
      h("div", { class: "row" }, "0%", h("span", { class: "bar", style: `background:linear-gradient(90deg,${loadColor(0)},${loadColor(50)},${loadColor(100)})` }), "100%+"),
      h("div", {}, "Labels: bus, angle, G = generation, L = load"));
  } else if (solved) {
    L.replaceChildren(
      h("div", {}, "Voltage magnitude (pu)"),
      h("div", { class: "row" }, "0.94", h("span", { class: "bar", style: `background:linear-gradient(90deg,${vColor(.94)},${vColor(1)},${vColor(1.06)})` }), "1.06"),
      h("div", { class: "row" }, "Line loading: ", h("span", { style: "color:#5f8f80" }, "■ <80%"), h("span", { style: "color:#c27c0e" }, "■ 80–100%"), h("span", { style: "color:#b42318" }, "■ >100%")));
  } else {
    L.replaceChildren(
      ...[[3, "REF"], [2, "PV"], [1, "PQ"], [4, "isolated"]].map(([t, n]) =>
        h("div", { class: "row" }, h("span", { class: "sw", style: `background:${TYPE_COLOR[t]}` }), n)),
      h("div", { class: "row" }, h("span", { class: "sw", style: "border:1.6px solid #8a5a12;background:none" }), "has generator"),
      h("div", { class: "row" }, "– – transformer"));
  }
}

function setStyle(style) {
  S.style = style;
  try { localStorage.setItem("ppedit.style", style); } catch (_) { /* storage unavailable */ }
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
    const gens = c.tables.gen.map((g, i) => [g, i]).filter(([g]) => g[0] === num);
    kids.push(h("h4", {}, `Generators (${gens.length})`), h("ul", {}, gens.map(([g, i]) =>
      h("li", {}, h("a", { onclick: () => goto("gen", i) }, `gen ${i + 1}`), `${fmt(g[1], 1)} MW · ${S.meta.columns.gen[7].options[g[7]]}`))));
    const brs = c.tables.branch.map((b, i) => [b, i]).filter(([b]) => b[0] === num || b[1] === num);
    kids.push(h("h4", {}, `Branches (${brs.length})`), h("ul", {}, brs.map(([b, i]) =>
      h("li", {}, h("a", { onclick: () => pick("branch", i) }, `${b[0]} → ${b[1]}`), `x ${b[3]}${b[10] ? "" : " · OUT"}`))));
    kids.push(h("div", { class: "btns" },
      h("button", { onclick: () => addConnected("gen", num) }, "+ Generator"),
      h("button", { onclick: () => addConnected("branch", num) }, "+ Branch from here"),
      h("button", { onclick: () => addConnected("bus", num) }, "+ New bus linked here"),
      h("button", { onclick: () => goto("bus", index) }, "Show in table")));
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
  const p = S.pos[busId];
  drag = { busId, index, sx: e.clientX, sy: e.clientY, px: p.x, py: p.y, moved: false };
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
      if (Math.abs(dx) + Math.abs(dy) > 2) drag.moved = true;
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
    const k = Math.max(0.05, Math.min(20, S.zoom.k * f));
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
    r.success ? card("Losses", `${fmt(r.losses_MW, 2)} MW`) : null,
    r.objective !== null && r.success ? card("Cost", `${fmt(r.objective, 2)} $/h`) : null,
    r.success ? card("Vm range", `${fmt(Math.min(...r.bus.map(b => b.Vm)), 3)} – ${fmt(Math.max(...r.bus.map(b => b.Vm)), 3)}`) : null);
  const kids = [cards];
  if (r.success) {
    kids.push(h("div", { style: "margin-bottom:12px" },
      h("button", { onclick: () => act("/api/apply").then(() => toast("Solution copied into bus Vm/Va and generator Pg/Qg")) },
        "Apply solution to case"),
      h("span", { class: "hint", style: "margin-left:10px" }, "copies Vm, Va and Pg, Qg into the case (undoable)")));
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
        r.branch.map(b => [null, null, null, null, null, b.loading_pct > 100 ? "hi" : b.loading_pct > 80 ? "mid" : null]))));
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
function block(title, head, rows, cls = []) {
  return h("div", { class: "res-block" }, h("h3", {}, title), h("div", { class: "scroll" },
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
    else if (a === "relayout") { layout(true); fitView(); renderNetwork(); }
    else if (a === "fit") { fitView(); renderNetwork(); }
    else if (a === "style") setStyle(btn.dataset.style);
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
