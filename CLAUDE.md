# CLAUDE.md

Guidance for Claude when working in the `dchassin/ppedit` repository.
General Eudoxys package conventions are in the `eudoxys-package` skill
(https://github.com/eudoxys/claude); this file records only what is
specific to this package.

## What this is

A local web application for editing PYPOWER cases and Arras Energy
GridLAB-D `module pypower` models: a Flask server (`ppedit/server.py`) holds
one `Case` (`ppedit/case.py`) in memory and serves a plain HTML/CSS/JS editor
(`ppedit/static/`, no build step, no JS dependencies). Python 3.10+, alpha.

- Docs site: https://www.chassin.org/ppedit (pdoc from docstrings)
- Maintainer: David P. Chassin
- CLI entry point: `ppedit` -> `ppedit.cli:main`

## Where things are

- `MANIFEST.md`: map of every file, a task index, and what not to read.
  Read it first instead of listing folders.
- No `DESIGN.md`: the design fits in the `ppedit` package docstring
  (specification and "What is not promised").

Upstream references (read-only; use sparse checkout of
`arras-energy/gridlabd`): `module/pypower/{bus,gen,branch,gencost,dcline}.cpp` for
GLM property names, units and keywords; `converters/py2glm.py` for the
`gridlabd convert -t pypower` output this package should stay compatible with.

## Core conventions

- Tables are lists of rows ordered as `COLUMNS[table]`; `gencost` and
  `dclinecost` rows are `[model, startup, shutdown, ncost, costs]` with
  `costs` a comma-separated string (as in the GLM `costs` property).
  `COST_TABLES` maps each cost table to the table it follows row by row.
  `areas` rows are `[area, price_ref_bus]`.
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
  numpy; the editor reports the solver error. `case9target` PF does not
  converge in PYPOWER either.
- DC lines are solved by `ppedit/_dcline.py`, not `pypower.toggle_dcline`
  (which fails with current numpy and mishandles `dclinecost`,
  out-of-service lines and Q cost rows); results are checked against the
  expected values of `pypower/t/t_dcline.py`.
- GLM `dcline`: `Sfrom` = `Pf + j Pt`, `Sto` = `Qf + j Qt` (GridLAB-D's
  positional mapping in `pypower.cpp` and `py2glm.py`, not the meaning in
  the `dcline.cpp` property descriptions). `dclinecost` has no GLM object
  and is dropped on GLM output (with a comment); so is `areas`, which
  module pypower also lacks (bus `area` is kept). `areas` is not passed to
  the solver; per-area results are computed in `Case._area_summary`.
- GLM import keeps only `bus`, `gen`, `branch`, `gencost`, `dcline`; everything else
  is listed in `Case.notes`, and saving over the source file needs `force`.
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
  is not edited in the diagram). GLM output keeps lat/lon but not geotags.
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
- Areas check box (`S.areas`, `showAreas()` in `app.js`): draws each area
  as the union of per-site discs clipped to the half-planes nearer than any
  other area's site (`areaRegions`; sites are buses plus points along
  internal lines), so regions never overlap; bus colors don't change.
  Turning it on re-runs the force layout with area cohesion and gives each
  area its own column strip (box) in the one-line layout (part of the
  `olCache` signature), where ties always keep a column in each box and
  a jog (never merged). Geodata positions are fixed, so an area may
  show as several patches there.
- A case without an `areas` table gets default rows from the bus areas on
  load (`Case._default_areas`, `_price_ref`); GLM output writes the "not
  written" comment only when the rows differ from those defaults.

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
