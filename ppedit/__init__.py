"""Browser-based editor for PYPOWER and Arras Energy `module pypower` models

Syntax
------

    ppedit serve [FILE] [-p PORT] [--host HOST] [--no-browser] [-d] [-w]
    ppedit convert INPUT OUTPUT [-d] [-w]
    ppedit check FILE [-d] [-w]

Positional arguments
--------------------

  - `serve`: start the editor web server and open it in a browser

  - `convert`: convert a case between `.py`, `.json`, and `.glm`

  - `check`: print validation issues; exit code 1 if there are errors

  - `FILE`, `INPUT`, `OUTPUT`: case files; the extension selects the format

Optional arguments
------------------

  - `-d|--debug`: enable debug logging and traceback on exceptions

  - `-h|--help`: show this help message and exit

  - `-p|--port PORT`: server port (default 8050)

  - `--host HOST`: interface to listen on (default `127.0.0.1`)

  - `--no-browser`: do not open a browser window

  - `-w|--warning`: enable warning messages from python

Description
-----------

`ppedit` is a local web application for building and editing power system
models used by the PYPOWER solver, either as PYPOWER case files or as
Arras Energy GridLAB-D models that use `module pypower`. A Flask server
holds one case in memory and exposes it through a JSON API; the browser
page edits the bus, generator, branch, and generator cost tables, draws the
network, checks the case, and runs PYPOWER power flow and optimal power flow.

Given a case file, `ppedit.Case` is expected to satisfy:

  1. **Round trip.** `Case.load(path).dumps(fmt)` re-read with `Case.load`
     gives the same tables for `py`, `json`, and `glm`.

  2. **Referential integrity.** Edits through `Case.set_value`,
     `Case.add_row`, and `Case.delete_rows` never leave a generator or branch
     pointing to a bus that does not exist: renumbering a bus renumbers its
     references, and deleting a bus with connections is refused unless
     `cascade=True`.

  3. **Cost alignment.** When the case has `gencost`, adding or deleting a
     generator adds or deletes its cost row(s).

  4. **No code execution.** `.py` case files are parsed, never imported or
     run, so a case file from elsewhere cannot run code on the server.

What is not promised
--------------------

  - One case per server, held in memory, for one user on the local machine.
    There is no authentication: do not expose the server on a shared network.

  - Only the solver-layer GLM objects (`bus`, `gen`, `branch`, `gencost`)
    are edited; other `module pypower` objects (`load`, `powerplant`, ...)
    and other modules are reported on load and dropped on save.

  - `dcline` and `areas` data are not edited.

  - The network diagram is a force-directed layout, not geography; positions
    are not saved. In the one-line style a bus with both generation and load
    is drawn as a generator (circle); its label shows both MW values.

  - Buses with no in-service branch are solved as isolated (type NONE)
    without changing the case.

Data
----

Columns follow `pypower.caseformat`; units are listed in `ppedit.case.COLUMNS`.

```mermaid
flowchart LR
    file[(.py / .json / .glm)] --> Case
    Case <-->|JSON API| server[Flask server]
    server <--> browser[Browser editor]
    Case --> solver[PYPOWER runpf / runopf]
```
"""

try:
    import importlib.metadata
    __version__ = importlib.metadata.version("ppedit")
except Exception: # pylint: disable=broad-exception-caught
    __version__ = "dev"

from ppedit.case import Case, CaseError, COLUMNS, TABLES, builtin_cases
from ppedit.server import create_app
from ppedit.cli import main
