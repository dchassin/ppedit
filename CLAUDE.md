# CLAUDE.md

Guidance for Claude when working in the `eudoxys/ppedit` repository.
General Eudoxys package conventions are in the `eudoxys-package` skill
(https://github.com/eudoxys/claude); this file records only what is
specific to this package.

## What this is

A local web application for editing PYPOWER cases and Arras Energy
GridLAB-D `module pypower` models: a Flask server (`ppedit/server.py`) holds
one `Case` (`ppedit/case.py`) in memory and serves a plain HTML/CSS/JS editor
(`ppedit/static/`, no build step, no JS dependencies). Python 3.12, alpha.

- Docs site: https://www.eudoxys.com/ppedit (pdoc from docstrings)
- Maintainer: David P. Chassin, Eudoxys Sciences LLC
- CLI entry point: `ppedit` -> `ppedit.cli:main`

## Where things are

- `MANIFEST.md`: map of every file, a task index, and what not to read.
  Read it first instead of listing folders.
- `DESIGN.md`: numbered design decisions, contracts, and open questions
  (cite decisions as "decision #N"). Read it before changing behavior it
  covers, follow its decisions, and ask before departing from one; update
  it in the same PR when a decision changes. The package docstring stays
  authoritative for users (specification and "What is not promised").

Upstream references (read-only; use sparse checkout of
`arras-energy/gridlabd`): `module/pypower/{bus,gen,branch,gencost,dcline}.cpp` for
GLM property names, units and keywords; `converters/py2glm.py` for the
`gridlabd convert -t pypower` output this package should stay compatible with.

## Core conventions

- Tables are lists of rows ordered as `COLUMNS[table]`; `gencost` and
  `dclinecost` rows are `[model, startup, shutdown, ncost, costs]` with
  `costs` a comma-separated string (as in the GLM `costs` property).
  `COST_TABLES` maps each cost table to the table it follows row by row.
  `areas` rows are `[area, price_ref_bus, name]`; `name` is free text
  kept in `ppc["areanames"]` outside PYPOWER's two-column `areas` array
  (decision #15).
- Every edit goes through `Case.set_value`, `add_row`, `delete_rows`,
  `set_global`, or `apply_result`, which keep references valid (bus
  renumber cascades, bus delete needs `cascade`, gencost follows gen,
  dclinecost follows dcline, area renumber moves its buses). The
  server wraps each in `Editor.edit()` for undo and rollback on error.
- `.py` cases are parsed with `ast` and never executed (`_parse_py`,
  `_eval`). Do not replace this with `import`/`exec`.
- Infinities travel as the strings `"Inf"`/`"-Inf"` in JSON.
- All mutating API calls return the full editor state; the browser
  re-renders from it (no client-side model of its own).

## Style

House style: no spaces after commas in calls and signatures, 4-space indent.
Frontend is vanilla JS in one file (`app.js`) with a tiny `h()` DOM helper.

## Build, test, docs

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install --upgrade pip && pip install -e . pytest
pytest            # ~2 s, includes ppedit/case.py docstring examples (test_doctest.py)
make docs
ppedit serve ppedit/_test/data/case3.glm   # manual UI check
```

Nothing needs network or credentials (the Claude tab needs an Anthropic
API key to start sessions; tests use a fake client).

## Known rough edges (don't "fix" silently; mention or ask)

- PYPOWER's OPF fails (inside pypower) on piecewise-linear costs and on
  reactive-power cost rows (`case30pwl`, `case9Q`, `case30Q`) with current
  numpy; the editor reports the solver error (DC OPF solves them).
  `case9target` PF does not converge in PYPOWER either. DC OPF (PIPS) on
  `pypower/t/t_case9_dcline` fails numerically inside pypower, with or
  without its DC lines.
- OPF solvers (`OPF_SOLVERS` in `case.py`, Run OPF ▾): only those whose
  module is installed are offered; IPOPT, CPLEX, MOSEK and Gurobi paths
  in pypower are untested here (none installed in CI). The `CVXOPF-*`
  solvers run cvxopf (`ppedit/_cvxopf.py`, optional, Python 3.11+ and the
  IPOPT library; CI installs it), not pypower: results are converted to
  the `runopf` layout so `Case.solve` and `printpf` read them unchanged
  (the report is printed without "f", as a PF, since cvxopf has no
  multipliers; `lam_P`/`mu_*` are `None`). DC lines are built as
  `HVDCLink` with `p_in = -Pf` because `cvxopf.hvdc_from_dcline` runs
  PYPOWER DC lines backwards (as of cvxopf b6edcc7, 2026-11-12); switch
  to it once that is fixed upstream. cvxopf solve options (decision #48)
  come from the Settings tab (`ppedit.cvxopf` in `localStorage`,
  `cvxopfOptions()` in `app.js`) as `options` of `/api/solve`;
  `_cvxopf.RECOMMENDED` is the per-model preparation profile from the
  cvxopf authors (issue #88) and may need revising as cvxopf changes.
  `verbose` captures file descriptors 1 and 2 while solving, so other
  threads' output in that window lands in the log too.
- Solver messages (decision #49): `Case.solve` returns `messages`; the
  Issues tab lists them under *Last solve* and its dot counts them, but
  `state.issues` stays `Case.validate()` only. Python warnings raised
  while solving are recorded (no longer silenced): numpy warnings from a
  failing PYPOWER solve now show up as solver warnings.
- Solver warning details (decision #53): `_cvxopf.EXPLAIN` and
  `ISOLATED_EXPLAIN`/`DCLINE_CUT_EXPLAIN` in `case.py` hold the physical
  explanations; `_cvxopf._cvxopf_warnings` recognizes cvxopf warnings by
  their text (regexes on cvxopf b6edcc7 wording), maps cvxopf's zero-based
  internal bus/branch/gen indexes to case rows (`_Elements`) and groups
  per-element warnings. A changed cvxopf message falls back to the plain
  `cvxopf warning: ...` with no detail. SOCP details come from
  `recover_socp_voltage`/`audit_socp_relaxation`. Issues tab:
  `issueDetail` in `app.js`, open message kept in `S.issueOpen`.
- DC lines are solved by `ppedit/_dcline.py`, not `pypower.toggle_dcline`
  (which fails with current numpy and mishandles `dclinecost`,
  out-of-service lines and Q cost rows); results are checked against the
  expected values of `pypower/t/t_dcline.py`.
- GLM `dcline`: `Sfrom` = `Pf + j Pt`, `Sto` = `Qf + j Qt` (GridLAB-D's
  positional mapping in `pypower.cpp` and `py2glm.py`, not the meaning in
  the `dcline.cpp` property descriptions). `dclinecost` has no module
  pypower class: GLM output writes it as `dclinecost` runtime-class
  objects (the `pypower.gencost` properties, parent the DC line, named
  `pp_dclinecost_<n>`; `_dump_glm_dclinecost`/`_glm_costs` in `case.py`),
  since `pypower.gencost` must have a gen parent; named for the
  `pypower.dclinecost` class gridlabd is expected to add (input reads
  that too). Drop the `class dclinecost` declaration once it exists. `areas`, which module
  pypower also lacks, is dropped on GLM output (bus `area` is kept). `areas` is not passed to
  the solver; per-area results are computed in `Case._area_summary`.
- GLM import keeps only `bus`, `gen`, `branch`, `gencost`, `dcline`; everything else
  is listed in `Case.notes`, and saving over the source file needs `force`.
  Preprocessor directives are not expanded: a GLM with no bus objects that
  uses `#include`/`#input`/`#begin` raises `CaseError` (it used to load as an
  empty case, e.g. the gridlabd `module/pypower/autotest/test_case*.glm`).
  `ppedit serve` prints `Case.notes`; the page lists them (from
  `Case.source`, the load report sent as state `source`) on the Results
  tab and switches to it when a file with notes is opened.
- GLM bus names are `pp_bus_<bus_i>`, unlike `py2glm.py` (`pp_bus_<row>`).
- Force-layout positions are not persisted; only geodata is. The diagram is drawn in screen
  coordinates (symbols and text keep their size when zooming), so pan/zoom
  re-renders through `scheduleRender()`; fine to ~300 buses.
- Classic style: a bus with generation and load is drawn as a circle.
- Geodata layout (`geoLayout`/`renderMap` in `app.js`): positions are Web
  Mercator metres (y down), so `S.zoom.k` is pixels per metre there. Map
  layers come straight from the browser (no server proxy, no cache):
  USGS `USGSImageryOnly` tiles (cached to level 16, scaled beyond),
  TIGERweb `Transportation` export images, and TIGERweb `State_County`
  layer 0/1 queries (JSON, CORS) drawn as paths, names at their interior
  points (states abbreviated when zoomed out). US coverage only; the USGS
  imagery has dark gaps over some water; offline the layers simply don't
  draw. Buses are not draggable in this layout (geodata
  is not edited in the diagram). A click on buses drawn within
  `OVERLAP_PX` screen pixels of each other opens `#bus-menu` at the
  pointer to choose one (`busesAt`/`openBusMenu`), instead of picking
  the topmost. GLM output keeps lat/lon on the bus and writes
  auxiliary values as `pp_geodata` runtime-class objects (parent the bus,
  named `pp_geodata_<bus_i>`, plus `_<row>` when the bus has several rows;
  `_dump_glm_geodata`/`_glm_geodata` in `case.py`); tags that are not GLM
  names are renamed (`_glm_tag_names`) and read back renamed.
- One-line style (`onelineLayout`/`renderOneline` in `app.js`): its own
  layered layout cached by topology signature (`olCache`), drawn in layout
  units under one transform (so it scales with zoom, unlike the other
  styles); buses are not draggable. Each branch and DC line gets a column
  in each of its two bars (on the side facing the other bus) joined by one
  horizontal jog (`routes` in `onelineLayout`, `route` in `renderOneline`);
  the two merge into one straight column when they are neighbors. Jogs lie
  in the free band of the first gap below the upper bar, one track each
  where they overlap (longer jogs higher), and that gap grows to fit its
  tracks. Branches skipping rows cross bus bars below their jog (drawn
  with a white halo gap); wide and dense on case118/case300.
- View menu (`#view-menu` in `index.html`): Re-layout, Fit, style and
  layout radio items, and the Areas check box; Labels, Map and search stay
  separate. Style and layout are set per opened case by `defaultView()`
  (decision #28: geodata layout when the case has geodata, else one-line
  under `ONELINE_MAX` = 20 buses, else classic) and are not persisted.
- One-line switching devices (decision #51): breakers and generator
  switches toggle `status` through `POST /api/cell`; load switches use
  `POST /api/load` (`Editor.toggle_load`), whose switched-off values live
  in `Editor.loads_off` (server memory, in undo snapshots, state
  `loads_off`), not in the case file. A generator symbol picks `"gen"`.
- Areas check box (`S.areas`, `showAreas()` in `app.js`, in View ▾): draws each area
  as the union of per-site discs clipped to the half-planes nearer than any
  other area's site (`areaRegions`; sites are buses plus points along
  internal lines), so regions never overlap; bus colors don't change.
  Only the outer hull is drawn (issue #106): the discs are rasterized on a
  grid limited to the view (about 250k points), enclosed gaps are filled
  from the nearest area, and the outline is traced (`traceOutline`,
  marching squares) and smoothed (`smoothLoop`); an area nested inside
  another still shows as a hole in it.
  In the geodata layout (`convex` argument) each area also grows, breadth
  first, into the empty grid points of its convex hull (`convexHull`),
  so shapes are as convex as their neighbors allow.
  Area names are clickable (decision #57, issue #112, `areaNameClick`,
  `pickArea`, `areaTotals`): they open the area's `areas` row in the
  inspector (`S.pick.kind === "areas"`) with its totals; one-line text
  has `pointer-events: none` except `text.area-name`.
  Turning it on re-runs the force layout with area cohesion and gives each
  area its own column strip (box) in the one-line layout (part of the
  `olCache` signature), where ties always keep a column in each box and
  a jog (never merged). Geodata positions are fixed, so an area may
  show as several patches there.
- Labels menu (`S.labels`, `LABEL_ITEMS`, `LABEL_DEFAULTS`, `dataLabels`
  in `app.js`): per style, per table a set of keys, built-in items
  prefixed `:` (`:voltage`, `:gen`, `:load`, `:flow`, `:loading`) and
  column names (`bus_i` is the bus number), saved in `localStorage` as
  `ppedit.labels`. Every item draws in every style; per-style defaults
  reproduce the drawing from before the menu existed. `:flow` and
  `:loading` need a solution (greyed out until one exists). Data labels
  are not placed to avoid collisions; DC lines get none.
- Search box (`#net-search`, `searchItems`/`runSearch`/`centerOn` in
  `app.js`): runs on Enter; client-side only, over buses, branches and DC
  lines (`A-B` and `branch N` include DC lines; `A-B` with no line between
  them lists the lines at A and B, with `found.note` shown and no
  auto-pick even for one; free text matches geodata
  aux columns only, since buses have no names). One match is picked and
  centered at the current zoom; several open `#search-menu` (at most 200
  listed).
- Page URL parameters (`?solve=pf|opf`, `&algorithm=`, `?view=`; end of
  `init()` in `app.js`, decision #44) are used by `eudoxys/pptools`
  (`pprun` Network tab: `?solve=pf&view=network`); keep them working when
  changing `init()`, tabs, or `/api/solve`.
- A case without an `areas` table gets default rows from the bus areas on
  load (`Case._default_areas`, `_price_ref`); GLM output writes the "not
  written" comment only when the rows differ from those defaults.

- Stopped server (decision #58, issue #51, `watchServer`/`serverDown`/
  `serverBack` in `app.js`): `GET /api/ping` is polled every 5 s outside
  `api()` and must stay free of `editor.lock`; a different `instance`
  (from `/api/meta`) means the server restarted and the page reloads.
  While down, the dialog polls every 2 s and closes by itself when the
  server answers (no wait button). "Restart server" can only show the
  `ppedit serve ... --no-browser` command: a page cannot start a process. `window.close()` is refused for tabs with back history
  (e.g. headless test browsers), which then show the stopped page.

- Work-in-progress indicator (decision #54, `#busy`, `busyStart`/`busyEnd` in
  `app.js`): every `api()` call counts unless it passes `{ background: true }`;
  pass it for any new polling so the indicator does not stay up. Rendering
  that is slow without a request (force layout of a large case) does not
  show it.

- Table import/export (decision #55, issue #73): pandas is used only in
  `ppedit/_tableio.py`; `Case.import_rows` takes plain lists. Replacing
  rows with changed bus/area numbers renumbers references (two passes via
  temporary numbers so swaps work), like cell edits do; `ncost` is never
  taken from the file. Parquet/Feather/Excel depend on optional engines
  (CI installs `pyarrow` and `openpyxl`).

- Extended result columns (decision #56, issue #108): `RESULT_COLUMNS` in
  `case.py` (PYPOWER array indexes), `extended` of `Case.solve`
  (`_extended`), `extendedColumns` in `app.js`. Shown only while a
  successful solution exists; not saved or applied. Export appends them
  (`Editor.extended` in `server.py`, `extended` of `dump_rows`);
  `Case.import_rows` drops columns with their names (`ignored`).
  `S.tableSort` column indexes past `COLUMNS[table]` refer to them.

- Claude tab (`ppedit/claude.py`, `renderClaude` in `app.js`): sessions
  call the Messages API with `urllib` (no SDK dependency) in background
  threads; each tool call that changes the case is one `Editor.edit()`
  under `editor.lock` (an `RLock`). The page polls `GET /api/claude` each
  second while the tab is open or a session works, and reloads the state
  when `revision` changes or a session ran `solve`. Web search uses the
  server-side `web_search_20250305` tool (must be enabled for the API
  organization); `fetch_url` and local file reads run in the ppedit
  server, local reads confined to the session's `root`.

## Working rules

- Don't commit generated output; `ppedit/_test/data/` is curated (see its
  README).
- When a public signature, column, or API route changes, update the module
  docstring, the package docstring, `README.md`, and the server API list.
- Update `MANIFEST.md` when adding, moving, or deleting files; run
  `python .github/check_manifest.py`.
- Conventional small test cases: `case9` (built-in) and
  `ppedit/_test/data/case3.glm`.
