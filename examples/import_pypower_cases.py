"""Import the PYPOWER built-in cases into `examples/pypower/`

Copies each `case*.py` shipped with the installed `pypower` package, writes
a GridLAB-D `module pypower` model (`.glm`) for it with `ppedit`, copies
the PYPOWER license and authors files, and rewrites the case table in
`examples/pypower/README.md` (between the `<!-- cases -->` markers).

Run from the repository root:

    python3 examples/import_pypower_cases.py

Re-run after upgrading `pypower` and commit the result.
"""

import datetime
import glob
import os
import re
import shutil
import sys
import warnings

import pypower

from ppedit import Case, builtin_cases

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)),"pypower")
SOURCE = os.path.dirname(pypower.__file__)

def _license_dir():
    """Folder holding the installed pypower LICENSE and AUTHORS files"""
    import importlib.metadata # pylint: disable=import-outside-toplevel
    dist = importlib.metadata.distribution("pypower")
    for file in dist.files or []:
        if os.path.basename(str(file)) == "LICENSE":
            return os.path.dirname(str(dist.locate_file(file)))
    raise FileNotFoundError("pypower LICENSE not found")

def main() -> int:
    """Import the cases and update the README table; return exit code"""
    warnings.simplefilter("ignore")
    os.makedirs(HERE,exist_ok=True)
    for old in glob.glob(os.path.join(HERE,"case*.*")):
        os.remove(old)
    licenses = _license_dir()
    for name in ("LICENSE","AUTHORS"):
        shutil.copy(os.path.join(licenses,name),os.path.join(HERE,name))
    rows = []
    for name in builtin_cases():
        shutil.copy(os.path.join(SOURCE,f"{name}.py"),os.path.join(HERE,f"{name}.py"))
        case = Case.builtin(name)
        case.save(os.path.join(HERE,f"{name}.glm"))
        errors = [x for x in case.validate() if x["level"] == "error"]
        pf = case.solve("pf") if not errors else {"success":False}
        opf = case.solve("opf") if not errors and case.tables["gencost"] else None
        size = case.size()
        load = sum(b[2] for b in case.tables["bus"])
        rows.append(f"| `{name}` | {size['bus']} | {size['gen']} | {size['branch']} "
            f"| {size['gencost']} | {load:,.1f} | {'yes' if pf['success'] else 'no'} "
            f"| {'—' if opf is None else 'yes' if opf['success'] else 'no'} |")
        print(f"{name}: {size}",file=sys.stderr)
    table = "\n".join([
        f"Imported from `pypower` {pypower_version()} on {datetime.date.today().isoformat()}.",
        "",
        "| Case | Buses | Generators | Branches | Cost rows | Load (MW) | PF converges | OPF converges |",
        "|---|--:|--:|--:|--:|--:|:-:|:-:|",
        *rows,
    ])
    readme = os.path.join(HERE,"README.md")
    with open(readme,encoding="utf-8") as fh:
        text = fh.read()
    text = re.sub(r"<!-- cases -->.*<!-- /cases -->",
        f"<!-- cases -->\n{table}\n<!-- /cases -->",text,flags=re.S)
    with open(readme,"w",encoding="utf-8") as fh:
        fh.write(text)
    return 0

def pypower_version() -> str:
    """Installed pypower version"""
    import importlib.metadata # pylint: disable=import-outside-toplevel
    return importlib.metadata.version("pypower")

if __name__ == "__main__":
    sys.exit(main())
