[![Validation](https://github.com/dchassin/ppedit/actions/workflows/validate.yaml/badge.svg)](https://github.com/dchassin/ppedit/actions/workflows/validate.yaml)
[![Documentation](https://github.com/dchassin/ppedit/actions/workflows/deploy-pages.yaml/badge.svg)](https://github.com/dchassin/ppedit/actions/workflows/deploy-pages.yaml)

Browser-based editor for PYPOWER and Arras Energy `module pypower` models

`ppedit` runs a small local web server and opens an editor in your
browser. It reads and writes PYPOWER case files (`.py`, `.json`) and Arras
Energy GridLAB-D models that use `module pypower` (`.glm`). You edit the bus,
generator, branch and generator-cost tables, see the network drawn as a
one-line diagram, get immediate validation (missing REF bus, dangling
references, zero-impedance branches, malformed cost curves, ...), and run
PYPOWER power flow and optimal power flow, with voltages and line loadings
shown on the diagram. It is for engineers preparing transmission and
sub-transmission models for Arras Energy.

# Documentation

See https://dchassin.github.io/ppedit

# Installation

Install using `pip`:

    pip install git+https://github.com/dchassin/ppedit

# Usage

    ppedit serve                 # new, empty case
    ppedit serve case14.glm      # edit a file; Save writes back to it
    ppedit convert case14.py case14.glm
    ppedit check case14.glm

The editor opens at http://localhost:8050/. Start from **New ▸** to load
any PYPOWER built-in case (`case9`, `case14`, ..., `case300`), or **Open…**
to upload a file. **Save as ▸** downloads `.py`, `.json` or `.glm`.

In the editor:

  - **Network**: drag buses, scroll to zoom, click a bus or branch to edit
    it in the side panel; add a generator, a branch, or a new linked bus from
    the panel. Two styles:
    - *Classic*: buses colored by type; after a solve, by voltage.
    - *One-line* (utility on-line diagram conventions): generator buses
      are circles, load buses triangles, other buses squares, and
      disconnected buses are drawn empty; transformers show as interlocking
      circles. Buses are colored by voltage magnitude (blue low, red high)
      and labeled with the bus number, voltage angle, and generation (G) and
      load (L) in MW. After a solve, lines are colored by loading as a
      percentage of `rateA` (blue light, red at or over the rating) and
      labeled with their MW flow, with an arrow in the direction of flow.
      Before a solve, the case's own Vm, Va and Pg values are shown.
  - **Buses / Generators / Branches / Gen costs**: spreadsheet-style
    editing; Enter or Tab commits, Escape cancels. Renumbering a bus updates
    the generators and branches that use it; deleting a bus asks before
    deleting its connections; generator cost rows follow their generators.
  - **Results**: PF/OPF summary, bus voltages (and LMPs for OPF), dispatch,
    branch flows and loading, the PYPOWER report, and **Apply solution to
    case** to copy the solved Vm/Va/Pg/Qg into the model.
  - **Issues**: every validation error and warning; click one to jump to
    the cell.
  - Undo/redo (Ctrl+Z, Ctrl+Shift+Z) covers every edit; Ctrl+S saves.

The server listens on `127.0.0.1` only and has no authentication; it is a
single-user desktop tool.

# Maintenance

## Update docs

    make docs

## Run tests

    pip install -e . pytest
    pytest

The tests need no network or credentials; see
[`ppedit/_test/README.md`](ppedit/_test/README.md).

# Examples

## Command line

    ppedit check ppedit/_test/data/case3.glm

outputs

    ppedit/_test/data/case3.glm: no issues found

## Python

    from ppedit import Case
    case = Case.builtin("case9")
    case.set_value("bus", 4, "Pd", 95)
    result = case.solve("opf")
    print(result["success"], round(result["objective"], 2))
    case.save("case9.glm")

outputs

    True 5422.68
