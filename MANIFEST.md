# MANIFEST

Map of the `eudoxys/ppedit` repository. Every tracked file is covered by
one row or glob below; check with `python .github/check_manifest.py`.
Update this file in the same PR that adds, moves, or deletes a file.

## Start here

1. `CLAUDE.md` — conventions and working rules
2. `DESIGN.md` — design decisions, contracts, open questions
3. `ppedit/__init__.py` — package docs (spec, limits) and public API
4. `ppedit/case.py` — case model, formats, validation, solver
5. `ppedit/server.py` — Flask app and JSON API

## Task index

- Add or change a table column: `COLUMNS` (and `GLM_UNITS`) in `ppedit/case.py`; validation in `Case.validate`; `ppedit/_test/test_case.py`
- Add a validation rule: `Case.validate`; test in `test_validate_finds_errors`
- Support another GLM object: `_parse_glm`/`_dump_glm` in `ppedit/case.py`; add it to `ppedit/_test/data/case3.glm` and its README
- Change area handling: `COLUMNS["areas"]`, `Case.validate` and `Case._area_summary` in `ppedit/case.py`; `areaRegions`/`convexHull`/`traceOutline`, area grouping in `layout`/`onelineLayout` in `ppedit/static/app.js`; `ppedit/_test/test_areas.py`
- Change the cvxopf solvers: `OPF_SOLVERS` and `Case.solve` in `ppedit/case.py`; `ppedit/_cvxopf.py`; `ppedit/_test/test_cvxopf.py`
- Change DC line handling: `COLUMNS["dcline"]`, `COST_TABLES` and `Case.solve` in `ppedit/case.py`; solver callbacks in `ppedit/_dcline.py`; `ppedit/_test/test_dcline.py`
- Change WECC 240 2020 areas (EIA-930 balancing authorities): `examples/wecc240_ba.csv`, then `python3 examples/wecc240_areas.py`; see `examples/README.md`
- Change WECC 240 generator costs: `examples/wecc240_gentypes.csv` (or rederive it with `examples/wecc240_gentypes.py`), then `python3 examples/build_wecc240_2018.py`; see `examples/README.md`
- Change PSS/E `.raw` input: `ppedit/_psse.py` (conversion), `INPUT_FORMATS`/`Case.load` in `ppedit/case.py`; `ppedit/_test/test_psse.py` and `ppedit/_test/data/psse_v*.raw`
- Add an API route: `ppedit/server.py` (and its docstring API list); `ppedit/_test/test_server.py`; call it from `ppedit/static/app.js`
- Change the UI: `ppedit/static/{index.html,style.css,app.js}`
- Change geodata handling or map layers: `Case.load_geodata`/`_geo_*` helpers in `ppedit/case.py`; `geoLayout`/`renderMap` in `ppedit/static/app.js`; `ppedit/_test/test_geodata.py`
- Add a CLI command or option: `ppedit/cli.py`, `ppedit/__init__.py` Syntax section, `README.md`
- Refresh the PYPOWER example cases: `python3 examples/import_pypower_cases.py` (after upgrading `pypower`)
- Change the Claude sessions (tools, data sources, models): `ppedit/claude.py`; `/api/claude*` routes in `ppedit/server.py`; Claude section of `ppedit/static/app.js`; `ppedit/_test/test_claude.py`
- Change a design decision or resolve an open question: `DESIGN.md` (never renumber decisions)
- Release: `pyproject.toml` version; see the eudoxys-package skill checklist

## Package

| Path | Purpose | Notes |
|---|---|---|
| `ppedit/__init__.py` | package docs, re-exports | pdoc landing page |
| `ppedit/cli.py` | CLI `main()`: serve, convert, check | entry point `ppedit`; tested by `_test/test_cli.py` |
| `ppedit/case.py` | `Case`: tables, `.py`/`.json`/`.glm` I/O (and `.raw` input), edits, validation, PF/OPF | tested by `_test/test_case.py` and its doctest |
| `ppedit/_psse.py` | PSS/E RAW reader (revisions 23–35), after MATPOWER `psse2mpc` | private; read only; tested by `_test/test_psse.py` |
| `ppedit/_cvxopf.py` | cvxopf OPF backend for `Case.solve` (`CVXOPF-*` solvers): case in, `runopf`-layout results out | private; optional dependency; tested by `_test/test_cvxopf.py` (skipped without cvxopf) |
| `ppedit/_dcline.py` | DC line model for the PYPOWER solver (`userfcn` callbacks, as `pypower.toggle_dcline`) | private; tested by `_test/test_dcline.py` |
| `ppedit/claude.py` | `Sessions`: Claude API conversations with tools that edit the case, web and local data sources | tested by `_test/test_claude.py` with a fake client |
| `ppedit/server.py` | `create_app()`, `Editor` (undo/redo), JSON API, `about()` package metadata | tested by `_test/test_server.py` |
| `ppedit/static/index.html` | editor page layout | |
| `ppedit/static/style.css` | editor styles | |
| `ppedit/static/app.js` | editor logic: tables, network diagram (simple, classic and one-line styles; force and geodata layouts with map layers; labels menu), results, issues, Claude sessions | checked by hand (see `CLAUDE.md`) |
| `ppedit/.gitignore` | generated-output ignores | |

## Tests

| Path | Purpose | Notes |
|---|---|---|
| `ppedit/_test/README.md` | how to run tests | |
| `ppedit/_test/conftest.py` | `client` and `post` fixtures | |
| `ppedit/_test/test_*.py` | tests | all fast, no network |
| `ppedit/_test/data/README.md` | test data provenance | |
| `ppedit/_test/test_examples.py` | checks `examples/pypower/` | each `.glm` matches its `.py` |
| `ppedit/_test/data/case3.glm` | 3-bus GLM test model | curated, hand-written |
| `ppedit/_test/data/psse_v33.raw` | 4-bus PSS/E revision 33 test case (loads, shunts, 2- and 3-winding transformers, DC line) | curated, hand-written; used by `test_psse.py` |
| `ppedit/_test/data/psse_v35.raw` | 3-bus PSS/E revision 35 test case (system-wide data, `@!` headers, switching devices) | curated, hand-written; used by `test_psse.py` |
| `ppedit/_test/data/IEEE_*_bus.raw` | IEEE 14, 24, 30, 39, 57, 118 and 300-bus cases as PSS/E revision 33 | added by the maintainer; used by `test_psse.py`; IEEE_300 ~135 kB |
| `ppedit/_test/data/case9_geodata.csv` | synthetic `case9` bus locations | curated, hand-written; used by `test_geodata.py` |

## Examples

| Path | Purpose | Notes |
|---|---|---|
| `examples/import_pypower_cases.py` | regenerates `examples/pypower/` | maintenance script |
| `examples/pypower/README.md` | provenance, conversion notes, case table | table generated by the script |
| `examples/pypower/LICENSE` | PYPOWER BSD license | copied from the `pypower` package |
| `examples/pypower/AUTHORS` | PYPOWER authors | copied from the `pypower` package |
| `examples/pypower/case*.py` | PYPOWER built-in cases, unchanged | copied; don't edit; case300.py ~60 kB |
| `examples/pypower/case*.glm` | the same cases as `module pypower` GLM models | generated; don't edit; case300.glm ~250 kB, use `head` |
| `examples/README.md` | provenance of the WECC 240 examples | |
| `examples/wecc240_case.raw` | WECC 240-bus case as PSS/E revision 34 (see `examples/README.md`) | from NREL (see file header); ~220 kB, use `head`; tested by `_test/test_psse.py` |
| `examples/wecc240_bus.csv` | WECC 240 bus index: row, PSS/E bus number, name, PYPOWER `bus_i` | from NREL |
| `examples/wecc240_cost.csv` | per-generator cost rows (`id` = bus name + PSS/E ID; `model,startup,shutdown,cost`) | costs copied from `wecc240_gentypes.csv` by type |
| `examples/wecc240_gentypes.csv` | polynomial costs (`c2,c1,c0`) by generator type (the PSS/E generator ID) | written by `wecc240_gentypes.py` |
| `examples/wecc240_gentypes.py` | derives `wecc240_gentypes.csv` from NREL's WECC 240 scheduling workbook | maintenance script; needs `openpyxl` |
| `examples/wecc240_2011.py` | the 2011 WECC 240-bus case (`wecc240_case.raw` converted by ppedit) with geodata; the Pacific DC Intertie and Intermountain HVDC lines as `dcline` rows (3.5% losses, zero `dclinecost`) | written by ppedit, DC lines edited (see `examples/README.md`); ~90 kB, use `head` |
| `examples/wecc240_2018.py` | `wecc240_2011.py` with generator costs by type from `wecc240_gentypes.csv`, branch `rateA` from `rateB`/`rateC`, voltage fixes (`Vg` 1.08 pu at five generator buses, shunt reactors at eight buses) and `rateA` raised to a 20% margin on the 13 most loaded branches | written by `build_wecc240_2018.py`; don't edit; ~95 kB, use `head` |
| `examples/build_wecc240_2018.py` | builds `wecc240_2018.py` from `wecc240_2011.py`, `wecc240_gentypes.csv`, `wecc240_cost.csv` (generator types) and `wecc240_bus.csv` | maintenance script |
| `examples/wecc240_2020.py` | the 2020 WECC 240-bus case with geodata; areas are the EIA-930 balancing authorities (set by `wecc240_areas.py`) | written by ppedit, areas set by `wecc240_areas.py`; ~165 kB, use `head` |
| `examples/wecc240_ba.csv` | EIA-930 balancing authority of each WECC 240 bus (`BUS_I,NAME,BA,NOTE`) | curated by hand (issue #90) |
| `examples/wecc240_areas.py` | sets the areas of a WECC 240 case (default `wecc240_2020.py`) from `wecc240_ba.csv`, in place | maintenance script |
| `examples/wecc240_case.py` | WECC 240-bus case without geodata | written by ppedit; ~70 kB, use `head` |
| `examples/wecc240_geodata.csv` | bus locations for the WECC 240-bus case (`BUS_I,LAT,LON` plus `GEOHASH,NAME,GEN,LOAD,BA,COUNTY`) | for `--geodata` or **Map ▸ Load CSV…** |
| `examples/wecc240_map.py` | WECC 240-bus case with its `geodata` and `geotags` | written by ppedit; ~105 kB, use `head` |
| `examples/wecc240_map.json` | the same case as JSON | written by ppedit; ~230 kB, use `head` |
| `examples/wecc240_map.glm` | the same case as a `module pypower` GLM model (keeps bus latitude/longitude) | written by ppedit; ~380 kB, use `head` |

## Repository

| Path | Purpose | Notes |
|---|---|---|
| `README.md` | user overview | |
| `CLAUDE.md` | assistant guidance | |
| `DESIGN.md` | design decisions, contracts, open questions | update with any decision change |
| `MANIFEST.md` | this file | |
| `CONTRIBUTING.md` | contributor guide | approved-issue rule |
| `TODO.md` | roadmap | |
| `pyproject.toml` | packaging | version here; static files in package-data |
| `requirements.txt` | dependencies | |
| `Makefile` | `make docs`, `make test` | |
| `LICENSE` | MIT | |
| `.gitignore` | | |
| `.gitattributes` | | |
| `.github/workflows/*.yaml` | CI: tests, docs, approved-issue check, issue assignment | |
| `.github/check_manifest.py` | checks this file | |

## Don't read

`docs/` (generated), `.venv/`, `*.egg-info/`.
