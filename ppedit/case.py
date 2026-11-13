"""PYPOWER case model

The `Case` class holds one PYPOWER case (`version`, `baseMVA`, `bus`, `gen`,
`branch`, and optional `gencost`, `dcline` and `dclinecost`) as plain Python
lists so it can be edited row by row and sent to a browser as JSON. It reads
and writes:

  - PYPOWER case files (`.py`), parsed with `ast` (the file is never run);
    the case may be built up in a variable (`ppc = {...}`) or returned as a
    dict literal (`return {...}`);
  - PYPOWER JSON (`.json`), the same keys as a `ppc` dict with list arrays;
  - Arras Energy GridLAB-D models (`.glm`) that use `module pypower`
    (`bus`, `gen`, `branch`, `gencost` and `dcline` objects).

DC lines (`dcline`, columns as in `pypower.idx_dcline`) connect two buses
with a controlled real power flow `Pf` and linear losses `loss0 +
loss1*Pf`; optional `dclinecost` rows (one per DC line, in the `gencost`
format) price the flow `Pf` for OPF. `Case.solve` models them as in
`pypower.toggle_dcline`.

A case may also carry bus locations (`Case.geodata`): rows of `[BUS_I, LAT,
LON, ...]` in decimal degrees (WGS84), where any further values are
auxiliary data named by `Case.geotags`. Geodata is read from the case's
`geodata` key (and `geotags` for the auxiliary names), from GLM bus
`latitude`/`longitude`, or from a CSV file with at least the columns
`BUS_I`, `LAT` and `LON` (`Case.load_geodata`); any other CSV columns are
kept as auxiliary data. The editor draws the network on a map from it.

Examples
--------

    >>> from ppedit import Case
    >>> case = Case.builtin("case9")
    >>> case.size()
    {'bus': 9, 'gen': 3, 'branch': 9, 'gencost': 3, 'dcline': 0, 'dclinecost': 0}
    >>> case.set_value("bus", 4, "Pd", 95)  # True would mean other cells changed
    False
    >>> [issue["message"] for issue in case.validate()]
    []
    >>> result = case.solve("pf")
    >>> result["success"], round(result["bus"][0]["Vm"], 3)
    (True, 1.0)
    >>> case.load_geodata("geo.csv", "BUS_I,LAT,LON,NAME\\n1,37.42,-122.17,Plant A\\n")
    1
    >>> case.geodata, case.geotags
    ([[1, 37.42, -122.17, 'Plant A']], ['NAME'])

Caveats
-------

  - Only the solver-layer objects of `module pypower` are edited. Other
    objects in a GLM file (`load`, `powerplant`, `powerline`, `relay`,
    `shunt`, `transformer`, `scada`, `weather`, `geodata`, ...) are counted
    and reported by `Case.load()` but are not kept, so saving to GLM drops
    them. `areas` data is not supported.

  - GLM `dcline` objects: `Sfrom` holds `Pf + j Pt` and `Sto` holds
    `Qf + j Qt`. This is how GridLAB-D passes these properties to PYPOWER
    (real and imaginary parts in PYPOWER column order, `module/pypower/
    pypower.cpp`) and how `gridlabd convert -t pypower` writes them, so the
    matrix GridLAB-D solves matches the case; it differs from the property
    descriptions in `dcline.cpp` (MVA flow at each end). `dclinecost` is
    not written to GLM; a comment in the output says how many rows were
    left out. GridLAB-D replaces `loss1 = 0` with its default loss factor
    (`pypower::default_dcline_loss1`, 0.05) unless it can infer one from
    `Sfrom` and `Sto`.

  - GLM output names buses `pp_bus_<bus_i>`, generators `pp_gen_<n>`,
    branches `pp_branch_<n>` and cost curves `pp_gencost_<n>` (1-based row
    order) and DC lines `pp_dcline_<n>` (1-based). `gridlabd convert -t
    pypower` names buses by row order and DC lines from 0 instead; the bus
    names agree when bus numbers run 1..N in order.

  - PYPOWER result columns (e.g. `PF`, `QF`, `lam_P`) present in an input
    case are dropped on load; only the input columns are edited.

  - Geodata is written to `.py` and `.json` cases as the `geodata` list
    (a plain list in `.py`, not a numpy array, because auxiliary values may
    be text) and `geotags`. GLM output keeps only each bus's `latitude` and
    `longitude`; auxiliary geodata is not written to GLM. Geodata rows are
    not passed to the solver.

References
----------

  - PYPOWER case format: `pypower.caseformat`
  - Arras Energy `module pypower`: https://github.com/arras-energy/gridlabd/tree/master/module/pypower
"""

import ast
import csv
import io
import json
import logging
import math
import os
import re
import contextlib
import warnings

_logger = logging.getLogger(__file__)

# ---------------------------------------------------------------------------
# column specifications
# ---------------------------------------------------------------------------

def _col(name,unit="",kind="float",default=0.0,desc="",options=None,glm=None):
    return {"name":name,"unit":unit,"kind":kind,"default":default,
            "desc":desc,"options":options,"glm":glm or name}

BUS_TYPES = {1:"PQ",2:"PV",3:"REF",4:"NONE"}
"""PYPOWER bus type codes and their `module pypower` keywords"""

GEN_STATUS = {1:"IN_SERVICE",0:"OUT_OF_SERVICE"}
"""Generator status codes and their `module pypower` keywords"""

BRANCH_STATUS = {1:"IN",0:"OUT"}
"""Branch status codes and their `module pypower` keywords"""

COST_MODELS = {1:"PIECEWISE",2:"POLYNOMIAL"}
"""Generator cost model codes and their `module pypower` keywords"""

DCLINE_STATUS = {1:"IN",0:"OUT"}
"""DC line status codes and their `module pypower` keywords"""

def _cost_columns(what):
    return [
        _col("model","","enum",2,"cost model",COST_MODELS),
        _col("startup","$",desc="startup cost"),
        _col("shutdown","$",desc="shutdown cost"),
        _col("ncost","","int",3,"number of cost coefficients or points"),
        _col("costs","","text","0,0,0",
            f"comma-separated cost data in {what}: c(n-1),...,c0 for POLYNOMIAL; "
            "p1,f1,p2,f2,... for PIECEWISE"),
    ]

COLUMNS = {
    "bus": [
        _col("bus_i","","int",0,"bus number"),
        _col("type","","enum",1,"bus type",BUS_TYPES),
        _col("Pd","MW",desc="real power demand"),
        _col("Qd","MVAr",desc="reactive power demand"),
        _col("Gs","MW",desc="shunt conductance (MW demanded at V = 1.0 pu)"),
        _col("Bs","MVAr",desc="shunt susceptance (MVAr injected at V = 1.0 pu)"),
        _col("area","","int",1,"area number"),
        _col("Vm","pu",default=1.0,desc="voltage magnitude"),
        _col("Va","deg",desc="voltage angle"),
        _col("baseKV","kV",desc="base voltage"),
        _col("zone","","int",1,"loss zone"),
        _col("Vmax","pu",default=1.1,desc="maximum voltage magnitude"),
        _col("Vmin","pu",default=0.9,desc="minimum voltage magnitude"),
    ],
    "gen": [
        _col("bus","","int",0,"bus number"),
        _col("Pg","MW",desc="real power output"),
        _col("Qg","MVAr",desc="reactive power output"),
        _col("Qmax","MVAr",default=300.0,desc="maximum reactive power output"),
        _col("Qmin","MVAr",default=-300.0,desc="minimum reactive power output"),
        _col("Vg","pu",default=1.0,desc="voltage magnitude setpoint"),
        _col("mBase","MVA",default=100.0,desc="machine MVA base"),
        _col("status","","enum",1,"machine status",GEN_STATUS),
        _col("Pmax","MW",default=100.0,desc="maximum real power output"),
        _col("Pmin","MW",desc="minimum real power output"),
        _col("Pc1","MW",desc="lower real power output of PQ capability curve"),
        _col("Pc2","MW",desc="upper real power output of PQ capability curve"),
        _col("Qc1min","MVAr",desc="minimum reactive power output at Pc1"),
        _col("Qc1max","MVAr",desc="maximum reactive power output at Pc1"),
        _col("Qc2min","MVAr",desc="minimum reactive power output at Pc2"),
        _col("Qc2max","MVAr",desc="maximum reactive power output at Pc2"),
        _col("ramp_agc","MW/min",desc="ramp rate for load following/AGC"),
        _col("ramp_10","MW",desc="ramp rate for 10 minute reserves"),
        _col("ramp_30","MW",desc="ramp rate for 30 minute reserves"),
        _col("ramp_q","MVAr/min",desc="ramp rate for reactive power"),
        _col("apf","",desc="area participation factor"),
    ],
    "branch": [
        _col("fbus","","int",0,"from bus number"),
        _col("tbus","","int",0,"to bus number"),
        _col("r","pu",desc="resistance"),
        _col("x","pu",default=0.1,desc="reactance"),
        _col("b","pu",desc="total line charging susceptance"),
        _col("rateA","MVA",desc="long term rating (0 = unlimited)"),
        _col("rateB","MVA",desc="short term rating (0 = unlimited)"),
        _col("rateC","MVA",desc="emergency rating (0 = unlimited)"),
        _col("ratio","",desc="transformer off-nominal turns ratio (0 = line)"),
        _col("angle","deg",desc="transformer phase shift angle"),
        _col("status","","enum",1,"branch status",BRANCH_STATUS),
        _col("angmin","deg",default=-360.0,desc="minimum angle difference"),
        _col("angmax","deg",default=360.0,desc="maximum angle difference"),
    ],
    "gencost": _cost_columns("MW of generator output"),
    "dcline": [
        _col("fbus","","int",0,"from bus number"),
        _col("tbus","","int",0,"to bus number"),
        _col("status","","enum",1,"DC line status",DCLINE_STATUS),
        _col("Pf","MW",desc="real power flow at the from bus (from -> to)"),
        _col("Pt","MW",desc="real power flow at the to bus (from -> to)"),
        _col("Qf","MVAr",desc="reactive power injection at the from bus"),
        _col("Qt","MVAr",desc="reactive power injection at the to bus"),
        _col("Vf","pu",default=1.0,desc="voltage setpoint at the from bus"),
        _col("Vt","pu",default=1.0,desc="voltage setpoint at the to bus"),
        _col("Pmin","MW",desc="minimum Pf (negative allows flow from to -> from)"),
        _col("Pmax","MW",default=100.0,desc="maximum Pf"),
        _col("QminF","MVAr",desc="minimum reactive power injection at the from bus"),
        _col("QmaxF","MVAr",desc="maximum reactive power injection at the from bus"),
        _col("QminT","MVAr",desc="minimum reactive power injection at the to bus"),
        _col("QmaxT","MVAr",desc="maximum reactive power injection at the to bus"),
        _col("loss0","MW",desc="constant term of the loss function (loss = loss0 + loss1*Pf)"),
        _col("loss1","MW/MW",desc="linear term of the loss function (loss = loss0 + loss1*Pf)"),
    ],
    "dclinecost": _cost_columns("MW of DC line flow Pf"),
}
"""Editable columns of each table (name, unit, kind, default, description)"""

TABLES = list(COLUMNS)
"""Names of the editable tables, in display order"""

COST_TABLES = {"gencost":"gen","dclinecost":"dcline"}
"""Cost tables and the table each one follows row by row"""

DCLINE_GLM = ["Vfrom","Vto","Pmin","Pmax","Qminf","Qmaxf","Qmint","Qmaxt","loss0","loss1"]
"""GLM `dcline` properties for the `dcline` columns `Vf` .. `loss1`"""

GLM_UNITS = {
    "bus": {"Pd":"MW","Qd":"MVAr","Gs":"MW","Bs":"MVAr","Vm":"pu.kV",
        "Va":"deg","baseKV":"kV","Vmax":"pu.kV","Vmin":"pu.kV"},
    "gen": {"Pg":"MW","Qg":"MVAr","Qmax":"MVAr","Qmin":"MVAr","Vg":"pu.kV",
        "mBase":"MVA","Pmax":"MW","Pmin":"MW","Pc1":"MW","Pc2":"MW",
        "Qc1min":"MVAr","Qc1max":"MVAr","Qc2min":"MVAr","Qc2max":"MVAr",
        "ramp_agc":"MW/min","ramp_10":"MW","ramp_30":"MW",
        "ramp_q":"MVAr/min","apf":"pu"},
    "branch": {"r":"pu.Ohm","x":"pu.Ohm","b":"pu.S","rateA":"MVA",
        "rateB":"MVA","rateC":"MVA","ratio":"pu","angle":"deg",
        "angmin":"deg","angmax":"deg"},
    "dcline": {"Vf":"pu.V","Vt":"pu.V","Pmin":"MW","Pmax":"MW",
        "QminF":"MVAr","QmaxF":"MVAr","QminT":"MVAr","QmaxT":"MVAr",
        "loss0":"MW","loss1":"MW/MW"},
}
"""Units written to GLM, matching `gridlabd convert -t pypower`"""

FORMATS = {".py":"py",".json":"json",".glm":"glm"}
"""File extensions and the format each is read and written in"""

GEODATA_COLUMNS = ["BUS_I","LAT","LON"]
"""Required geodata columns (CSV header names are matched ignoring case);
latitude and longitude are decimal degrees (WGS84)"""

class CaseError(ValueError):
    """Raised for invalid case data or an unreadable case file"""

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _number(value,kind="float"):
    """Convert a cell value to the column's kind; raise CaseError if invalid"""
    if isinstance(value,str):
        text = value.strip()
        if text == "":
            raise CaseError("value is empty")
        if text.lower() in ("inf","+inf","infinity"):
            value = math.inf
        elif text.lower() in ("-inf","-infinity"):
            value = -math.inf
        else:
            try:
                value = float(text)
            except ValueError as err:
                raise CaseError(f"'{value}' is not a number") from err
    if isinstance(value,bool):
        value = int(value)
    if not isinstance(value,(int,float)):
        raise CaseError(f"'{value}' is not a number")
    if kind in ("int","enum"):
        if not math.isfinite(value) or value != int(value):
            raise CaseError(f"'{value}' is not an integer")
        return int(value)
    return float(value)

def _costs_list(text):
    """Parse a comma-separated cost string into a list of floats"""
    if isinstance(text,(list,tuple)):
        return [float(x) for x in text]
    parts = [x for x in re.split(r"[,\s]+",str(text).strip()) if x]
    try:
        return [float(x) for x in parts]
    except ValueError as err:
        raise CaseError(f"costs '{text}' must be comma-separated numbers") from err

def _costs_text(values):
    return ",".join(f"{x:g}" for x in values)

def _fmt(value):
    """Format a number compactly for case files"""
    if isinstance(value,int):
        return str(value)
    if math.isinf(value):
        return "Inf" if value > 0 else "-Inf"
    return f"{value:.10g}"

# ---------------------------------------------------------------------------
# case
# ---------------------------------------------------------------------------

class Case:
    """Editable PYPOWER case

    Arguments
    ---------

      - `data`: a PYPOWER `ppc` dict (numpy arrays or lists), or `None` for
        an empty case

      - `name`: case name, used as the function name in `.py` output

    Each table is a list of rows; each row is a list ordered as
    `COLUMNS[table]`. `gencost` rows are `[model, startup, shutdown, ncost,
    costs]` where `costs` is a comma-separated string.
    """

    def __init__(self,data:dict|None=None,name:str="case"):
        self.name = name
        """Case name (a valid Python identifier)"""
        self.version = "2"
        """PYPOWER case format version"""
        self.baseMVA = 100.0
        """System MVA base"""
        self.tables = {x:[] for x in TABLES}
        """Rows of each table, ordered as `COLUMNS[table]`"""
        self.notes = []
        """Messages from loading (e.g. GLM objects that were not kept)"""
        self.geodata = []
        """Bus locations: rows of `[BUS_I, LAT, LON, aux...]` (decimal degrees)"""
        self.geotags = []
        """Names of the auxiliary geodata values (columns after `LON`)"""
        if data is not None:
            self._from_ppc(data)

    # ----- construction -----

    def _from_ppc(self,data):
        if not isinstance(data,dict):
            raise CaseError("case data must be a dict")
        for key in ("baseMVA","bus","gen","branch"):
            if key not in data:
                raise CaseError(f"case data is missing '{key}'")
        self.version = str(data.get("version","2"))
        self.baseMVA = _number(data["baseMVA"])
        for table in ("bus","gen","branch"):
            spec = COLUMNS[table]
            rows = []
            for n,row in enumerate(_tolist(data[table])):
                if len(row) < len(spec):
                    raise CaseError(f"{table} row {n+1} has {len(row)} columns, "
                        f"expected at least {len(spec)}")
                rows.append([_number(v,c["kind"]) for v,c in zip(row,spec)])
            self.tables[table] = rows
        spec = COLUMNS["dcline"]
        rows = []
        for n,row in enumerate(_tolist(data.get("dcline",[]))):
            if len(row) < len(spec):
                raise CaseError(f"dcline row {n+1} has {len(row)} columns, "
                    f"expected at least {len(spec)}")
            rows.append([_number(v,c["kind"]) for v,c in zip(row,spec)])
        self.tables["dcline"] = rows
        for table in COST_TABLES:
            self.tables[table] = _cost_rows(table,data.get(table,[]))
        self.geodata,self.geotags = _geo_from_ppc(data)

    @classmethod
    def builtin(cls,name:str) -> "Case":
        """Load a case shipped with the `pypower` package (e.g. `case14`)"""
        if name not in builtin_cases():
            raise CaseError(f"'{name}' is not a pypower built-in case")
        import pypower # pylint: disable=import-outside-toplevel
        path = os.path.join(os.path.dirname(pypower.__file__),f"{name}.py")
        return cls.load(path)

    @classmethod
    def load(cls,path:str,text:str|None=None) -> "Case":
        """Read a case file

        Arguments
        ---------

          - `path`: file name; the extension selects the format (`FORMATS`)

          - `text`: file contents (read from `path` if `None`)

        Returns
        -------

          - `Case`: the case; `Case.notes` lists anything not kept

        Exceptions
        ----------

          - `CaseError`: unknown extension or unreadable content
        """
        fmt = FORMATS.get(os.path.splitext(path)[1].lower())
        if fmt is None:
            raise CaseError(f"'{path}' has an unsupported extension "
                f"(use {', '.join(FORMATS)})")
        if text is None:
            with open(path,"r",encoding="utf-8") as fh:
                text = fh.read()
        name = re.sub(r"\W","_",os.path.splitext(os.path.basename(path))[0]) or "case"
        if name[0].isdigit():
            name = "case_" + name
        if fmt == "py":
            case = cls(_parse_py(text,name),name)
        elif fmt == "json":
            try:
                data = json.loads(text)
            except json.JSONDecodeError as err:
                raise CaseError(f"invalid JSON: {err}") from err
            case = cls(data,name)
        else:
            data,notes = _parse_glm(text)
            case = cls(data,name)
            case.notes = notes
        _logger.debug("loaded %s: %s",path,case.size())
        return case

    # ----- output -----

    def to_ppc(self,arrays:bool=False) -> dict:
        """Return the case as a PYPOWER `ppc` dict

        Arguments
        ---------

          - `arrays`: return numpy arrays (for the solver) instead of lists;
            geodata is left out

        `gencost`, `dcline` and `dclinecost` are included only when they
        have rows.
        """
        data = {"version":self.version,"baseMVA":self.baseMVA}
        for table in ("bus","gen","branch","gencost","dcline","dclinecost"):
            if table in COST_TABLES:
                if self.tables[table]:
                    costs = []
                    for model,startup,shutdown,ncost,text in self.tables[table]:
                        costs.append([model,startup,shutdown,ncost]+_costs_list(text))
                    width = max(len(x) for x in costs)
                    data[table] = [x+[0.0]*(width-len(x)) for x in costs]
            elif table in ("bus","gen","branch") or self.tables[table]:
                data[table] = [list(row) for row in self.tables[table]]
        if arrays:
            import numpy as np # pylint: disable=import-outside-toplevel
            for key in TABLES:
                if key in data:
                    width = len(data[key][0]) if key in COST_TABLES else len(COLUMNS[key])
                    data[key] = np.array(data[key],dtype=float).reshape(-1,width)
        elif self.geodata:
            data["geodata"] = [list(row) for row in self.geodata]
            data["geotags"] = list(self.geotags)
        return data

    def to_dict(self) -> dict:
        """Return the case for the browser editor (JSON-safe)"""
        return {
            "name": self.name,
            "version": self.version,
            "baseMVA": self.baseMVA,
            "tables": {t:[[_jsonsafe(v) for v in row] for row in rows]
                for t,rows in self.tables.items()},
            "notes": self.notes,
            "geodata": _jsonsafe(self.geodata),
            "geotags": list(self.geotags),
        }

    def dumps(self,fmt:str) -> str:
        """Return the case as text in format `fmt` (`py`, `json`, or `glm`)"""
        if fmt == "py":
            return self._dump_py()
        if fmt == "json":
            data = self.to_ppc()
            return json.dumps(_jsonsafe(data),indent=2) + "\n"
        if fmt == "glm":
            return self._dump_glm()
        if fmt == "csv":
            return self.dumps_geodata()
        raise CaseError(f"unknown format '{fmt}'")

    def save(self,path:str):
        """Write the case to `path`; the extension selects the format"""
        fmt = FORMATS.get(os.path.splitext(path)[1].lower())
        if fmt is None:
            raise CaseError(f"'{path}' has an unsupported extension")
        text = self.dumps(fmt)
        with open(path,"w",encoding="utf-8") as fh:
            fh.write(text)

    def _dump_py(self):
        out = io.StringIO()
        name = self.name if self.name.isidentifier() else "case"
        out.write(f'''"""PYPOWER case {name} (written by ppedit)"""

from numpy import array

def {name}():
    """Power flow data for {len(self.tables["bus"])} bus, {len(self.tables["gen"])} generator case.

    @return: PYPOWER case dict
    """
    ppc = {{"version": '{self.version}'}}

    ## system MVA base
    ppc["baseMVA"] = {_fmt(self.baseMVA)}
''')
        data = self.to_ppc()
        for table in ("bus","gen","branch","gencost","dcline","dclinecost"):
            if table not in data:
                continue
            header = " ".join(c["name"] for c in COLUMNS[table]) if table not in COST_TABLES \
                else "model startup shutdown ncost c(n-1) ... c0 (or x1 y1 ... xn yn)"
            out.write(f"\n    ## {table} data\n    # {header}\n")
            out.write(f'    ppc["{table}"] = array([\n')
            rows = [", ".join(_fmt(v) for v in row) for row in data[table]]
            out.write(",\n".join(f"        [{r}]" for r in rows))
            out.write("\n    ])\n")
        if self.geodata:
            out.write(f"\n    ## geodata (bus locations, decimal degrees)\n"
                f"    # {' '.join(GEODATA_COLUMNS+self.geotags)}\n    ppc[\"geodata\"] = [\n")
            rows = [", ".join(_geo_literal(v) for v in row) for row in self.geodata]
            out.write(",\n".join(f"        [{r}]" for r in rows))
            out.write(f"\n    ]\n    ppc[\"geotags\"] = {json.dumps(self.geotags)}\n")
        out.write("\n    return ppc\n")
        return out.getvalue()

    def _dump_glm(self):
        out = io.StringIO()
        out.write(f"// {self.name}: written by ppedit\n")
        out.write(f"module pypower\n{{\n    version {self.version};\n"
            f"    baseMVA {_fmt(self.baseMVA)};\n}}\n")
        out.write("\n//\n// bus\n//\n")
        where = self.geomap()
        for row in self.tables["bus"]:
            out.write(f'object pypower.bus\n{{\n    name "pp_bus_{row[0]}";\n')
            if row[0] in where:
                lat,lon = where[row[0]][1:3]
                out.write(f"    latitude {_fmt(float(lat))};\n    longitude {_fmt(float(lon))};\n")
            for col,val in zip(COLUMNS["bus"],row):
                out.write(f"    {col['name']} {_glm_value('bus',col,val)};\n")
            out.write("}\n")
        out.write("\n//\n// gen\n//\n")
        for n,row in enumerate(self.tables["gen"]):
            out.write(f'object pypower.gen\n{{\n    name "pp_gen_{n+1}";\n'
                f'    parent "pp_bus_{row[0]}";\n')
            for col,val in zip(COLUMNS["gen"],row):
                out.write(f"    {col['name']} {_glm_value('gen',col,val)};\n")
            out.write("}\n")
        out.write("\n//\n// branch\n//\n")
        for n,row in enumerate(self.tables["branch"]):
            out.write(f'object pypower.branch\n{{\n    name "pp_branch_{n+1}";\n'
                f'    from "pp_bus_{row[0]}";\n    to "pp_bus_{row[1]}";\n')
            for col,val in zip(COLUMNS["branch"],row):
                out.write(f"    {col['name']} {_glm_value('branch',col,val)};\n")
            out.write("}\n")
        if self.tables["gencost"]:
            out.write("\n//\n// gencost\n//\n")
            ngen = len(self.tables["gen"])
            for n,(model,startup,shutdown,_,costs) in enumerate(self.tables["gencost"]):
                out.write(f'object pypower.gencost\n{{\n    name "pp_gencost_{n+1}";\n'
                    f'    parent "pp_gen_{n%ngen+1 if ngen else n+1}";\n'
                    f'    model {COST_MODELS.get(model,"UNKNOWN")};\n'
                    f'    startup {_fmt(startup)} $;\n    shutdown {_fmt(shutdown)} $;\n'
                    f'    costs "{costs}";\n}}\n')
        if self.tables["dcline"]:
            out.write("\n//\n// dcline\n//\n")
            for n,row in enumerate(self.tables["dcline"]):
                out.write(f'object pypower.dcline\n{{\n    name "pp_dcline_{n+1}";\n'
                    f'    from "pp_bus_{row[0]}";\n    to "pp_bus_{row[1]}";\n'
                    f'    status {DCLINE_STATUS.get(row[2],row[2])};\n'
                    f'    Sfrom {_glm_complex(row[3],row[4])} MVA;\n'
                    f'    Sto {_glm_complex(row[5],row[6])} MVA;\n')
                for name,col,val in zip(DCLINE_GLM,COLUMNS["dcline"][7:],row[7:]):
                    out.write(f"    {name} {_glm_value('dcline',col,val)};\n")
                out.write("}\n")
        if self.tables["dclinecost"]:
            out.write(f"\n// {len(self.tables['dclinecost'])} dclinecost row(s) not written: "
                "module pypower has no DC line cost objects\n")
        return out.getvalue()

    # ----- editing -----

    def size(self) -> dict:
        """Return the number of rows in each table"""
        return {t:len(r) for t,r in self.tables.items()}

    def _check(self,table,row=None):
        if table not in COLUMNS:
            raise CaseError(f"unknown table '{table}'")
        if row is not None and not 0 <= row < len(self.tables[table]):
            raise CaseError(f"{table} row {row} does not exist")

    def set_global(self,key:str,value):
        """Set `baseMVA`, `version`, or `name`"""
        if key == "baseMVA":
            value = _number(value)
            if value <= 0:
                raise CaseError("baseMVA must be positive")
            self.baseMVA = value
        elif key == "version":
            self.version = str(value).strip() or "2"
        elif key == "name":
            value = re.sub(r"\W","_",str(value).strip())
            if not value or value[0].isdigit():
                raise CaseError("name must be a valid identifier")
            self.name = value
        else:
            raise CaseError(f"unknown case property '{key}'")

    def set_value(self,table:str,row:int,column:str,value) -> bool:
        """Set one cell

        Changing a bus number also renumbers the generators, branches, DC
        lines and geodata that refer to it. Changing a cost model or cost
        data (`gencost` or `dclinecost`) updates `ncost` to match.

        Returns
        -------

          - `bool`: `True` if other cells or tables changed as a result
        """
        self._check(table,row)
        names = [c["name"] for c in COLUMNS[table]]
        if column not in names:
            raise CaseError(f"{table} has no column '{column}'")
        k = names.index(column)
        spec = COLUMNS[table][k]
        record = self.tables[table][row]
        if spec["kind"] == "text":
            costs = _costs_list(value)
            record[k] = _costs_text(costs)
            model = record[0]
            record[3] = len(costs)//2 if model == 1 else len(costs)
            return True
        value = _number(value,spec["kind"])
        if spec["kind"] == "enum" and value not in spec["options"]:
            raise CaseError(f"{column} must be one of "
                + ", ".join(f"{a} ({b})" for a,b in spec["options"].items()))
        cascade = False
        if table == "bus" and column == "bus_i":
            if value < 0:
                raise CaseError("bus number must not be negative")
            old = record[0]
            if value != old and any(r[0] == value for r in self.tables["bus"]):
                raise CaseError(f"bus {value} already exists")
            for gen in self.tables["gen"]:
                if gen[0] == old:
                    gen[0] = value
                    cascade = True
            for table2 in ("branch","dcline"):
                for branch in self.tables[table2]:
                    for j in (0,1):
                        if branch[j] == old:
                            branch[j] = value
                            cascade = True
            for geo in self.geodata:
                if geo[0] == old and value != old:
                    geo[0] = value
                    cascade = True
        if table in ("gen","branch","dcline") and column in ("bus","fbus","tbus"):
            if not any(r[0] == value for r in self.tables["bus"]):
                raise CaseError(f"bus {value} does not exist")
        if table in COST_TABLES and column == "model" and value != record[0]:
            n = len(_costs_list(record[4]))
            record[3] = n//2 if value == 1 else n
            cascade = True
        record[k] = value
        return cascade

    def default_row(self,table:str) -> list:
        """Return a new row with default values for `table`"""
        self._check(table)
        row = [c["default"] for c in COLUMNS[table]]
        buses = self.tables["bus"]
        if table == "bus":
            row[0] = max((r[0] for r in buses),default=0) + 1
            if buses:
                row[6],row[9],row[10] = buses[-1][6],buses[-1][9],buses[-1][10]
        elif table == "gen":
            ref = [r[0] for r in buses if r[1] == 3]
            row[0] = ref[0] if ref else (buses[0][0] if buses else 0)
            row[6] = self.baseMVA
        elif table in ("branch","dcline"):
            row[0] = buses[0][0] if buses else 0
            row[1] = buses[1][0] if len(buses) > 1 else row[0]
        return row

    def add_row(self,table:str,values:list|dict|None=None,after:int|None=None) -> int:
        """Insert a row and return its index

        Arguments
        ---------

          - `table`: table name

          - `values`: a full row, a dict of column values to override the
            defaults, or `None` for defaults

          - `after`: insert after this row (default: append)

        Adding a generator (DC line) also adds a cost row (a copy of the last
        one, or zero polynomial costs) when the case has `gencost`
        (`dclinecost`) rows, so the cost table stays aligned with its table.
        """
        self._check(table)
        rows = self.tables[table]
        row = self.default_row(table)
        if isinstance(values,(list,tuple)):
            if len(values) != len(row):
                raise CaseError(f"{table} rows have {len(row)} columns")
            row = list(values)
        index = len(rows) if after is None else max(0,min(after+1,len(rows)))
        rows.insert(index,row)
        try:
            if isinstance(values,(list,tuple)):
                for col,val in zip(COLUMNS[table],values):
                    self.set_value(table,index,col["name"],val)
            elif isinstance(values,dict):
                for key,val in values.items():
                    self.set_value(table,index,key,val)
            if table == "bus" and sum(1 for r in rows if r[0] == rows[index][0]) > 1:
                raise CaseError(f"bus {rows[index][0]} already exists")
        except CaseError:
            del rows[index]
            raise
        cost = {v:k for k,v in COST_TABLES.items()}.get(table)
        if cost and self.tables[cost]:
            costs = self.tables[cost]
            ngen = len(rows) - 1
            template = list(costs[ngen-1]) if ngen and len(costs) >= ngen \
                else [2,0.0,0.0,3,"0,0,0"]
            costs.insert(index,template)
        return index

    def delete_rows(self,table:str,rows:list[int],cascade:bool=False) -> dict:
        """Delete rows

        Arguments
        ---------

          - `table`: table name

          - `rows`: row indexes to delete

          - `cascade`: when deleting buses, also delete the generators,
            branches and DC lines connected to them; otherwise refuse if any
            exist. Geodata rows of deleted buses are always removed.

        Deleting generators (DC lines) also deletes their `gencost`
        (`dclinecost`) rows.

        Returns
        -------

          - `dict`: number of rows deleted from each table
        """
        self._check(table)
        for row in rows:
            self._check(table,row)
        removed = {t:0 for t in TABLES}
        rows = sorted(set(rows),reverse=True)
        if table == "bus":
            numbers = {self.tables["bus"][r][0] for r in rows}
            gens = [n for n,g in enumerate(self.tables["gen"]) if g[0] in numbers]
            branches = [n for n,b in enumerate(self.tables["branch"])
                if b[0] in numbers or b[1] in numbers]
            dclines = [n for n,b in enumerate(self.tables["dcline"])
                if b[0] in numbers or b[1] in numbers]
            if (gens or branches or dclines) and not cascade:
                raise CaseError(f"bus(es) {sorted(numbers)} have {len(gens)} "
                    f"generator(s), {len(branches)} branch(es) and {len(dclines)} "
                    "DC line(s) connected")
            for sub,index in (("gen",gens),("branch",branches),("dcline",dclines)):
                for k,v in self.delete_rows(sub,index).items():
                    removed[k] += v
            self.geodata = [g for g in self.geodata if g[0] not in numbers]
        cost = {v:k for k,v in COST_TABLES.items()}.get(table)
        if cost and self.tables[cost]:
            ngen = len(self.tables[table])
            costs = self.tables[cost]
            drop = [r for r in rows if r < len(costs)]
            drop += [r+ngen for r in rows if r+ngen < len(costs)]
            for r in sorted(set(drop),reverse=True):
                del costs[r]
                removed[cost] += 1
        for r in rows:
            del self.tables[table][r]
            removed[table] += 1
        return removed

    # ----- geodata -----

    def set_geodata(self,rows:list,tags:list|None=None):
        """Replace the geodata

        Arguments
        ---------

          - `rows`: rows of `[BUS_I, LAT, LON, aux...]` (decimal degrees),
            or an empty list to remove the geodata

          - `tags`: names of the auxiliary values (default: none); rows are
            padded with `""` or truncated to match

        Exceptions
        ----------

          - `CaseError`: a row has fewer than 3 values, a bus number is not
            an integer, or a latitude/longitude is not a number in range
        """
        self.geodata,self.geotags = _geo_rows(rows,tags or [])

    def load_geodata(self,path:str,text:str|None=None) -> int:
        """Read geodata from a CSV file, replacing any existing geodata

        Arguments
        ---------

          - `path`: CSV file name

          - `text`: file contents (read from `path` if `None`)

        The header row must name the columns `BUS_I`, `LAT` and `LON` (in any
        order, ignoring case); other columns are kept as auxiliary data named
        by their headers (`Case.geotags`). Auxiliary values that read as
        numbers are stored as numbers, others as text.

        Returns
        -------

          - `int`: number of geodata rows read

        Exceptions
        ----------

          - `CaseError`: missing columns or invalid values
        """
        if text is None:
            with open(path,"r",encoding="utf-8-sig") as fh:
                text = fh.read()
        rows,tags = _read_geodata_csv(text)
        self.set_geodata(rows,tags)
        _logger.debug("geodata %s: %d rows, tags %s",path,len(rows),tags)
        return len(self.geodata)

    def dumps_geodata(self) -> str:
        """Return the geodata as CSV text (`BUS_I,LAT,LON` and the tags)"""
        out = io.StringIO()
        writer = csv.writer(out,lineterminator="\n")
        writer.writerow(GEODATA_COLUMNS+self.geotags)
        for row in self.geodata:
            writer.writerow([_fmt(v) if isinstance(v,(int,float)) else v for v in row])
        return out.getvalue()

    def geomap(self) -> dict:
        """Return `{bus_i: geodata row}` (the first row of each bus)"""
        where = {}
        for row in self.geodata:
            where.setdefault(row[0],row)
        return where

    def apply_result(self,result:dict):
        """Copy a solution's bus voltages, generator outputs and DC line flows into the case

        DC line `Pf`, `Pt`, `Qf` and `Qt` are copied for in-service lines.
        """
        if not result.get("success"):
            raise CaseError("cannot apply a failed solution")
        if len(result["bus"]) != len(self.tables["bus"]) \
                or len(result["gen"]) != len(self.tables["gen"]) \
                or len(result.get("dcline",[])) != len(self.tables["dcline"]):
            raise CaseError("solution does not match the case (it was edited)")
        for row,res in zip(self.tables["bus"],result["bus"]):
            row[7],row[8] = res["Vm"],res["Va"]
        for row,res in zip(self.tables["gen"],result["gen"]):
            row[1],row[2] = res["Pg"],res["Qg"]
        for row,res in zip(self.tables["dcline"],result.get("dcline",[])):
            if res["solved"]:
                row[3:7] = [res["Pf"],res["Pt"],res["Qf"],res["Qt"]]

    # ----- checking and solving -----

    def validate(self) -> list[dict]:
        """Check the case for errors and likely mistakes

        Returns
        -------

          - `list[dict]`: issues, each with `level` (`error` or `warning`),
            `table`, `row` (index or `None`), `column`, and `message`.
            An empty list means no issues were found.
        """
        issues = []
        def add(level,table,row,column,message):
            issues.append({"level":level,"table":table,"row":row,
                "column":column,"message":message})
        bus,gen,branch,dcline = (self.tables[t] for t in ("bus","gen","branch","dcline"))
        if not bus:
            add("error","bus",None,None,"case has no buses")
        numbers = {}
        for n,row in enumerate(bus):
            if row[0] < 0:
                add("error","bus",n,"bus_i","bus number must not be negative")
            elif row[0] == 0:
                add("warning","bus",n,"bus_i","bus number 0 is read as 'unset' by GridLAB-D module pypower")
            if row[0] in numbers:
                add("error","bus",n,"bus_i",f"bus {row[0]} duplicates row {numbers[row[0]]+1}")
            numbers.setdefault(row[0],n)
            if row[12] > row[11]:
                add("error","bus",n,"Vmin","Vmin exceeds Vmax")
            if row[7] <= 0:
                add("warning","bus",n,"Vm","voltage magnitude is not positive")
        refs = [n for n,r in enumerate(bus) if r[1] == 3]
        if bus and not refs:
            add("error","bus",None,"type","no reference (REF) bus")
        for n in refs[1:]:
            add("warning","bus",n,"type","more than one REF bus")
        online = {}
        for n,row in enumerate(gen):
            if row[0] not in numbers:
                add("error","gen",n,"bus",f"bus {row[0]} does not exist")
            if row[9] > row[8]:
                add("error","gen",n,"Pmin","Pmin exceeds Pmax")
            if row[4] > row[3]:
                add("error","gen",n,"Qmin","Qmin exceeds Qmax")
            if row[7] == 1:
                online.setdefault(row[0],[]).append(n)
        for n,row in enumerate(dcline):
            for j,col in ((0,"fbus"),(1,"tbus")):
                if row[j] not in numbers:
                    add("error","dcline",n,col,f"bus {row[j]} does not exist")
                elif row[2] == 1:
                    online.setdefault(row[j],[]).append(None) # a DC line terminal is a PV source
            if row[0] == row[1]:
                add("error","dcline",n,"tbus","DC line connects a bus to itself")
            if row[9] > row[10]:
                add("error","dcline",n,"Pmin","Pmin exceeds Pmax")
            if row[11] > row[12]:
                add("error","dcline",n,"QminF","QminF exceeds QmaxF")
            if row[13] > row[14]:
                add("error","dcline",n,"QminT","QminT exceeds QmaxT")
            if row[16] >= 1:
                add("error","dcline",n,"loss1","loss1 must be less than 1")
            elif row[16] < 0:
                add("warning","dcline",n,"loss1","loss1 is negative")
            for k,col in ((7,"Vf"),(8,"Vt")):
                if row[k] <= 0:
                    add("warning","dcline",n,col,"voltage setpoint is not positive")
            if row[2] == 1 and not row[9] <= row[3] <= row[10]:
                add("warning","dcline",n,"Pf",
                    "Pf is outside Pmin..Pmax (power flow uses Pf as given)")
        for n,row in enumerate(bus):
            if row[1] in (2,3) and row[0] not in online:
                add("warning","bus",n,"type",
                    f"{BUS_TYPES[row[1]]} bus {row[0]} has no in-service generator "
                    "(the solver will treat it as PQ)")
        connected = set()
        for n,row in enumerate(branch):
            for j,col in ((0,"fbus"),(1,"tbus")):
                if row[j] not in numbers:
                    add("error","branch",n,col,f"bus {row[j]} does not exist")
            if row[0] == row[1]:
                add("error","branch",n,"tbus","branch connects a bus to itself")
            if row[2] == 0 and row[3] == 0:
                add("error","branch",n,"x","branch has zero impedance")
            if row[5] < 0:
                add("error","branch",n,"rateA","rating is negative")
            if row[11] > row[12]:
                add("error","branch",n,"angmin","angmin exceeds angmax")
            if row[10] == 1:
                connected.update((row[0],row[1]))
        if len(bus) > 1:
            for n,row in enumerate(bus):
                if row[0] not in connected and row[1] != 4:
                    add("warning","bus",n,None,f"bus {row[0]} has no in-service branch")
        for table,owner in COST_TABLES.items():
            costs,count = self.tables[table],len(self.tables[owner])
            if not costs:
                continue
            if table == "gencost" and len(costs) not in (count,2*count):
                add("error",table,None,None,
                    f"{len(costs)} cost rows for {count} generators "
                    f"(need {count} or {2*count})")
            elif table == "dclinecost" and len(costs) != count:
                add("error",table,None,None,
                    f"{len(costs)} cost rows for {count} DC lines (need {count})")
            for n,(model,startup,shutdown,ncost,text) in enumerate(costs):
                values = _costs_list(text)
                need = 2*ncost if model == 1 else ncost
                if len(values) != need:
                    add("error",table,n,"costs",
                        f"{COST_MODELS.get(model,'?')} with ncost={ncost} needs "
                        f"{need} values, found {len(values)}")
                if model == 1:
                    xs = values[0::2]
                    if any(b < a for a,b in zip(xs,xs[1:])):
                        add("error",table,n,"costs","piecewise MW points must increase")
                if startup < 0 or shutdown < 0:
                    add("error",table,n,"startup","startup/shutdown cost is negative")
        if self.geodata:
            seen = {}
            for n,row in enumerate(self.geodata):
                if row[0] not in numbers:
                    add("warning","bus",None,None,
                        f"geodata row {n+1}: bus {row[0]} does not exist")
                elif row[0] in seen:
                    add("warning","bus",numbers[row[0]],None,
                        f"bus {row[0]} has more than one geodata row (row {seen[row[0]]+1} is used)")
                seen.setdefault(row[0],n)
            missing = [str(r[0]) for r in bus if r[0] not in seen]
            if missing:
                add("warning","bus",None,None,f"{len(missing)} bus(es) have no geodata: "
                    + ", ".join(missing[:10]) + (", ..." if len(missing) > 10 else ""))
        return issues

    def solve(self,kind:str="pf",algorithm:str="NR") -> dict:
        """Run a PYPOWER power flow (`pf`) or optimal power flow (`opf`)

        Arguments
        ---------

          - `kind`: `pf` or `opf` (OPF needs `gencost`)

          - `algorithm`: power flow method: `NR`, `FDXB`, `FDBX`, or `GS`

        Returns
        -------

          - `dict`: `success`, `kind`, `iterations` (PF), `objective`
            (OPF, $/h), `losses_MW`, `log` (solver text output), and the rows
            `bus` (`bus_i`, `Vm`, `Va`, `lam_P`), `gen` (`bus`, `Pg`, `Qg`),
            `branch` (`fbus`, `tbus`, `PF`, `QF`, `PT`, `QT`,
            `loading_pct` of `rateA`, or `None` when unrated), and `dcline`
            (`fbus`, `tbus`, `solved`, `Pf`, `Pt`, `Qf`, `Qt`, `Vf`, `Vt`,
            `loss_MW`, and for OPF the multipliers `mu_Pmin`, `mu_Pmax`
            in $/MWh). `losses_MW` includes the DC line losses, which are
            also given alone as `dcline_losses_MW`.

        Buses with no in-service branch are solved as isolated (type NONE)
        without changing the case; `log` names them. DC lines are modeled
        as in `pypower.toggle_dcline`: each in-service line is a pair of
        generators at its terminal buses, which become PV buses held at
        `Vf` and `Vt`; power flow keeps `Pf` and sets `Pt = Pf - (loss0 +
        loss1*Pf)`, and OPF dispatches `Pf` within `Pmin..Pmax` at the
        `dclinecost` cost (zero if there is none). DC lines that are out of
        service or touch an isolated bus are not solved (`solved` is
        false, flows 0).

        Exceptions
        ----------

          - `CaseError`: the case has validation errors, or OPF without costs
        """
        errors = [x for x in self.validate() if x["level"] == "error"]
        if errors:
            raise CaseError(f"case has {len(errors)} error(s); first: "
                f"{errors[0]['table']}: {errors[0]['message']}")
        from pypower.api import runpf, runopf, ppoption # pylint: disable=import-outside-toplevel,no-name-in-module
        algs = {"NR":1,"FDXB":2,"FDBX":3,"GS":4}
        if algorithm not in algs:
            raise CaseError(f"algorithm must be one of {', '.join(algs)}")
        if kind == "opf" and not self.tables["gencost"]:
            raise CaseError("OPF needs generator cost data (gencost)")
        if kind not in ("pf","opf"):
            raise CaseError("kind must be 'pf' or 'opf'")
        ppc = self.to_ppc(arrays=True)
        # buses with no in-service branch make the solver fail; solve them as isolated
        connected = {r[k] for r in self.tables["branch"] if r[10] == 1 and r[0] != r[1] for k in (0,1)}
        isolated = [r[0] for r in self.tables["bus"] if r[1] != 4 and r[0] not in connected] \
            if len(self.tables["bus"]) > 1 else []
        for n,row in enumerate(self.tables["bus"]):
            if row[0] in isolated:
                ppc["bus"][n,1] = 4
        opt = ppoption(PF_ALG=algs[algorithm],VERBOSE=0,OUT_ALL=0)
        log = io.StringIO()
        if isolated:
            log.write(f"Solved bus(es) {', '.join(str(x) for x in isolated)} as isolated "
                "(type NONE): no in-service branch\n")
        if "dcline" in ppc:
            from ppedit._dcline import add_dclines # pylint: disable=import-outside-toplevel
            dead = {r[0] for r in self.tables["bus"] if r[1] == 4} | set(isolated)
            cut = [n for n,r in enumerate(self.tables["dcline"])
                if r[2] == 1 and (r[0] in dead or r[1] in dead)]
            for n in cut:
                ppc["dcline"][n,2] = 0
            if cut:
                log.write(f"DC line(s) {', '.join(str(n+1) for n in cut)} not solved: "
                    "terminal bus is isolated\n")
            ppc = add_dclines(ppc)
        with contextlib.redirect_stdout(log), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                if kind == "pf":
                    res,success = runpf(ppc,opt)
                    res["success"] = bool(success)
                else:
                    res = runopf(ppc,opt)
            except Exception as err: # pylint: disable=broad-exception-caught
                _logger.debug("solver failed: %s",err)
                return {"success":False,"kind":kind,"log":log.getvalue()+f"\n{err}",
                    "bus":[],"gen":[],"branch":[],"dcline":[]}
        try:
            from pypower.printpf import printpf # pylint: disable=import-outside-toplevel
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                printpf(res,log,ppoption(opt,OUT_ALL=1)) # results form: (results, fd, ppopt)
        except Exception as err: # pylint: disable=broad-exception-caught
            log.write(f"(report unavailable: {err})\n")
        dclines = []
        for n,d in enumerate(res["dcline"] if "dcline" in res else []):
            solved = bool(d[2]) and bool(res["success"])
            dclines.append({"fbus":int(d[0]),"tbus":int(d[1]),"solved":solved,
                "Pf":float(d[3]),"Pt":float(d[4]),"Qf":float(d[5]),"Qt":float(d[6]),
                "Vf":float(d[7]),"Vt":float(d[8]),"loss_MW":float(d[3]-d[4]) if solved else 0.0,
                "mu_Pmin":float(d[17]) if kind == "opf" else None,
                "mu_Pmax":float(d[18]) if kind == "opf" else None})
        if dclines:
            log.write("\nDC lines\n   #   from     to      Pf MW      Pt MW    loss MW"
                "    Qf MVAr    Qt MVAr\n")
            for n,d in enumerate(dclines):
                log.write(f"{n+1:4d} {d['fbus']:6d} {d['tbus']:6d} " + (
                    "".join(f"{d[k]:11.2f}" for k in ("Pf","Pt","loss_MW","Qf","Qt"))
                    if d["solved"] else "   (not solved: out of service or isolated)") + "\n")
        bus,gen,branch = res["bus"],res["gen"],res["branch"]
        result = {
            "success": bool(res["success"]),
            "kind": kind,
            "algorithm": algorithm,
            "iterations": int(res["iterations"]) if "iterations" in res else None,
            "objective": float(res["f"]) if kind == "opf" and "f" in res else None,
            "log": log.getvalue(),
            "bus": [{"bus_i":int(b[0]),"Vm":float(b[7]),"Va":float(b[8]),
                "lam_P":float(b[13]) if kind == "opf" and len(b) > 13 else None}
                for b in bus],
            "gen": [{"bus":int(g[0]),"Pg":float(g[1]),"Qg":float(g[2])} for g in gen],
            "branch": [],
            "dcline": dclines,
        }
        losses = 0.0
        for b in branch:
            pf,qf,pt,qt = (float(x) for x in b[13:17])
            rate = float(b[5])
            smax = max(math.hypot(pf,qf),math.hypot(pt,qt))
            if b[10] == 1:
                losses += pf + pt
            result["branch"].append({"fbus":int(b[0]),"tbus":int(b[1]),
                "PF":pf,"QF":qf,"PT":pt,"QT":qt,
                "loading_pct":100*smax/rate if rate > 0 else None})
        result["dcline_losses_MW"] = sum(d["loss_MW"] for d in dclines)
        result["losses_MW"] = losses + result["dcline_losses_MW"]
        return _jsonsafe(result)

# ---------------------------------------------------------------------------
# module helpers
# ---------------------------------------------------------------------------

def builtin_cases() -> list[str]:
    """Return the names of the cases shipped with the `pypower` package"""
    try:
        import pypower # pylint: disable=import-outside-toplevel
    except ImportError:
        return []
    folder = os.path.dirname(pypower.__file__)
    skip = {"caseformat"}
    names = [os.path.splitext(x)[0] for x in os.listdir(folder)
        if re.fullmatch(r"case\w+\.py",x)]
    def order(name):
        m = re.match(r"case(\d+)",name)
        return (int(m.group(1)) if m else 99999,name)
    return sorted((x for x in names if x not in skip),key=order)

def _cost_rows(table,value):
    """Read `gencost`/`dclinecost` matrix rows as `[model, startup, shutdown, ncost, costs]`"""
    rows = []
    for n,row in enumerate(_tolist(value)):
        if len(row) < 4:
            raise CaseError(f"{table} row {n+1} has fewer than 4 columns")
        model,startup,shutdown,ncost = (_number(row[0],"int"),
            _number(row[1]),_number(row[2]),_number(row[3],"int"))
        costs = [float(x) for x in row[4:]]
        need = 2*ncost if model == 1 else ncost
        costs = costs[:need] if len(costs) >= need else costs
        rows.append([model,startup,shutdown,ncost,_costs_text(costs)])
    return rows

def _tolist(value):
    if hasattr(value,"tolist"):
        value = value.tolist()
    if value and not isinstance(value[0],(list,tuple)):
        value = [value]
    return [list(x) for x in value]

def _jsonsafe(value):
    """Replace infinities and NaN (not valid JSON) with strings"""
    if isinstance(value,float):
        if math.isinf(value):
            return "Inf" if value > 0 else "-Inf"
        if math.isnan(value):
            return None
        return value
    if isinstance(value,dict):
        return {k:_jsonsafe(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):
        return [_jsonsafe(v) for v in value]
    return value

# ----- geodata helpers -----

def _geo_value(value):
    """Return an auxiliary geodata value as a number if it reads as one, else text"""
    if isinstance(value,bool):
        return int(value)
    if isinstance(value,(int,float)):
        return value if math.isfinite(value) else str(value)
    text = "" if value is None else str(value).strip()
    if re.fullmatch(r"[+-]?\d+",text):
        return int(text)
    try:
        number = float(text)
    except ValueError:
        return text
    return number if math.isfinite(number) else text

def _geo_rows(rows,tags):
    """Check and normalize geodata rows; return `(rows, tags)`"""
    if hasattr(tags,"tolist"):
        tags = tags.tolist()
    if isinstance(tags,str):
        tags = [tags]
    tags = [str(x) for x in tags]
    rows = _tolist(rows) if len(rows) else []
    width = max((len(r) for r in rows),default=3) - 3
    tags += [f"col{k+4}" for k in range(len(tags),width)]
    out = []
    for n,row in enumerate(rows):
        if len(row) < 3:
            raise CaseError(f"geodata row {n+1} has {len(row)} values, "
                "expected at least 3 (BUS_I, LAT, LON)")
        try:
            bus,lat,lon = _number(row[0],"int"),_number(row[1]),_number(row[2])
        except CaseError as err:
            raise CaseError(f"geodata row {n+1}: {err}") from err
        if not -90 <= lat <= 90:
            raise CaseError(f"geodata row {n+1}: latitude {lat} is not in -90..90")
        if not -180 <= lon <= 180:
            raise CaseError(f"geodata row {n+1}: longitude {lon} is not in -180..180")
        aux = [_geo_value(v) for v in row[3:3+len(tags)]]
        out.append([bus,lat,lon]+aux+[""]*(len(tags)-len(aux)))
    return out,tags

def _geo_from_ppc(data):
    """Read `geodata`/`geotags` from a case dict

    `geodata` is a list of rows (named by `geotags`) or a dict of columns
    that includes `BUS_I`, `LAT` and `LON`.
    """
    geo = data.get("geodata")
    if geo is None:
        return [],[]
    if isinstance(geo,dict):
        keys = {str(k).upper():k for k in geo}
        missing = [c for c in GEODATA_COLUMNS if c not in keys]
        if missing:
            raise CaseError(f"geodata is missing column(s) {', '.join(missing)}")
        tags = [k for k in geo if str(k).upper() not in GEODATA_COLUMNS]
        cols = [geo[keys[c]] for c in GEODATA_COLUMNS] + [geo[k] for k in tags]
        cols = [x.tolist() if hasattr(x,"tolist") else list(x) for x in cols]
        if len({len(x) for x in cols}) > 1:
            raise CaseError("geodata columns have different lengths")
        return _geo_rows([list(r) for r in zip(*cols)],tags)
    if not isinstance(geo,(list,tuple)) and not hasattr(geo,"tolist"):
        raise CaseError("geodata must be a list of rows or a dict of columns")
    return _geo_rows(geo,data.get("geotags") or [])

def _read_geodata_csv(text):
    """Parse geodata CSV text; return `(rows, tags)` before checking"""
    lines = [r for r in csv.reader(io.StringIO(text.lstrip("\ufeff"))) if any(c.strip() for c in r)]
    if not lines:
        raise CaseError("geodata CSV is empty")
    header = [x.strip() for x in lines[0]]
    upper = [x.upper() for x in header]
    missing = [c for c in GEODATA_COLUMNS if c not in upper]
    if missing:
        raise CaseError(f"geodata CSV is missing column(s) {', '.join(missing)} "
            f"(found {', '.join(header)})")
    index = [upper.index(c) for c in GEODATA_COLUMNS]
    aux = [k for k,x in enumerate(upper) if x not in GEODATA_COLUMNS]
    rows = []
    for line in lines[1:]:
        line = line + [""]*(len(header)-len(line))
        rows.append([line[k] for k in index] + [line[k] for k in aux])
    return rows,[header[k] for k in aux]

def _geo_literal(value):
    """Format a geodata value as a Python literal"""
    return _fmt(value) if isinstance(value,(int,float)) else json.dumps(value)

_LATLON = re.compile(r"^\s*(\d+(?:\.\d*)?)\s*([NSEW])\s*(\d+(?:\.\d*)?)(?:\s*:\s*(\d+(?:\.\d*)?))?\s*$",re.I)

def _glm_angle(text,hemispheres):
    """Read a GLM latitude/longitude: decimal degrees or `37N25:12.3` style"""
    text = _unquote(text)
    m = _LATLON.match(text)
    if m and m.group(2).upper() in hemispheres:
        value = float(m.group(1)) + float(m.group(3))/60 + float(m.group(4) or 0)/3600
        return -value if m.group(2).upper() == hemispheres[1] else value
    return _number(text)

def _glm_complex(real,imag):
    """Format two numbers as a GLM complex value `a+bj`"""
    sign = "-" if imag < 0 or (imag == 0 and math.copysign(1,imag) < 0) else "+"
    return f"{_fmt(real)}{sign}{_fmt(abs(imag))}j"

_COMPLEX = re.compile(r"^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)"
    r"(?:\s*([+-]\s*(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*([ijdr]))?")

def _glm_complex_value(text):
    """Read a GLM complex value (`a+bj`, `a+bi`, polar `a+bd`/`a+br`, or real) as `(real, imag)`"""
    text = _unquote(text)
    m = _COMPLEX.match(text)
    if not m:
        raise CaseError(f"'{text}' is not a complex number")
    a = float(m.group(1))
    if m.group(2) is None:
        return a,0.0
    b = float(m.group(2).replace(" ",""))
    if m.group(3) in "ij":
        return a,b
    angle = math.radians(b) if m.group(3) == "d" else b
    return a*math.cos(angle),a*math.sin(angle)

def _glm_value(table,col,val):
    if col["kind"] == "enum":
        return col["options"].get(val,str(val))
    unit = GLM_UNITS.get(table,{}).get(col["name"])
    text = _fmt(val)
    if math.isinf(val) if isinstance(val,float) else False:
        text = "+INF" if val > 0 else "-INF"
    return f"{text} {unit}" if unit else text

# ----- PYPOWER .py parser (no code execution) -----

_NAMES = {"inf":math.inf,"Inf":math.inf,"INF":math.inf,"nan":math.nan,
    "pi":math.pi,"True":1,"False":0}

def _eval(node,env):
    """Evaluate a literal-only expression tree"""
    if isinstance(node,ast.Constant) and isinstance(node.value,(int,float,str)):
        return node.value
    if isinstance(node,(ast.List,ast.Tuple)):
        return [_eval(x,env) for x in node.elts]
    if isinstance(node,ast.Dict):
        return {_eval(k,env):_eval(v,env) for k,v in zip(node.keys,node.values)}
    if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.USub,ast.UAdd)):
        val = _eval(node.operand,env)
        return -val if isinstance(node.op,ast.USub) else val
    if isinstance(node,ast.BinOp) and isinstance(node.op,(ast.Add,ast.Sub,ast.Mult,ast.Div,ast.Pow)):
        a,b = _eval(node.left,env),_eval(node.right,env)
        if not all(isinstance(x,(int,float)) for x in (a,b)):
            raise CaseError("arithmetic on non-numbers")
        ops = {ast.Add:lambda:a+b,ast.Sub:lambda:a-b,ast.Mult:lambda:a*b,
            ast.Div:lambda:a/b,ast.Pow:lambda:a**b if abs(b) < 64 else math.nan}
        return ops[type(node.op)]()
    if isinstance(node,ast.Name):
        if node.id in env:
            return env[node.id]
        if node.id in _NAMES:
            return _NAMES[node.id]
    if isinstance(node,ast.Attribute) and isinstance(node.value,ast.Name) \
            and node.value.id in ("np","numpy","math") and node.attr in _NAMES:
        return _NAMES[node.attr]
    if isinstance(node,ast.Call):
        func = node.func.attr if isinstance(node.func,ast.Attribute) else \
            node.func.id if isinstance(node.func,ast.Name) else None
        if func in ("array","matrix","asarray") and node.args:
            return _eval(node.args[0],env)
        if func in ("float","int") and len(node.args) == 1:
            val = _eval(node.args[0],env)
            return float(val) if func == "float" else int(val)
    raise CaseError(f"unsupported expression at line {getattr(node,'lineno','?')}")

def _is_case(data):
    """Check that a dict has the required PYPOWER case keys"""
    return all(k in data for k in ("baseMVA","bus","gen","branch"))

def _parse_py(text,name):
    """Extract the ppc dict from a PYPOWER case file without running it

    The case is either assigned to a variable (`ppc = {...}`, optionally
    followed by `ppc[key] = ...`) or returned as a dict literal
    (`return {...}`).
    """
    try:
        tree = ast.parse(text)
    except SyntaxError as err:
        raise CaseError(f"invalid Python: {err}") from err
    funcs = [x for x in tree.body if isinstance(x,ast.FunctionDef)]
    bodies = [f.body for f in funcs if f.name == name] + [f.body for f in funcs] + [tree.body]
    for body in bodies:
        env,data = {},None
        target = None
        for stmt in body:
            if isinstance(stmt,ast.Assign) and len(stmt.targets) == 1:
                tgt = stmt.targets[0]
                try:
                    if isinstance(tgt,ast.Name):
                        value = _eval(stmt.value,env)
                        env[tgt.id] = value
                        if isinstance(value,dict) and target is None:
                            target,data = tgt.id,value
                    elif isinstance(tgt,ast.Subscript) and isinstance(tgt.value,ast.Name) \
                            and tgt.value.id == target:
                        key = _eval(tgt.slice,env)
                        data[key] = _eval(stmt.value,env) # pylint: disable=unsupported-assignment-operation
                except CaseError as err:
                    _logger.debug("skipping statement at line %s: %s",stmt.lineno,err)
            elif isinstance(stmt,ast.Return) and stmt.value is not None:
                try:
                    value = _eval(stmt.value,env)
                except CaseError as err:
                    _logger.debug("skipping statement at line %s: %s",stmt.lineno,err)
                    continue
                if isinstance(value,dict) and _is_case(value):
                    return value
        if data and _is_case(data):
            return data
    raise CaseError("no PYPOWER case (a dict with baseMVA, bus, gen, branch) found")

# ----- GLM parser -----

_SOLVER_CLASSES = {"bus","gen","branch","gencost","dcline"}

def _strip_comments(text):
    out = []
    for line in text.splitlines():
        quoted = False
        for i,ch in enumerate(line):
            if ch == '"':
                quoted = not quoted
            elif ch == "/" and not quoted and line[i:i+2] == "//":
                line = line[:i]
                break
        out.append(line)
    return "\n".join(out)

def _glm_blocks(text):
    """Yield (header, body) for each top-level `{...}` block"""
    i,n = 0,len(text)
    while i < n:
        j = text.find("{",i)
        if j < 0:
            return
        header = text[i:j].strip().splitlines()
        header = header[-1].strip() if header else ""
        depth,k,quoted = 0,j,False
        while k < n:
            ch = text[k]
            if ch == '"':
                quoted = not quoted
            elif not quoted and ch == "{":
                depth += 1
            elif not quoted and ch == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        yield header,text[j+1:k]
        i = k+1

def _glm_props(body):
    """Parse `name value;` lines at the top level of an object body"""
    props,depth,buf,quoted = {},0,"",False
    for ch in body:
        if ch == '"':
            quoted = not quoted
        if not quoted and ch == "{":
            depth += 1
        elif not quoted and ch == "}":
            depth -= 1
            buf = ""
            continue
        if depth > 0:
            continue
        if ch == ";" and not quoted:
            part = buf.strip()
            buf = ""
            if part:
                bits = part.split(None,1)
                props[bits[0]] = bits[1].strip() if len(bits) > 1 else ""
        else:
            buf += ch
    return props

def _unquote(text):
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1]
    return text

def _glm_number(text,kind="float",options=None):
    text = _unquote(text)
    if options:
        lookup = {v.upper():k for k,v in options.items()}
        if text.upper() in lookup:
            return lookup[text.upper()]
        if text.upper() == "PQREF":
            return 1
    m = re.match(r"^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?|[+-]?INF)",text,re.I)
    if not m:
        raise CaseError(f"'{text}' is not a number")
    return _number(m.group(1),kind)

def _parse_glm(text):
    """Extract a ppc dict from an Arras Energy GLM model"""
    text = _strip_comments(text)
    directives = sum(1 for line in text.splitlines() if line.strip().startswith("#"))
    text = "\n".join("" if line.strip().startswith("#") else line for line in text.splitlines())
    data = {"version":"2","baseMVA":100.0,"bus":[],"gen":[],"branch":[],"gencost":[],"dcline":[]}
    geodata = []
    names = {"bus":{},"gen":{}}
    objects = {t:[] for t in _SOLVER_CLASSES}
    others = {}
    module = False
    for header,body in _glm_blocks(text):
        words = header.split()
        if len(words) >= 2 and words[0] == "module" and words[1] == "pypower":
            module = True
            props = _glm_props(body)
            if "baseMVA" in props:
                data["baseMVA"] = _glm_number(props["baseMVA"])
            if "version" in props:
                data["version"] = _unquote(props["version"])
            extra = sorted(set(props) - {"baseMVA","version"})
            if extra:
                others["module pypower setting"] = len(extra)
        elif words and words[0] in ("module","clock","class","schedule","filter","global"):
            key = " ".join(words[:2]) if words[0] == "module" else words[0]
            others[key] = others.get(key,0) + 1
        elif len(words) >= 2 and words[0] == "object":
            for nested in re.findall(r"\bobject\s+([\w.]+)",body):
                nested = nested[8:] if nested.startswith("pypower.") else nested
                others[nested] = others.get(nested,0) + 1
            oclass = words[1].split(":")[0]
            if oclass.startswith("pypower."):
                oclass = oclass[8:]
            if oclass in _SOLVER_CLASSES:
                props = _glm_props(body)
                if ":" in words[1] and "name" not in props:
                    props["name"] = words[1]
                objects[oclass].append(props)
            else:
                others[oclass] = others.get(oclass,0) + 1
    if not module and not any(objects.values()):
        raise CaseError("no 'module pypower' objects found in GLM")
    # buses
    for n,props in enumerate(objects["bus"]):
        row = []
        for col in COLUMNS["bus"]:
            try:
                row.append(_glm_number(props[col["name"]],col["kind"],col["options"])
                    if col["name"] in props else col["default"])
            except CaseError as err:
                raise CaseError(f"bus object {n+1} {col['name']}: {err}") from err
        if "bus_i" not in props:
            row[0] = n+1
        names["bus"][_unquote(props.get("name",f"pp_bus_{n+1}"))] = row[0]
        data["bus"].append(row)
        if "latitude" in props and "longitude" in props:
            try:
                geodata.append([row[0],_glm_angle(props["latitude"],"NS"),
                    _glm_angle(props["longitude"],"EW")])
            except CaseError as err:
                raise CaseError(f"bus object {n+1} location: {err}") from err
    # generators
    for n,props in enumerate(objects["gen"]):
        row = []
        for col in COLUMNS["gen"]:
            row.append(_glm_number(props[col["name"]],col["kind"],col["options"])
                if col["name"] in props else col["default"])
        if "bus" not in props and "parent" in props:
            parent = _unquote(props["parent"])
            if parent not in names["bus"]:
                raise CaseError(f"gen object {n+1} parent '{parent}' is not a bus")
            row[0] = names["bus"][parent]
        names["gen"][_unquote(props.get("name",f"pp_gen_{n+1}"))] = n
        data["gen"].append(row)
    # branches
    for n,props in enumerate(objects["branch"]):
        row = []
        for col in COLUMNS["branch"]:
            row.append(_glm_number(props[col["name"]],col["kind"],col["options"])
                if col["name"] in props else col["default"])
        for j,ref in ((0,"from"),(1,"to")):
            if ("fbus","tbus")[j] not in props and ref in props:
                bus = _unquote(props[ref])
                if bus not in names["bus"]:
                    raise CaseError(f"branch object {n+1} {ref} '{bus}' is not a bus")
                row[j] = names["bus"][bus]
        data["branch"].append(row)
    # costs, ordered by parent generator
    costs,seen = [],{}
    for n,props in enumerate(objects["gencost"]):
        parent = _unquote(props.get("parent",""))
        index = names["gen"].get(parent,n)
        seen[index] = seen.get(index,-1) + 1 # second cost per gen is the Q cost
        model = _glm_number(props.get("model","POLYNOMIAL"),"enum",COST_MODELS)
        values = _costs_list(_unquote(props.get("costs","")))
        ncost = len(values)//2 if model == 1 else len(values)
        costs.append((seen[index],index,n,[model,_glm_number(props.get("startup","0")),
            _glm_number(props.get("shutdown","0")),ncost]+values))
    data["gencost"] = [x[3] for x in sorted(costs)]
    # DC lines (Sfrom = Pf + j Pt and Sto = Qf + j Qt, as GridLAB-D passes them to PYPOWER)
    spec = COLUMNS["dcline"]
    for n,props in enumerate(objects["dcline"]):
        try:
            row = [0,0,_glm_number(props.get("status","IN"),"enum",DCLINE_STATUS)]
            for j,(ref,num) in enumerate((("from","fbus"),("to","tbus"))):
                if ref in props:
                    bus = _unquote(props[ref])
                    if bus not in names["bus"]:
                        raise CaseError(f"{ref} '{bus}' is not a bus")
                    row[j] = names["bus"][bus]
                elif num in props:
                    row[j] = _glm_number(props[num],"int")
                else:
                    raise CaseError(f"'{ref}' is missing")
            row += list(_glm_complex_value(props.get("Sfrom","0")))
            row += list(_glm_complex_value(props.get("Sto","0")))
            row += [_glm_number(props[name]) if name in props else col["default"]
                for name,col in zip(DCLINE_GLM,spec[7:])]
        except CaseError as err:
            raise CaseError(f"dcline object {n+1}: {err}") from err
        data["dcline"].append(row)
    if geodata:
        data["geodata"] = geodata
    notes = []
    if directives:
        others["# directive"] = directives
    if others:
        notes.append("not kept (only module pypower bus, gen, branch, gencost and dcline data "
            "are edited): "
            + ", ".join(f"{v} {k}" for k,v in sorted(others.items())))
    return data,notes
