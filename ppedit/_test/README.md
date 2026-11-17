# Tests

Run from the repository root:

    pip install -e . pytest
    pytest

All tests are fast (a few seconds) and need no network or credentials.

| File | Checks |
|---|---|
| `conftest.py` | `client` (Flask test client on `case9`) and `post` fixtures |
| `test_case.py` | format round trips for all built-in cases, `.py` parsing without execution, GLM import conventions, edit cascades, validation, PF/OPF solve |
| `test_server.py` | API guard (header, host), edits, undo/redo, rollback of bad edits, rows, solve/apply, upload, export, save |
| `test_doctest.py` | examples in the `ppedit.case` docstring |
| `test_examples.py` | every `examples/pypower/*.glm` holds the same data as its `.py` |
| `test_cvxopf.py` | `CVXOPF-*` OPF solvers: AC matches PIPS, all formulations solve case9, isolated buses, piecewise-linear and reactive costs, DC line direction (skipped unless cvxopf is installed) |
| `test_cli.py` | `convert` and `check` commands and error exit codes |
| `test_areas.py` | `areas` table on `case30`: defaults rebuilt from bus areas, price reference bus, round trips, dropped on GLM output, not passed to the solver, renumber cascades, validation, per-area results (unlisted areas, isolated buses, DC line ties) |
| `test_dcline.py` | `dcline` and `dclinecost` tables: load and round trips (including `gridlabd convert` GLM style), PF and OPF against the expected values of `pypower/t/t_dcline.py`, isolated terminals, validation, edit cascades |
| `test_psse.py` | PSS/E RAW input on `data/psse_v33.raw`, `data/psse_v35.raw` and the IEEE cases: field parsing, buses, generators, branches, DC lines, read-only save guard, errors, API and CLI, `examples/wecc240_case.raw` |
| `test_claude.py` | `ppedit.claude` and `/api/claude`: sessions with a fake API client (no key), edits with undo, tool errors rolled back, local reads confined to the root, several sessions |
| `test_geodata.py` | geodata CSV parsing and errors, `.py`/`.json`/`.glm` round trips, bus edit cascades, validation warnings, `/api/geodata`, `convert --geodata` |
| `data/` | curated inputs; see `data/README.md` |
