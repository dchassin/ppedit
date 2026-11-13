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
`arras-energy/gridlabd`): `module/pypower/{bus,gen,branch,gencost}.cpp` for
GLM property names, units and keywords; `converters/py2glm.py` for the
`gridlabd convert -t pypower` output this package should stay compatible with.

## Core conventions

- Tables are lists of rows ordered as `COLUMNS[table]`; `gencost` rows are
  `[model, startup, shutdown, ncost, costs]` with `costs` a comma-separated
  string (as in the GLM `costs` property).
- Every edit goes through `Case.set_value`, `add_row`, `delete_rows`,
  `set_global`, or `apply_result`, which keep references valid (bus
  renumber cascades, bus delete needs `cascade`, gencost follows gen). The
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

Nothing needs network or credentials.

## Known rough edges (don't "fix" silently; mention or ask)

- PYPOWER's OPF fails (inside pypower) on piecewise-linear costs and on
  reactive-power cost rows (`case30pwl`, `case9Q`, `case30Q`) with current
  numpy; the editor reports the solver error. `case9target` PF does not
  converge in PYPOWER either.
- GLM import keeps only `bus`, `gen`, `branch`, `gencost`; everything else
  is listed in `Case.notes`, and saving over the source file needs `force`.
- GLM bus names are `pp_bus_<bus_i>`, unlike `py2glm.py` (`pp_bus_<row>`).
- Network positions are not persisted. The diagram is drawn in screen
  coordinates (symbols and text keep their size when zooming), so pan/zoom
  re-renders through `scheduleRender()`; fine to ~300 buses.
- Classic style: a bus with generation and load is drawn as a circle.

## Working rules

- Don't commit generated output; `ppedit/_test/data/` is curated (see its
  README).
- When a public signature, column, or API route changes, update the module
  docstring, the package docstring, `README.md`, and the server API list.
- Update `MANIFEST.md` when adding, moving, or deleting files; run
  `python .github/check_manifest.py`.
- Conventional small test cases: `case9` (built-in) and
  `ppedit/_test/data/case3.glm`.
