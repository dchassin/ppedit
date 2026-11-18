# dchassin/ppedit — package design (draft 1)

Status: design recorded as built, 2026-11-15 (version 0.2.0, after PR #50; issue #52); decision #36f added 2026-11-17 (issue #69); inherited conventions revised 2026-11-17; decision #36g added 2026-11-17 (page URL parameters); decision #36h added 2026-11-18 (open from URL, issue #83).
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
| 26 | Area regions (`areaRegions`) are drawn as unions of per-site discs clipped to the half-planes nearer than any other area's site, so regions never overlap; turning areas on adds area cohesion to the force layout and per-area column strips to the one-line layout. |
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
| 36 | Siemens PTI PSS/E power flow cases (`.raw`, revisions 23–35) are an input format only (issue #54): `INPUT_FORMATS` adds `.raw` to `FORMATS`, and saving to `.raw` raises `CaseError` (`writable_format`). `ppedit/_psse.py` follows MATPOWER's `psse2mpc` conversion so results can be checked against its expected outputs (optional test when the `matpower` package is installed; its PSS/E sample data is not redistributed), adding revisions 34–35, field lookup by `@!` header names, switched shunt and load/shunt status, switching devices as branches, `NOMV = 0` as the bus base voltage, star buses at the winding 1 base voltage, and `Vg` = the generator bus voltage in the case for generators that regulate another bus (`IREG`), so solved cases such as `examples/wecc240_case.raw` solve to their stored voltages. Data PYPOWER cannot hold is listed in `Case.notes`; names are not kept. |
| 36b | How a case was read is kept as `Case.source` (file, format, PSS/E revision, rows per table, and the load notes) and sent as editor state `source` until another case is opened; it survives saving, which clears `Case.notes`. The Results tab shows it, and opening a file with notes switches to Results instead of showing a toast. |
| 36c | Results tables sort client-side by column (`block` in `app.js`): a header click sorts ascending, a second descending, a third restores the solver order. Keys are the displayed numbers (a cell may carry an exact `data-sort` value, e.g. loading), blanks (`–`, `unrated`) always last, ties in solver order. The sort is kept per table title in `S.resSort` for the page session (not in `localStorage`), so it survives new solves. |
| 36d | Case tables (Buses through Areas; issue #63) sort client-side by column for display only (`tableOrder` in `app.js`): a header click sorts ascending, a second descending, a third restores the case order. The case row order is never changed; rows keep their case index (row labels, selection, edits, Issues links). Keys are the cell values (`"Inf"`/`"-Inf"` as infinities, text such as `costs` compared numerically by character runs), blanks last, ties in case order. The sort is kept per table in `S.tableSort` for the page session (not in `localStorage`), so it survives edits and tab switches; an edited row moves to its sorted place when the table re-renders. |
| 36e | Run PF ▾ and Run OPF ▾ are menus, not a solver select beside buttons: picking a solver runs it. The choices come from `GET /api/meta` `solvers` (`ppedit.case.solvers()`): every `PF_SOLVERS` algorithm, and the `OPF_SOLVERS` whose Python module is installed (checked with `importlib.util.find_spec`, not imported), grouped AC then DC. `Case.solve(kind,algorithm)` validates `algorithm` against the table for `kind` (default `NR` for PF, `PIPS` for OPF) and refuses a solver whose module is missing; DC OPF runs `runopf` with `PF_DC=1` and the result carries `dc`, `algorithm` and `solver` (its label). |
| 36f | cvxopf (github.com/cvxgrp/cvxopf) is an optional OPF backend (issue #69), not a dependency (it needs Python 3.11+ and the IPOPT library): its four formulations are `OPF_SOLVERS` `CVXOPF-AC` (`ac`), `CVXOPF-SOCP` (`socp`, listed with AC), `CVXOPF-DC` (`lossy_dc`) and `CVXOPF-1NODE` (`singlenode_dc`), where the third tuple element is the cvxopf formulation name (a `str`) instead of a PYPOWER algorithm code. `ppedit/_cvxopf.py` passes the PYPOWER case (minus type NONE buses and what touches them, minus reactive cost rows) and returns the solution in `runopf` layout, so the rest of `Case.solve` (DC line rows, losses, areas, report) is shared. Values cvxopf does not compute are reported as unknown rather than estimated: `lam_P`/`mu_*` are `None`, the copper plate returns no branch rows, and DC models return flat voltages with zero angles. DC lines map to `HVDCLink(p_min_mw=-Pmax,p_max_mw=-Pmin)` (not `hvdc_from_dcline`, which reverses PYPOWER's direction). |
| 36g | The page reads optional URL parameters at the end of `init()` in `app.js` (added 2026-11-17 for pptools `pprun`, whose Network tab embeds this page): `?solve=pf\|opf` runs `POST /api/solve` once on load (`&algorithm=NAME` passed through, else the default solver; errors toast as usual) and `?view=NAME` opens that tab when it exists. Other values are ignored; nothing is stored. |
| 36h | Open ▾ is a menu (issue #83): *Local file…* is the browser file picker (upload, `{filename,text}`), and *From URL* is a URL text input in the menu that sends `POST /api/open {"url"}`. The ppedit server downloads the file (`download_case` in `server.py`, `urllib`, `http`/`https` only, `DOWNLOAD_TIMEOUT` seconds, at most `DOWNLOAD_LIMIT` bytes) outside `editor.lock`, rather than the browser, so hosts without CORS headers work; GitHub file page URLs (`github.com/OWNER/REPO/blob/REF/PATH`) are changed to `raw.githubusercontent.com` URLs (`github_raw_url`) and other HTML responses are refused with a hint to use the raw link, the extension of the URL path selects the format, `Case.source["file"]` is the URL downloaded, and like an upload the case has no path until Save as. `.py` cases stay parsed with `ast` (decision #7), so a remote case runs no code. The test patches `urlopen` (decision #35). |
| 36i | Main tabs are Network, Tables, Results, Issues, Claude, Settings (2026-11-18). The seven case tables are one **Tables** tab with a second row of table tabs (`#table-tabs`) at the top of `#view-table`, shown only while a table is open; the Tables tab opens the table shown last (`S.table`, default `bus`, page session only). Table names stay view names (`show("bus")`), so `?view=bus` (decision #36g), Issues links and `goto` are unchanged, and `?view=tables` opens the last table. The case globals `baseMVA` and `version` moved from the tab bar to the **Settings** tab (`#view-settings`); the case name stays in the top bar. |
| 36j | Display precision of case table values (issue #70) is a browser setting on the Settings tab, stored in `localStorage` (`ppedit.precision`) like label choices (#27), not in the case. Decimal places are set per precision key (`PRECISION_UNITS`/`PRECISION_DEFAULTS` in `app.js`): the column unit, except `pu` columns, split into `puV` (`Vm`, `Vmax`, `Vmin`, `Vg`, `Vf`, `Vt`) and `puMVA` (impedances `r`, `x`, `b`); defaults MW 2, MVAr 2, puMVA 3, deg 3, puV 3, $ 2, MW/MW 3 (from the issue), MVA 2, kV 3, MW/min 2, MVAr/min 2, unitless 4; a blank setting is full precision. A value within 1e-12 of 0 shows as `0`; otherwise `toFixed`, so a small value shows as `0.00`. Rounding is display only: table cells and side-panel inputs (`numInput`) show the full value while focused, so an edit never starts from or commits a rounded value; sorting and filtering use the stored values. Results, diagram labels and the solver report keep their own fixed formats. |

## Conventions inherited from the `eudoxys-package` skill

- Repository layout, `pyproject.toml` with dependencies from
  `requirements.txt`, `Makefile` targets `docs` and `test`.
- Private folders start with `_` (`ppedit/_test/`) and carry a `README.md`;
  curated test data only, with provenance in `ppedit/_test/data/README.md`.
- pdoc documentation from docstrings, published to
  https://www.chassin.org/ppedit by `.github/workflows/deploy-pages.yaml`.
- `MANIFEST.md` maps every tracked file (`python .github/check_manifest.py`).
- Pull requests from new contributors must link an issue with the
  `approved` label (`.github/workflows/approved-issue.yaml`); every open
  issue is assigned to the owner (`.github/workflows/assign-issues.yaml`).
- `requires-python` matches the CI Python version (3.12).
- House style: no spaces after commas in calls and signatures, 4-space
  indent.

`ppedit` departs from the skill in one way: it lives at `dchassin/ppedit`
and is documented at chassin.org rather than eudoxys.com. Since `epm`
indexes only the eudoxys organization, `epm install ppedit` does not work;
install with `pip install git+https://github.com/dchassin/ppedit`.

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
- **#33 GLM files built with `#include`/`#input`.** Use an installed
  `gridlabd` to convert to JSON first, or keep refusing (decision #19).
