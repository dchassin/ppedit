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
| `test_cli.py` | `convert` and `check` commands and error exit codes |
| `test_geodata.py` | geodata CSV parsing and errors, `.py`/`.json`/`.glm` round trips, bus edit cascades, validation warnings, `/api/geodata`, `convert --geodata` |
| `data/` | curated inputs; see `data/README.md` |
