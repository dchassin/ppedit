# eudoxys/ppedit — package design (draft 1)

Status: design recorded as built, 2026-11-15 (version 0.2.0, after PR #50; issue #52); decision #36f added 2026-11-17 (issue #69); inherited conventions revised 2026-11-17; decision #36g added 2026-11-17 (page URL parameters); decision #36h added 2026-11-18 (open from URL, issue #83); decisions #36k (cvxopf solve options, issue #88) and #36l (solver messages on the Issues tab, issue #89) added 2026-11-18; decision #14b (area names, issue #93) added 2026-11-18; decision #36m (solver warning details, follow-up to PR #92) added 2026-11-18; decision #26 revised 2026-11-20 (area outer hulls, issue #106).
Draft 1 documents the current state; it was written after the code, so
every decision below is already implemented unless it is listed under
`# planned` or in Open questions.

Docstrings are authoritative for users (the `ppedit` package docstring
holds the specification and "What is not promised"; `ppedit.case`,
`ppedit.server` and `ppedit.claude` hold the formats, API and session
details). This file keeps the decisions behind them, the structure that
ties them together, and what is still planned or undecided.

## Purpose

`ppedit` is a local web application for building and editing transmission
and sub-transmission models for the PYPOWER solver, stored either as
PYPOWER case files (`.py`, `.json`) or as Arras Energy GridLAB-D models
that use `module pypower` (`.glm`). It edits the case tables, checks them,
draws the network, and runs PYPOWER power flow (PF) and optimal power flow
(OPF), so a model can be prepared and checked before it is run in
GridLAB-D.

In scope:

- the PYPOWER case tables `bus`, `gen`, `branch`, `gencost`, `dcline`,
  `dclinecost` and `areas`, plus `baseMVA`, `version` and the case name;
- bus locations (geodata) with auxiliary columns, for a map layout;
- PF/OPF through the `pypower` package, with results shown on the diagram
  and optionally copied back into the case;
- sessions with the Anthropic Messages API that read and edit the case
  through the same checked, undoable edits as the browser.

Excluded (and where it lives instead):

- GLM objects other than the solver layer of `module pypower` (`load`,
  `powerplant`, `powerline`, `shunt`, ..., other modules): reported on
  load and dropped on save; edit them in the GLM source or in GridLAB-D
  (planned: issue #33 and TODO "Preserve non-solver GLM objects").
- Running GridLAB-D itself, time series, and distribution models.
- Multi-user or networked use: one case, one user, the local machine.

Coverage: any PYPOWER case size the browser can draw (the diagram is
practical to about 300 buses); map layers cover the United States only.

## Decisions settled

Numbers are permanent: add `Nb` rows for related later decisions; edit a
reversed decision in place and note the date.

| # | Decision |
|---|---|
| 1 | One Flask server process holds one `Case` in memory (in an `Editor`) for one local user; the browser is a view of it. There is no database and no session state outside the server process. |
| 2 | The frontend is plain HTML/CSS/JS in `ppedit/static/` (one `app.js`, a tiny `h()` DOM helper), with no build step and no JS dependencies. The server sends the page and these files with `Cache-Control: no-cache`, so browsers revalidate them on every load and pick up an upgraded package without a hard reload. |
| 3 | The browser keeps no model of its own: every mutating API call returns the full editor state and the page re-renders from it. |
| 4 | Tables are plain Python lists of rows in `COLUMNS[table]` order (no numpy, no pandas in the model), so they edit row by row and serialize directly to JSON. Column metadata (unit, kind, default, options, GLM name) lives in `COLUMNS`. |
| 5 | Every edit goes through `Case.set_value`, `add_row`, `delete_rows`, `set_global`, `set_geodata`/`load_geodata` or `apply_result`, which keep references valid. The server wraps each call in `Editor.edit()`, which snapshots the case, rolls back on any exception, and records one undo step only if the case changed. |
| 6 | Undo/redo stores whole-case snapshots (deep copies of tables, globals and geodata), at most `HISTORY` = 200 steps. Simple and always correct; acceptable for cases of a few hundred buses. |
| 7 | `.py` case files are parsed with `ast` and evaluated by a restricted evaluator (`_parse_py`, `_eval`); they are never imported or executed. Both `ppc = {...}` assignments and `return {...}` literals are accepted (issue #5). |
| 8 | Round trip: `Case.load(path).dumps(fmt)` re-read gives the same tables for `py`, `json` and `glm`, except what a format cannot hold (see #15, #17). This is the main property the tests protect. |
| 9 | Referential integrity: renumbering a bus renumbers its generators, branches, DC lines, area price reference and geodata; deleting a bus with connections (or that is a price reference bus) is refused unless `cascade=True`. |
| 10 | Cost tables follow their parent row by row: `COST_TABLES = {"gencost":"gen","dclinecost":"dcline"}`; adding or deleting a generator (DC line) adds or deletes its cost row(s). Cost rows are `[model,startup,shutdown,ncost,costs]` with `costs` a comma-separated string, as in the GLM `costs` property. |
| 11 | GLM DC lines use the positional mapping `Sfrom = Pf + j Pt`, `Sto = Qf + j Qt`, matching how GridLAB-D `pypower.cpp` passes them to PYPOWER and what `gridlabd convert -t pypower` writes, not the per-end meaning in the `dcline.cpp` property descriptions. Still open for review: issue #11. |
| 12 | DC lines are solved by `ppedit/_dcline.py`, a reimplementation of the `pypower.toggle_dcline` model as `userfcn` callbacks, because `toggle_dcline` fails with current numpy and mishandles `dclinecost`, out-of-service lines and Q cost rows. Results are checked against `pypower/t/t_dcline.py`. |
| 13 | Buses with no in-service branch are solved as isolated (type NONE) without changing the case; DC lines touching them are not solved. |
| 14 | `areas` is not passed to the solver. Per-area results (`Case._area_summary`) are sums over the bus `area` column; an area's price is the OPF `lam_P` at its price reference bus. A case without `areas` gets default rows from the bus areas on load (`_default_areas`, `_price_ref`: REF bus, else most in-service capacity, else first bus). |
| 14b | Areas have an optional free-text `name` (issue #93), the third `areas` column (kind `name`); the integer `area` stays the key into the bus `area` column, which must stay numeric for the solver arrays. Names are kept outside PYPOWER's two-column `areas` array: `to_ppc()` (and `.py`/`.json` output) writes `areanames`, one string per `areas` row, only when some area has a name, and loading reads it back (absent: empty names; wrong length: `CaseError`). GLM output drops names with the rows (#15). Per-area results carry `name`. |
| 15 | `areas` is not written to GLM (module pypower has no such object); buses keep `area`, and a comment is written only when the rows differ from the defaults. |
| 16 | Renumbering an area in `areas` renumbers the `area` of its buses; deleting a price reference bus with `cascade` deletes the area row. Both are under review: issues #16 and #17. |
| 17 | `dclinecost` is written to GLM as `dclinecost` runtime-class objects with the `pypower.gencost` properties, parent the DC line, named `pp_dclinecost_<n>` (PR #48, closing #45), because `pypower.gencost` needs a generator parent. GLM input also reads `pypower.dclinecost`, the class GridLAB-D is expected to add; drop the runtime class declaration once it exists. (See https://github.com/arras-energy/gridlabd/issues/405) |
| 18 | GLM import keeps only `bus`, `gen`, `branch`, `gencost`, `dcline` (and ppedit's own `dclinecost`/`pp_geodata` objects); everything else is listed in `Case.notes`, and overwriting the source file needs `force` (HTTP 409 with `confirm`). |
| 19 | GLM preprocessor directives are not expanded. A GLM with no bus objects that uses `#include`/`#input`/`#begin` raises `CaseError` naming the files to open instead (issue #28), rather than loading as an empty case. |
| 20 | GLM object names: `pp_bus_<bus_i>` (by bus number, unlike `py2glm.py`'s row order), `pp_gen_<n>`, `pp_branch_<n>`, `pp_gencost_<n>`, `pp_dcline_<n>` (1-based rows). |
| 21 | Geodata is a separate list on `Case` (`geodata` rows `[BUS_I,LAT,LON,aux...]`, names in `geotags`), not a case table. It is read from CSV, from `geodata`/`geotags` keys in `.py`/`.json`, or from GLM bus `latitude`/`longitude` plus `pp_geodata` runtime-class objects for auxiliary values (issue #40, PR #44); written back the same way. Tags that are not GLM names are renamed (`_glm_tag_names`) and read back renamed. |
| 22 | Geodata is not edited in the diagram (buses are not draggable in the geodata layout); force-layout positions are not persisted. Both are planned (TODO). |
| 23 | Map layers are fetched by the browser directly from public U.S. government services (USGS The National Map imagery, Census TIGERweb roads and state/county boundaries): no server proxy, no cache, US coverage only, and offline the layers simply do not draw. |
| 24 | The diagram is drawn in screen coordinates for the simple and classic styles (symbols and text keep their size when zooming; pan/zoom re-render through `scheduleRender()`), and in layout units under one transform for the one-line style (it scales as a whole). |
| 25 | The one-line style computes its own layered layout from the topology (`onelineLayout`, cached by topology signature in `olCache`): rows by distance from the nearest generator bus; each line gets a column at each of its two bars joined by one horizontal jog. Buses are not draggable. Its width and crossings on large meshed cases are open (issues #24, #25). |
| 26 | Area regions (`areaRegions`) are built from per-site discs clipped to the half-planes nearer than any other area's site, so regions never overlap. Only each area's outer hull is drawn (issue #106): the discs are rasterized, gaps they enclose (between the lines of a meshed network) are filled from the nearest area, and the outline is traced (`traceOutline`) and smoothed (`smoothLoop`), so neighboring areas share one border and there are no inner boundaries. In the geodata layout each area also grows into its convex hull (expanded by the disc radius) where no other area's discs are, ties going to the nearer area, so shapes are as convex as their neighbors allow; turning areas on adds area cohesion to the force layout and per-area column strips to the one-line layout. |
| 26b | The diagram's style and layout are chosen when a case is opened (`defaultView` in `app.js`): the geodata layout (classic style) when the case has geodata, else the one-line style for cases with fewer than 20 buses (`ONELINE_MAX`), else the classic style (force layout). They are not stored in `localStorage` (the **Areas** choice still is, as `ppedit.areas`). **View ▾** holds Re-layout, Fit, the style and layout choices (radio items) and the Areas check box; **Labels ▾**, **Map ▾** and the search box stay separate. |
| 27 | Label choices (**Labels ▾**) are per style and per table, stored in the browser's `localStorage` (`ppedit.labels`), not in the case. Labels are not placed to avoid collisions. |
| 28 | Search (`#net-search`) is client-side only over buses, branches and DC lines; one match is selected and centered, several open a menu (at most 200). Buses have no names, so free text matches geodata auxiliary columns only. |
| 29 | Security for a local server: `POST` requests must carry JSON and the header `X-PPEdit: 1` (cross-origin pages cannot send it without a CORS preflight, which is never granted), and requests whose `Host` is not a loopback name are refused unless `allow_remote=True`. There is no authentication. |
| 30 | Infinities travel as the strings `"Inf"`/`"-Inf"` in JSON. |
| 31 | API sessions (`ppedit/claude.py`) call the Anthropic Messages API with `urllib` (no SDK dependency) in background threads. Each tool call that changes the case is one `Editor.edit()` under `editor.lock` (an `RLock`), so model-generated edits are validated and undoable like browser edits; concurrent sessions interleave their edits. |
| 32 | Session data sources are chosen per session: `web` (the server-side `web_search_20250305` tool and a `fetch_url` tool run by the ppedit server, at most `FETCH_LIMIT` bytes) and `local` (`list_files`/`read_file` confined to the session `root`). Sessions live in server memory only. |
| 33 | The page polls `GET /api/claude` once a second while the Claude tab is open or a session is running, and reloads the state when `revision` changes or a session ran `solve` (no websockets or server push). |
| 34 | The CLI (`ppedit serve/convert/check`) is a thin layer over `Case` and `create_app`; `check` exits 1 on validation errors. `-v` enables INFO messages (files loaded/saved and HTTP request lines); `-d` enables DEBUG and tracebacks (issue #49). |
| 35 | Nothing needs network or credentials to build or test; the API session tests use a fake client. |
| 36a | Siemens PTI PSS/E power flow cases (`.raw`, revisions 23–35) are an input format only (issue #54): `INPUT_FORMATS` adds `.raw` to `FORMATS`, and saving to `.raw` raises `CaseError` (`writable_format`). `ppedit/_psse.py` follows MATPOWER's `psse2mpc` conversion so results can be checked against its expected outputs (optional test when the `matpower` package is installed; its PSS/E sample data is not redistributed), adding revisions 34–35, field lookup by `@!` header names, switched shunt and load/shunt status, switching devices as branches, `NOMV = 0` as the bus base voltage, star buses at the winding 1 base voltage, and `Vg` = the generator bus voltage in the case for generators that regulate another bus (`IREG`), so solved cases such as `examples/wecc240_case.raw` solve to their stored voltages. Data PYPOWER cannot hold is listed in `Case.notes`; names are not kept. |
| 36b | How a case was read is kept as `Case.source` (file, format, PSS/E revision, rows per table, and the load notes) and sent as editor state `source` until another case is opened; it survives saving, which clears `Case.notes`. The Results tab shows it, and opening a file with notes switches to Results instead of showing a toast. |
| 36c | Results tables sort client-side by column (`block` in `app.js`): a header click sorts ascending, a second descending, a third restores the solver order. Keys are the displayed numbers (a cell may carry an exact `data-sort` value, e.g. loading), blanks (`–`, `unrated`) always last, ties in solver order. The sort is kept per table title in `S.resSort` for the page session (not in `localStorage`), so it survives new solves. |
| 36d | Case tables (Buses through Areas; issue #63) sort client-side by column for display only (`tableOrder` in `app.js`): a header click sorts ascending, a second descending, a third restores the case order. The case row order is never changed; rows keep their case index (row labels, selection, edits, Issues links). Keys are the cell values (`"Inf"`/`"-Inf"` as infinities, text such as `costs` compared numerically by character runs), blanks last, ties in case order. The sort is kept per table in `S.tableSort` for the page session (not in `localStorage`), so it survives edits and tab switches; an edited row moves to its sorted place when the table re-renders. |
| 36e | Run PF ▾ and Run OPF ▾ are menus, not a solver select beside buttons: picking a solver runs it. The choices come from `GET /api/meta` `solvers` (`ppedit.case.solvers()`): every `PF_SOLVERS` algorithm, and the `OPF_SOLVERS` whose Python module is installed (checked with `importlib.util.find_spec`, not imported), grouped AC then DC. `Case.solve(kind,algorithm)` validates `algorithm` against the table for `kind` (default `NR` for PF, `PIPS` for OPF) and refuses a solver whose module is missing; DC OPF runs `runopf` with `PF_DC=1` and the result carries `dc`, `algorithm` and `solver` (its label). |
| 36f | cvxopf (github.com/cvxgrp/cvxopf) is an optional OPF backend (issue #69), not a dependency (it needs Python 3.11+ and the IPOPT library): its four formulations are `OPF_SOLVERS` `CVXOPF-AC` (`ac`), `CVXOPF-SOCP` (`socp`, listed with AC), `CVXOPF-DC` (`lossy_dc`) and `CVXOPF-1NODE` (`singlenode_dc`), where the third tuple element is the cvxopf formulation name (a `str`) instead of a PYPOWER algorithm code. `ppedit/_cvxopf.py` passes the PYPOWER case (minus type NONE buses and what touches them, minus reactive cost rows) and returns the solution in `runopf` layout, so the rest of `Case.solve` (DC line rows, losses, areas, report) is shared. Values cvxopf does not compute are reported as unknown rather than estimated: `lam_P`/`mu_*` are `None`, the copper plate returns no branch rows, and DC models return flat voltages with zero angles. DC lines map to `HVDCLink(p_min_mw=-Pmax,p_max_mw=-Pmin)` (not `hvdc_from_dcline`, which reverses PYPOWER's direction). |
| 36g | The page reads optional URL parameters at the end of `init()` in `app.js` (added 2026-11-17 for pptools `pprun`, whose Network tab embeds this page): `?solve=pf\|opf` runs `POST /api/solve` once on load (`&algorithm=NAME` passed through, else the default solver; errors toast as usual) and `?view=NAME` opens that tab when it exists. Other values are ignored; nothing is stored. |
| 36h | Open ▾ is a menu (issue #83): *Local file…* is the browser file picker (upload, `{filename,text}`), and *From URL* is a URL text input in the menu that sends `POST /api/open {"url"}`. The ppedit server downloads the file (`download_case` in `server.py`, `urllib`, `http`/`https` only, `DOWNLOAD_TIMEOUT` seconds, at most `DOWNLOAD_LIMIT` bytes) outside `editor.lock`, rather than the browser, so hosts without CORS headers work; GitHub file page URLs (`github.com/OWNER/REPO/blob/REF/PATH`) are changed to `raw.githubusercontent.com` URLs (`github_raw_url`) and other HTML responses are refused with a hint to use the raw link, the extension of the URL path selects the format, `Case.source["file"]` is the URL downloaded, and like an upload the case has no path until Save as. `.py` cases stay parsed with `ast` (decision #7), so a remote case runs no code. The test patches `urlopen` (decision #35). |
| 36i | Main tabs are Network, Tables, Results, Issues, Claude, Settings (2026-11-18). The seven case tables are one **Tables** tab with a second row of table tabs (`#table-tabs`) at the top of `#view-table`, shown only while a table is open; the Tables tab opens the table shown last (`S.table`, default `bus`, page session only). Table names stay view names (`show("bus")`), so `?view=bus` (decision #36g), Issues links and `goto` are unchanged, and `?view=tables` opens the last table. The case globals `baseMVA` and `version` moved from the tab bar to the **Settings** tab (`#view-settings`); the case name stays in the top bar. |
| 36j | Display precision of case table values (issue #70) is a browser setting on the Settings tab, stored in `localStorage` (`ppedit.precision`) like label choices (#27), not in the case. Decimal places are set per precision key (`PRECISION_UNITS`/`PRECISION_DEFAULTS` in `app.js`): the column unit, except `pu` columns, split into `puV` (`Vm`, `Vmax`, `Vmin`, `Vg`, `Vf`, `Vt`) and `puMVA` (impedances `r`, `x`, `b`); defaults MW 2, MVAr 2, puMVA 3, deg 3, puV 3, $ 2, MW/MW 3 (from the issue), MVA 2, kV 3, MW/min 2, MVAr/min 2, unitless 4; a blank setting is full precision. A value within 1e-12 of 0 shows as `0`; otherwise `toFixed`, so a small value shows as `0.00`. Rounding is display only: table cells and side-panel inputs (`numInput`) show the full value while focused, so an edit never starts from or commits a rounded value; sorting and filtering use the stored values. Results, diagram labels and the solver report keep their own fixed formats. |
| 36k | cvxopf solve options (issue #88) are a browser setting on the Settings tab (`ppedit.cvxopf` in `localStorage`, like #36j), shown only when cvxopf is installed (`solvers()["cvxopf"]` lists the choices), and sent as `options` with every `CVXOPF-*` `POST /api/solve`; `Case.solve(kind,algorithm,options)` checks them (`_cvxopf.solve_options`, `CaseError` when invalid) and ignores them for other solvers. The network model stays the Run OPF ▾ choice (#36e, #36f). `preparation` is `default` (no `NumericalPreparation`: cvxopf's original coordinates and hourly objective assembly) or `recommended`, the per-model `build_opf` profile from the cvxopf authors (issue #88 comments; `_cvxopf.RECOMMENDED`): copper plate and lossy network flow `exact_fixed_boxes`, `canonical_scaling="joint5"`; SOCP adds `normalize_device_limits`; full AC `normalize_device_limits`, `exact_fixed_boxes`, `cost_coordinates` with `objective_assembly` left `hourly` (`component_first` is for `build_opf_multistep`). Prepared execution runs only with CLARABEL (convex) or IPOPT (AC), so `recommended` with another solver is refused. `solver` picks a CVXPY solver for the convex models (default CLARABEL; full AC is always IPOPT); `verbose` copies what the solver writes to file descriptors 1 and 2 (IPOPT prints from C; CVXPY logs to `stderr`) into `log`; `solver_options` are other `OPFBuild.solve` keyword arguments (tolerances, iteration limits), typed as `name=value` pairs and passed unchecked except that `solver`, `verbose` and `nlp` are reserved. Solver tolerances are otherwise the solver defaults. Horizons, profiles and devices (storage, renewables, curtailable loads) are not offered: see Open questions. |
| 36l | Solver errors and warnings (issue #89) are returned with each solution as `result.messages` (`{"level","message"}`, also written to `log`) and listed on the Issues tab under *Last solve*, above the case validation issues; they clear with the result on any edit. Errors: the solver raised (`failed: <exception>`) or did not converge (with the cvxopf solver status). Warnings: buses solved as isolated, DC lines not solved, data cvxopf does not model, cvxopf `UserWarning`s, an SOCP relaxation that is not exact or whose recovered voltages are not AC feasible, and the distinct Python warnings raised during the solve (deprecation and future warnings dropped; at most `SOLVER_WARNINGS` = 10 listed, with counts; before this they were silenced). The Issues tab count includes them and its status dot is red when there is any error (case or solver), yellow for warnings only; the solve toast points to Issues. Validation issues (`state.issues`, `Case.validate`) stay separate, so `ppedit check` and the diagram highlights are unchanged. |
| 36m | The top bar shows only the folder of the open file (`splitPath` in `app.js`; the full path is the tooltip, `not saved to a file` when there is none) and is a button that opens the folder chooser `#folder-menu` (2026-11-18, issue #97). The chooser lists folders on the server machine, not the browser's, because saving is done by the server (`POST /api/folders` → `list_folder` in `server.py`: subfolders not starting with `.` and the files with a writable extension, shown for reference; default the open file's folder, else the server's working directory). It is a POST so it needs `X-PPEdit` like the other routes that touch files (decision #29). The chooser only chooses: *Use this folder* sends `POST /api/folder {path}`, which moves `Editor.path` into that folder keeping the file name (else `<case name>.glm`; `.raw` becomes `.glm`) and marks the case unsaved when the path changes; nothing is written. Saving stays with *Save* and *Save as* (so the GLM dropped-content guard of `POST /api/save` still applies). |
| 36n | One-line switching devices act on a click (2026-11-18, issue #99): a branch breaker toggles the branch `status` and a generator's disconnect switch the gen `status` (`POST /api/cell`); a load's switch sends `POST /api/load {bus}` (`Editor.toggle_load`), which sets `Pd = Qd = 0` and keeps the old values in `Editor.loads_off` (bus number → `[Pd, Qd]`), or restores them. PYPOWER has no load status, so the values are kept by the server, not in the case: they are part of the undo snapshots, sent as state `loads_off` (so the switched-off load keeps its column, symbol and open switch in `onelineLayout`), dropped by any edit that leaves the bus missing or with nonzero `Pd`/`Qd`, cleared when a case is opened, and not saved (a saved case has `Pd = Qd = 0`). A click on the generator symbol opens the generator in the inspector (`S.pick.kind === "gen"`), not its bus. Breakers are drawn above the branch click target. |
| 36o | About dialog (2026-11-18): clicking the "ppedit" title (`#brand`) opens a modal `<dialog id="about">` (`showAbout` in `app.js`) with the package metadata from `/api/meta` `about` (`ppedit.server.about`): name, version, summary, the `Copyright` lines and first line of `LICENSE`, and the repository, documentation, issues and license URLs, all read at run time from the installed distribution metadata (`pyproject.toml` `[project]`, `LICENSE`, and the README as long description), falling back to the source tree. The license URL is `<repository>/blob/main/LICENSE`. The funding acknowledgment is the first README section whose heading mentions funding, an acknowledgment or a sponsor (`_readme_funding`); when there is none the dialog says so. |
| 36o | A solver warning may carry a `detail` (follow-up to PR #92): `explain`, what the warning means physically (fixed text per kind of warning, `_cvxopf.EXPLAIN`, `case.ISOLATED_EXPLAIN`, `case.DCLINE_CUT_EXPLAIN`); `constraints`, at most 20 rows of `constraint` (as cvxopf builds it, or as the relaxation leaves it out), `element` label, `table` and `row` of the case element (or `None`), and `value` (violation or data value); `more`, the rows not listed. Selecting such a warning on the Issues tab opens the detail below it (one at a time); element links jump to the table row, and the solver log stays one link away (messages without a detail still open Results). cvxopf reports buses and branches by its zero-based internal indexes: ppedit maps them to case rows and bus numbers and rewrites the message, and groups per-element warnings into one message (`rateA` = 0 branches; CVXPY "too many subexpressions" constraints, described by the quantities they relate, a compile-time note only). A non-exact SOCP relaxation lists the voltage-product cones that are not tight and the loops whose angles do not close (from `recover_socp_voltage`/`audit_socp_relaxation`), then the worst AC violation of each kind at the recovered voltages. cvxopf's `CostAccuracyWarning` (a `RuntimeWarning`, dropped before) is now reported. Warnings are recognized by their text, so a cvxopf wording change falls back to the plain message without a detail. Validation issues are unchanged. |
| 36p | Work-in-progress indicator (2026-11-20): while any request made through `api()` in `app.js` has been pending for `BUSY_DELAY` = 300 ms, `#busy` shows an indeterminate bar along the top of the page, a label below the top bar naming the oldest pending request (`BUSY_LABELS`: Solving, Opening case, Saving, …; else Working) with elapsed seconds after `BUSY_ELAPSED` = 2 s, and a progress cursor, so a slow solve, download or load does not look like a hung server. The delay keeps quick edits from flashing it. Polling (`GET /api/claude` and its state reloads, decision #33) passes `background` and does not count; map layer requests do not use `api()`. The page load counts as pending from the start (`#busy` is visible in `index.html` until `init()` ends). A request is removed in a `setTimeout` after its response, so the indicator stays up while the page re-renders from it; the bar moves by a CSS transform animation, which keeps running while the page script is busy. The page stays usable while it shows (no overlay blocks input). |
| 36q | Table data import/export (2026-11-20, issue #73): **Import…** and **Export ▾** sit between *Delete selected* and the filter box of every case table. Files are read and written by the server with pandas in `ppedit/_tableio.py` only, so the model stays plain lists (#4); pandas is a required dependency, and Parquet/Feather (`pyarrow`) and Excel (`openpyxl`) are offered only when installed (`table_formats`, `GET /api/meta` `table_formats`). The format comes from the file name extension (`.csv`, `.tsv`, `.json`, each optionally `.gz`/`.bz2`/`.xz`/`.zip`/`.zst`; `.parquet`, `.feather`, `.xlsx`); pickle is not offered (it runs code when read). Files have one column per table column, named as in `COLUMNS`, no index; JSON is records with infinities as `"Inf"`/`"-Inf"`. With no rows selected, import appends (`Case.import_rows`, through `add_row`: any subset of columns, defaults for missing columns and empty cells, cost rows added for new generators and DC lines) and export writes all rows; with rows selected, import replaces them in case order with the file rows in file order (every column and value required, row counts must match; changed values set as by `set_value`, so bus/area renumbers cascade, and numbers may be exchanged among the replaced rows; `ncost` follows `model` and `costs`) and export writes only them. The browser sends the file as base64 in JSON (`POST /api/table/import`, keeping decision #29's `X-PPEdit` rule) and the whole import is one `Editor.edit()` (one undo step, rolled back on any error, which names the file row). Export is `GET /api/table/export`, fetched first so an error shows as a toast instead of a downloaded error file. Export ignores the display sort and filter (#36d). |

## Conventions inherited from the `eudoxys-package` skill

- Repository layout, `pyproject.toml` with dependencies from
  `requirements.txt`, `Makefile` targets `docs` and `test`.
- Private folders start with `_` (`ppedit/_test/`) and carry a `README.md`;
  curated test data only, with provenance in `ppedit/_test/data/README.md`.
- pdoc documentation from docstrings, published to
  https://www.eudoxys.com/ppedit by `.github/workflows/deploy-pages.yaml`.
- `MANIFEST.md` maps every tracked file (`python .github/check_manifest.py`).
- Pull requests from new contributors must link an issue with the
  `approved` label (`.github/workflows/approved-issue.yaml`); every open
  issue is assigned to the owner (`.github/workflows/assign-issues.yaml`).
- `requires-python` matches the CI Python version (3.12).
- House style: no spaces after commas in calls and signatures, 4-space
  indent.

## Module layout

```
ppedit/
  __init__.py       package docs (spec, limits); re-exports Case, CaseError,
                    COLUMNS, TABLES, builtin_cases, create_app, main
  cli.py            main(argv): serve, convert, check, --version
  case.py           Case: tables, .py/.json/.glm I/O, edits, validation,
                    geodata, PF/OPF; Case(data=None,name="case"),
                    Case.load(path,text=None), Case.builtin(name)
  _dcline.py        add_dclines(ppc): DC line userfcn callbacks for pypower
  _psse.py          parse_raw(text): PSS/E RAW case -> ppc dict and notes
  _tableio.py       read_rows/dump_rows: table data files via pandas
  server.py         Editor (case, path, undo/redo, lock);
                    create_app(case,path,allow_remote,shutdown,claude_client)
  claude.py         Sessions(editor,client): Messages API tool-use loops
  static/
    index.html      page layout: toolbar, tabs (Network, Tables, Results,
                    Issues, Claude, Settings), table tabs, inspector, menus
    style.css       styles
    app.js          state rendering, tables, network diagram (styles,
                    layouts, map layers, labels, areas, search), results,
                    issues, Claude and Settings tabs
  _test/            pytest suite (fast, no network) and curated data
  # planned
  (none yet: planned work extends the modules above; see TODO.md)
```

### Contracts

`Case` has one constructor and two factories:

```python
Case(data:dict|None=None,name:str="case")
Case.load(path:str,text:str|None=None) -> Case    # format from extension
Case.builtin(name:str) -> Case                    # pypower built-in case
```

Edits return whether other cells changed (`set_value`) or what was
removed (`delete_rows`), and raise `CaseError` on invalid input; nothing is
partially applied, because the server rolls back through `Editor.edit()`.

| object | fields |
|---|---|
| `Case.tables` | `bus`, `gen`, `branch`, `gencost`, `dcline`, `dclinecost`, `areas`: lists of rows in `COLUMNS[table]` order (columns and units: `ppedit.case.COLUMNS`) |
| `Case` globals | `baseMVA`, `version`, `name` |
| `Case.geodata`, `Case.geotags` | rows `[BUS_I,LAT,LON,aux...]` (WGS84 degrees); auxiliary column names |
| `Case.notes` | content read but not kept (GLM objects, PSS/E data) |
| `Case.source` | how `Case.load` read the case: `file`, `format`, `revision`, `size`, `notes` (#36b) |
| `Case.validate()` | list of `{level,table,row,column,message}`, `level` `error` or `warning` |
| `Case.solve(kind,algorithm)` | `success`, `kind`, `algorithm`, `solver`, `dc`, `iterations`, `objective`, `losses_MW`, `dcline_losses_MW`, `log`, rows `bus`, `gen`, `branch`, `dcline`, `areas` (see the `Case.solve` docstring) |
| editor state (every API response) | `case`, `path`, `format`, `dirty`, `issues`, `can_undo`, `can_redo`, `result`, `cascade`, `revision`, `source`; Claude routes add `claude` |

The API routes are listed in the `ppedit.server` docstring, which is
authoritative.

## Sources

| input | read by | written by |
|---|---|---|
| PYPOWER `.py` case | `_parse_py` (ast, no execution) | `_dump_py` |
| PYPOWER `.json` case | `json` | `json` |
| GLM `module pypower` model | `_parse_glm` (solver objects, `dclinecost`, `pp_geodata`) | `_dump_glm`, `_dump_glm_dclinecost`, `_dump_glm_geodata` |
| geodata CSV (`BUS_I,LAT,LON,...`) | `Case.load_geodata` | `Case.dumps_geodata` (`/api/export?format=csv`) |
| PSS/E `.raw` case (revisions 23–35) | `ppedit._psse.parse_raw` (read only, #36) | — |
| PYPOWER built-in cases | `Case.builtin` | — |
| map tiles and boundaries | browser, from USGS and TIGERweb | — |
| web pages, local files (API sessions) | `ppedit.claude` tools | — |

Upstream references (read-only; sparse checkout of `arras-energy/gridlabd`):
`module/pypower/{bus,gen,branch,gencost,dcline}.cpp` for GLM property
names, units and keywords; `converters/py2glm.py` for the
`gridlabd convert -t pypower` output this package stays compatible with.

## Method

1. **Load.** The file extension selects the parser (`.raw` is converted
   to PYPOWER tables first, #36). Result columns present
   in an input case (`PF`, `lam_P`, ...) are dropped. Missing `areas` rows
   are filled from bus areas (#14). GLM content that is not kept goes to
   `Case.notes`.
2. **Edit.** Each browser or API session edit is one `Editor.edit()` call
   (#5); references cascade (#9, #10) and the last solution is cleared.
3. **Validate.** `Case.validate()` runs on every state response: missing
   REF bus, duplicate or negative bus numbers, dangling references,
   voltage limits, zero-impedance branches, malformed cost rows, area rows,
   DC line limits, and similar. Issues are reported, never auto-fixed.
4. **Solve.** `Case.solve` refuses a case with validation errors, marks
   isolated buses NONE in a copy (#13), adds DC line callbacks (#12), runs
   `pypower.runpf` or `runopf`, and returns result rows plus per-area sums
   (#14). Solver failures inside pypower are reported as they come.
5. **Apply.** `apply_result` copies the solved voltages and dispatch back
   into the case as one undoable edit.
6. **Save.** `Case.dumps(fmt)` writes the chosen format; GLM writes what
   it can hold (#15, #17, #21) and saving over a GLM source with notes
   needs `force` (#18).

Differences between PYPOWER and GridLAB-D results for the same model are
reported in docs (e.g. `loss1 = 0`, issue #12), not forced to match.

## Outputs

The outputs are case files in the input formats (see Sources) and the
editor state JSON (see Contracts). There are no generated data tables.

## Files and formats

- Case files: `.py`, `.json`, `.glm`; the extension selects the format.
  PSS/E `.raw` is read but not written.
- Geodata: CSV with at least `BUS_I`, `LAT`, `LON`; other columns kept.
- Table data (import/export of one case table, #36q): CSV, TSV, JSON
  records (optionally compressed), Parquet, Feather, Excel; columns named
  as in `COLUMNS`.
- Examples: `examples/pypower/case*.py` (copied from `pypower`) and
  `case*.glm` (generated by `examples/import_pypower_cases.py`);
  `examples/wecc240_*` (written by ppedit). Large examples (~85–350 kB)
  are listed in `MANIFEST.md`.

## Dependencies and gaps in other packages

| need | status |
|---|---|
| PF/OPF solver | `pypower` ✔ |
| DC line model | `pypower.toggle_dcline` fails with current numpy; new `_dcline.py` |
| PYPOWER OPF with piecewise-linear costs or Q cost rows (`case30pwl`, `case9Q`, `case30Q`) | fails inside pypower with current numpy; reported, not fixed |
| GridLAB-D `pypower.dclinecost` class | none yet; ppedit writes a runtime class (#17) |
| GridLAB-D areas object | none; areas dropped on GLM output (#15) |
| GLM preprocessor expansion | none in ppedit; option to use an installed `gridlabd` (issue #33) |
| web server | `flask` ✔ |
| table data files (#36q) | `pandas` ✔; `pyarrow` (Parquet, Feather) and `openpyxl` (Excel) optional |
| Anthropic API client | `urllib` from the standard library (no SDK) |

## Known issues to handle

- PYPOWER's OPF fails on piecewise-linear and reactive-power cost rows
  with current numpy (AC OPF only: the DC OPF solves them); `case9target`
  PF does not converge in PYPOWER either. DC OPF (PIPS) on PYPOWER's
  `t_case9_dcline` fails numerically inside pypower, with or without its
  DC lines.
- GridLAB-D replaces DC line `loss1 = 0` with its default loss factor
  (0.05), so ppedit and GridLAB-D can solve the same GLM differently
  (issue #12).
- Classic style: a bus with generation and load is drawn as a circle.
- One-line style: wide and dense on `case118`/`case300`; lines that skip
  rows cross bus bars (issues #24, #25).
- The USGS imagery has dark gaps over some water.
- Assumption: default area price reference bus is the REF bus, else the
  bus with the most in-service generating capacity, else the first bus;
  override by editing `areas.price_ref_bus`.

## Open questions

- **#11 GLM `dcline` `Sfrom`/`Sto` mapping.** Keep the positional mapping
  (current, decision #11) or switch to per-end MVA after an upstream fix
  in `pypower.cpp` and `py2glm.py`; and whether to report it upstream.
- **#12 `loss1 = 0` on GLM output.** Leave documented; warn in
  `Case.validate` or on GLM save; write a small nonzero `loss1`; or set
  `default_dcline_loss1` in the GLM if it can be set per model.
- **#13 Confirm before writing data GLM cannot hold.** Since PR #48,
  `dclinecost` is written to GLM, so the original case no longer applies;
  decide whether to close #13 or reuse its 409/`confirm` flow for
  `areas` price reference buses.
- **#16 Area renumbering.** Keep the bus cascade (current); never cascade;
  or cascade only on request with a `cascade` flag and a prompt.
- **#17 Deleting a price reference bus.** Delete the area row (current);
  keep the row and report the dangling reference; or move the price
  reference to another bus in the area.
- **#24, #25 One-line layout on large cases.** Column compaction, bus
  splitting, or routing that avoids crossing intermediate bus bars.
- **#54 PSS/E names and locations.** Bus names and (revision 35)
  substation coordinates could become geodata auxiliary columns and
  locations; they are dropped for now.
- **#88 cvxopf horizons and devices.** Multi-period studies
  (`build_opf_multistep` with horizon `T`, interval `delta` and demand
  profiles), cvxopf device models (storage, nondispatchable units with
  curtailment, curtailable loads) and hierarchical rollout need study
  configuration the PYPOWER case does not hold. The cvxopf authors suggest
  building every horizon with `build_opf_multistep` (including `T=1`, then
  with the `component_first` AC profile) and keeping horizon, profiles and
  devices under study configuration, apart from solve options (#36k).
- **#33 GLM files built with `#include`/`#input`.** Use an installed
  `gridlabd` to convert to JSON first, or keep refusing (decision #19).
