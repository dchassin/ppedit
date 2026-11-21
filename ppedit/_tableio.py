"""Import and export of case table rows as data files (issue #73)

Files are read and written with pandas, which is used here only: the case
model stays plain lists of rows (decision #4). The format is chosen from the
file name extension (`table_format`), for example `bus.csv`, `bus.csv.gz`,
`gen.parquet` or `branch.xlsx`. Formats whose pandas engine is not installed
(`pyarrow` for Parquet and Feather, `openpyxl` for Excel) are not offered by
`table_formats` and are refused by `read_rows` and `dump_rows`.

Import also reads the other pandas formats (`table_read_formats`) when their
engine is installed: JSON lines (`.jsonl`, `.ndjson`), XML, HTML (the first
table; `lxml`, or `bs4` and `html5lib`), fixed-width text (`.fwf`), Excel
`.xlsm`, `.xls`, `.xlsb` and OpenDocument `.ods` (`python-calamine`, else
`openpyxl`, `xlrd`, `pyxlsb` and `odfpy` respectively),
ORC (`pyarrow`), HDF5 (`.h5`, `.hdf5`, `.hdf`; `tables`; one data set),
Stata `.dta`, SAS `.sas7bdat` and `.xpt`, and SPSS `.sav` (`pyreadstat`).
These are not written. Pickle files are refused: loading one can run
arbitrary code.

Files have one column per table column, headed by its name in
`ppedit.case.COLUMNS` (no index column); `Case.import_rows` checks the names.

Time series files (`read_series`, `dump_series`; issue #124) have one row
per time step: an optional label column (named `time`, `timestamp`,
`datetime`, `date`, `step` or `index`, any case, or an unnamed first column
as pandas writes an index) and one column per case value named
`table.column.key` (see `ppedit.Case.set_series`), e.g. `bus.Pd.5`.
"""

import importlib.util
import io
import math
import os
import re
import tempfile

from ppedit.case import COLUMNS,CaseError

# extension -> (pandas format, modules needed besides pandas, writable).
# Modules: None, a name, "a|b" (either) or "a+b" (both). Pickle is not read:
# loading one can run arbitrary code (as `.py` cases are never executed).
_FORMATS = {
    ".csv":("csv",None,True),
    ".tsv":("tsv",None,True),
    ".json":("json",None,True),
    ".parquet":("parquet","pyarrow",True),
    ".feather":("feather","pyarrow",True),
    ".xlsx":("excel","openpyxl",True),
    ".jsonl":("jsonl",None,False),
    ".ndjson":("jsonl",None,False),
    ".xml":("xml",None,False),
    ".html":("html","lxml|bs4+html5lib",False),
    ".htm":("html","lxml|bs4+html5lib",False),
    ".fwf":("fwf",None,False),
    ".xlsm":("excel","python_calamine|openpyxl",False),
    ".xls":("excel","python_calamine|xlrd",False),
    ".xlsb":("excel","python_calamine|pyxlsb",False),
    ".ods":("excel","python_calamine|odf",False),
    ".orc":("orc","pyarrow",False),
    ".h5":("hdf","tables",False),
    ".hdf5":("hdf","tables",False),
    ".hdf":("hdf","tables",False),
    ".dta":("stata",None,False),
    ".sas7bdat":("sas",None,False),
    ".xpt":("sas",None,False),
    ".sav":("spss","pyreadstat",False),
}

# Excel-like extension -> pandas engine when `python-calamine` (pandas'
# preferred reader for all of them; xlrd is deprecated) is not installed
_EXCEL_ENGINES = {".xlsx":"openpyxl",".xlsm":"openpyxl",".xls":"xlrd",".xlsb":"pyxlsb",".ods":"odf"}

# compression suffix -> (pandas compression, module needed); text formats only
_COMPRESSION = {
    ".gz":("gzip",None),
    ".bz2":("bz2",None),
    ".xz":("xz",None),
    ".zip":("zip",None),
    ".zst":("zstd","zstandard"),
}

_TEXT = ("csv","tsv","json","jsonl","xml","fwf")

def _installed(module:str|None) -> bool:
    if module is None:
        return True
    return any(all(importlib.util.find_spec(m) is not None for m in alt.split("+"))
        for alt in module.split("|"))

def _formats(write:bool) -> list[str]:
    if not _installed("pandas"):
        return []
    out = []
    for ext,(fmt,module,writable) in _FORMATS.items():
        if (write and not writable) or not _installed(module):
            continue
        out.append(ext)
        if fmt in _TEXT:
            out.extend(ext+suffix for suffix,(_,mod) in _COMPRESSION.items() if _installed(mod))
    return out

def table_formats() -> list[str]:
    """Return the file extensions that can be read and written

    Returns
    -------

      - `list[str]`: extensions such as `.csv`, `.csv.gz` and `.parquet`
        whose pandas engine is installed; empty if pandas is not installed
    """
    return _formats(True)

def table_read_formats() -> list[str]:
    """Return the file extensions that can be read (imported)

    Returns
    -------

      - `list[str]`: `table_formats` plus the formats pandas reads but
        ppedit does not write (`.jsonl`, `.xml`, `.html`, `.fwf`, `.xls`,
        `.xlsm`, `.xlsb`, `.ods`, `.orc`, `.h5`, `.dta`, `.sas7bdat`,
        `.xpt`, `.sav`, ...), each only when its engine is installed
    """
    return _formats(False)

def table_format(filename:str,write:bool=False) -> tuple[str,str|None]:
    """Return the pandas format and compression of a file name

    Arguments
    ---------

      - `filename`: file name or path, e.g. `bus.csv.gz`

      - `write`: refuse formats that are only read

    Returns
    -------

      - `tuple[str,str|None]`: format (`csv`, `tsv`, `json`, `jsonl`,
        `xml`, `html`, `fwf`, `parquet`, `feather`, `orc`, `excel`, `hdf`,
        `stata`, `sas` or `spss`) and compression (`gzip`, `bz2`, `xz`,
        `zip`, `zstd` or `None`)

    Exceptions
    ----------

      - `CaseError`: the extension is not supported, its pandas engine is
        not installed, or (with `write`) it is only read
    """
    name = filename.lower()
    if name.endswith((".pkl",".pickle")):
        raise CaseError(f"'{filename}': pickle files are not read, since loading one can "
            "run arbitrary code; save the data as Parquet or CSV instead")
    compression = None
    for suffix,(comp,module) in _COMPRESSION.items():
        if name.endswith(suffix):
            if not _installed(module):
                raise CaseError(f"'{suffix}' files need the '{module}' package")
            name,compression = name[:-len(suffix)],comp
            break
    for ext,(fmt,module,writable) in _FORMATS.items():
        if name.endswith(ext):
            if compression and fmt not in _TEXT:
                raise CaseError(f"'{ext}' files cannot be compressed")
            if write and not writable:
                raise CaseError(f"'{ext}' files can be imported but not exported "
                    "(use " + ", ".join(table_formats() or [".csv"]) + ")")
            if not _installed("pandas"):
                raise CaseError("importing and exporting table data needs the 'pandas' package")
            if not _installed(module):
                raise CaseError(f"'{ext}' files need the "
                    + " or ".join("'" + "' and '".join(a.split("+")) + "'" for a in module.split("|"))
                    + " package" + ("s" if "+" in module or "|" in module else ""))
            return fmt,compression
    raise CaseError(f"'{filename}' is not a supported table data file "
        "(use " + ", ".join((table_read_formats() if not write else table_formats()) or [".csv"]) + ")")

def _read_frame(filename:str,data:bytes,fmt:str,compression:str|None):
    """Read file contents into a pandas data frame"""
    import pandas as pd # pylint: disable=import-outside-toplevel
    buffer = io.BytesIO(data)
    ext = next(e for e in sorted(_FORMATS,key=len,reverse=True)
        if re.sub(r"\.(gz|bz2|xz|zip|zst)$","",filename.lower()).endswith(e))
    if fmt in ("csv","tsv"):
        return pd.read_csv(buffer,sep="," if fmt == "csv" else "\t",
            compression=compression,skipinitialspace=True)
    if fmt in ("json","jsonl"):
        return pd.read_json(buffer,orient="records",compression=compression,
            precise_float=True,lines=fmt == "jsonl")
    if fmt == "xml":
        return pd.read_xml(buffer,compression=compression,
            parser="lxml" if _installed("lxml") else "etree")
    if fmt == "fwf":
        return pd.read_fwf(buffer,compression=compression)
    if fmt == "html":
        tables = pd.read_html(io.StringIO(data.decode("utf-8","replace")),
            flavor="lxml" if _installed("lxml") else "bs4")
        return tables[0]
    if fmt == "parquet":
        return pd.read_parquet(buffer)
    if fmt == "feather":
        return pd.read_feather(buffer)
    if fmt == "orc":
        return pd.read_orc(buffer)
    if fmt == "excel":
        engine = _EXCEL_ENGINES[ext]
        if ext != ".xlsx" and _installed("python_calamine"):
            engine = "calamine"
        return pd.read_excel(buffer,engine=engine)
    if fmt == "stata":
        return pd.read_stata(buffer)
    if fmt == "sas":
        return pd.read_sas(buffer,format="xport" if ext == ".xpt" else "sas7bdat")
    # hdf and spss readers need a file path
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp,"data"+ext)
        with open(path,"wb") as fh:
            fh.write(data)
        if fmt == "spss":
            return pd.read_spss(path)
        with pd.HDFStore(path,mode="r") as store:
            keys = store.keys()
            if len(keys) != 1:
                raise CaseError(f"'{filename}' holds {len(keys)} data sets "
                    f"({', '.join(keys) or 'none'}); keep one")
            return store[keys[0]]

def _value(value):
    """Convert a pandas cell to a plain Python value (`None` when missing)"""
    if hasattr(value,"item"):
        value = value.item()
    if value is None or (isinstance(value,float) and math.isnan(value)):
        return None
    return value

DROPNA = ("rows","columns","empty-rows","empty-columns")
"""`dropna` choices of `read_rows` and `read_series`: drop the rows (or
columns) with any missing value, or only those with no value at all"""

def _dropna(columns:list[str],rows:list[list],dropna:str|None,keep:tuple=(),
        report:dict|None=None) -> tuple[list[str],list[list]]:
    """Drop rows or columns with missing (`None`) cells as `dropna` says
    (see `DROPNA`; `None` or empty keeps everything); columns `keep`
    (indexes) are never dropped nor looked at. Fills `report` with
    `dropped`: `{"rows": count, "columns": [names]}`"""
    if not dropna:
        return columns,rows
    if dropna not in DROPNA:
        raise CaseError(f"dropna must be one of {', '.join(DROPNA)}, not '{dropna}'")
    test = all if dropna.startswith("empty-") else any
    look = [j for j in range(len(columns)) if j not in keep]
    gone_rows,gone_cols = 0,[]
    if dropna.endswith("rows"):
        out = [r for r in rows if not (look and test(r[j] is None for j in look))]
        gone_rows,rows = len(rows) - len(out),out
    else:
        drop = {j for j in look if rows and test(r[j] is None for r in rows)}
        gone_cols = [columns[j] for j in sorted(drop)]
        columns = [c for j,c in enumerate(columns) if j not in drop]
        rows = [[v for j,v in enumerate(r) if j not in drop] for r in rows]
    if gone_rows and not rows:
        raise CaseError(f"every row has {'no' if test is all else 'a missing'} value, so dropping "
            f"them (dropna '{dropna}') leaves nothing to import")
    if gone_cols and len(gone_cols) == len(look):
        raise CaseError(f"every {'value ' if keep else ''}column has "
            f"{'no' if test is all else 'a missing'} value, so dropping them (dropna '{dropna}') "
            "leaves nothing to import")
    if report is not None:
        report["dropped"] = {"rows":gone_rows,"columns":gone_cols}
    return columns,rows

def read_rows(filename:str,data:bytes,dropna:str|None=None,
        report:dict|None=None) -> tuple[list[str],list[list]]:
    """Read table data from file contents

    Arguments
    ---------

      - `filename`: file name, whose extension sets the format

      - `data`: file contents

      - `dropna`: drop missing values when read (`DROPNA`): `rows` drops
        each row with a missing cell, `columns` each column with one,
        `empty-rows` and `empty-columns` only rows or columns with no
        value at all; `None` (default) keeps them

      - `report`: a dict to fill with `dropped` (`{"rows": count,
        "columns": [names]}`) when `dropna` is given

    Returns
    -------

      - `tuple[list[str],list[list]]`: column names (stripped) and rows of
        plain Python values, `None` for missing cells

    Exceptions
    ----------

      - `CaseError`: unsupported format, the file cannot be read, an
        unknown `dropna`, or `dropna` dropping every row or column
    """
    fmt,compression = table_format(filename)
    try:
        frame = _read_frame(filename,data,fmt,compression)
    except CaseError:
        raise
    except Exception as err: # pandas raises many kinds of errors
        raise CaseError(f"cannot read '{filename}': {err}") from err
    columns = [str(c).strip() for c in frame.columns]
    rows = [[_value(v) for v in row] for row in frame.astype(object).itertuples(index=False,name=None)]
    return _dropna(columns,rows,dropna,report=report)

def dump_rows(table:str,rows:list[list],filename:str,
        extended:tuple[list[dict],list[list]]|None=None) -> bytes:
    """Write table rows to file contents

    Arguments
    ---------

      - `table`: table name (sets the column names)

      - `rows`: rows in `COLUMNS[table]` order

      - `filename`: file name, whose extension sets the format

      - `extended`: optional `(columns, rows)` of solution values to write
        after the table columns: `columns` are `RESULT_COLUMNS` entries,
        `rows` one per row of `rows` (`None` for a missing value, written
        as an empty cell)

    Returns
    -------

      - `bytes`: file contents

    Exceptions
    ----------

      - `CaseError`: unknown table or unsupported format
    """
    import pandas as pd # pylint: disable=import-outside-toplevel
    if table not in COLUMNS:
        raise CaseError(f"unknown table '{table}'")
    fmt,compression = table_format(filename,write=True)
    columns = [c["name"] for c in COLUMNS[table]]
    frame = pd.DataFrame([list(r) for r in rows],columns=columns)
    for col in COLUMNS[table]:
        if col["kind"] in ("int","enum"):
            frame[col["name"]] = frame[col["name"]].astype("int64")
        elif col["kind"] == "float":
            frame[col["name"]] = frame[col["name"]].astype("float64")
        else:
            frame[col["name"]] = frame[col["name"]].astype(str)
    if extended:
        cols,values = extended
        for j,col in enumerate(cols):
            frame[col["name"]] = pd.Series([math.nan if v[j] is None else float(v[j])
                for v in values],dtype="float64")
    buffer = io.BytesIO()
    if fmt in ("csv","tsv"):
        frame.to_csv(buffer,sep="," if fmt == "csv" else "\t",index=False,
            compression=compression)
    elif fmt == "json":
        # JSON has no infinity: write it as the strings "Inf"/"-Inf" (as in
        # the editor state), which Case.import_rows reads back
        frame = frame.astype(object).map(lambda v: ("Inf" if v > 0 else "-Inf")
            if isinstance(v,float) and math.isinf(v) else v)
        frame.to_json(buffer,orient="records",compression=compression,
            double_precision=15)
    elif fmt == "parquet":
        frame.to_parquet(buffer,index=False)
    elif fmt == "feather":
        frame.to_feather(buffer)
    else:
        frame.to_excel(buffer,index=False,sheet_name=table)
    return buffer.getvalue()

SERIES_LABELS = ("time","timestamp","datetime","date","step","index")
"""Names of the step label column of a time series file (any case)"""

def read_series(filename:str,data:bytes,dropna:str|None=None,
        report:dict|None=None) -> tuple[list[str]|None,list[str],list[list]]:
    """Read a time series from file contents (see the module docstring)

    `dropna` and `report` are as in `read_rows`, applied to the value
    columns only: the label column is never dropped, and a missing label
    does not make a step missing (`rows` drops steps, `columns` drops
    value columns).

    Returns
    -------

      - `tuple`: step labels (text, or `None` when the file has no label
        column), value column names, and one row of values per step

    Exceptions
    ----------

      - `CaseError`: unsupported format, the file cannot be read, or more
        than one label column
    """
    columns,rows = read_rows(filename,data)
    label = [j for j,c in enumerate(columns) if c.lower() in SERIES_LABELS
        or (j == 0 and (c == "" or c.startswith("Unnamed:")))]
    if len(label) > 1:
        raise CaseError(f"'{filename}' has {len(label)} step label columns "
            f"({', '.join(columns[j] for j in label)}); keep one")
    if not label:
        columns,rows = _dropna(columns,rows,dropna,report=report)
        return None,columns,rows
    j = label[0]  # move the label column first, then keep it out of dropna
    columns = [columns[j]] + columns[:j] + columns[j+1:]
    rows = [[row[j]] + row[:j] + row[j+1:] for row in rows]
    columns,rows = _dropna(columns,rows,dropna,(0,),report)
    # a numeric label column with a blank cell is read as floats: 1.0 -> "1"
    index = ["" if row[0] is None else str(int(row[0]))
        if isinstance(row[0],float) and row[0].is_integer() else str(row[0]) for row in rows]
    return index,columns[1:],[row[1:] for row in rows]

def dump_series(series:dict,filename:str) -> bytes:
    """Write a time series (`Case.series`) to file contents: a `time` column
    of step labels, then one column per series column"""
    import pandas as pd # pylint: disable=import-outside-toplevel
    fmt,compression = table_format(filename,write=True)
    frame = pd.DataFrame(series["values"],columns=series["columns"],dtype="float64")
    frame.insert(0,"time",list(series["index"]))
    buffer = io.BytesIO()
    if fmt in ("csv","tsv"):
        frame.to_csv(buffer,sep="," if fmt == "csv" else "\t",index=False,compression=compression)
    elif fmt == "json":
        frame.to_json(buffer,orient="records",compression=compression,double_precision=15)
    elif fmt == "parquet":
        frame.to_parquet(buffer,index=False)
    elif fmt == "feather":
        frame.to_feather(buffer)
    else:
        frame.to_excel(buffer,index=False,sheet_name="series")
    return buffer.getvalue()
