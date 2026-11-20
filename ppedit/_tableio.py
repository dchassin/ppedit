"""Import and export of case table rows as data files (issue #73)

Files are read and written with pandas, which is used here only: the case
model stays plain lists of rows (decision #4). The format is chosen from the
file name extension (`table_format`), for example `bus.csv`, `bus.csv.gz`,
`gen.parquet` or `branch.xlsx`. Formats whose pandas engine is not installed
(`pyarrow` for Parquet and Feather, `openpyxl` for Excel) are not offered by
`table_formats` and are refused by `read_rows` and `dump_rows`.

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

from ppedit.case import COLUMNS,CaseError

# extension -> (pandas format, module needed besides pandas)
_FORMATS = {
    ".csv":("csv",None),
    ".tsv":("tsv",None),
    ".json":("json",None),
    ".parquet":("parquet","pyarrow"),
    ".feather":("feather","pyarrow"),
    ".xlsx":("excel","openpyxl"),
}

# compression suffix -> (pandas compression, module needed); text formats only
_COMPRESSION = {
    ".gz":("gzip",None),
    ".bz2":("bz2",None),
    ".xz":("xz",None),
    ".zip":("zip",None),
    ".zst":("zstd","zstandard"),
}

_TEXT = ("csv","tsv","json")

def _installed(module:str|None) -> bool:
    return module is None or importlib.util.find_spec(module) is not None

def table_formats() -> list[str]:
    """Return the file extensions that can be read and written

    Returns
    -------

      - `list[str]`: extensions such as `.csv`, `.csv.gz` and `.parquet`
        whose pandas engine is installed; empty if pandas is not installed
    """
    if not _installed("pandas"):
        return []
    out = []
    for ext,(fmt,module) in _FORMATS.items():
        if not _installed(module):
            continue
        out.append(ext)
        if fmt in _TEXT:
            out.extend(ext+suffix for suffix,(_,mod) in _COMPRESSION.items() if _installed(mod))
    return out

def table_format(filename:str) -> tuple[str,str|None]:
    """Return the pandas format and compression of a file name

    Arguments
    ---------

      - `filename`: file name or path, e.g. `bus.csv.gz`

    Returns
    -------

      - `tuple[str,str|None]`: format (`csv`, `tsv`, `json`, `parquet`,
        `feather` or `excel`) and compression (`gzip`, `bz2`, `xz`, `zip`,
        `zstd` or `None`)

    Exceptions
    ----------

      - `CaseError`: the extension is not supported or its pandas engine is
        not installed
    """
    name = filename.lower()
    compression = None
    for suffix,(comp,module) in _COMPRESSION.items():
        if name.endswith(suffix):
            if not _installed(module):
                raise CaseError(f"'{suffix}' files need the '{module}' package")
            name,compression = name[:-len(suffix)],comp
            break
    for ext,(fmt,module) in _FORMATS.items():
        if name.endswith(ext):
            if compression and fmt not in _TEXT:
                raise CaseError(f"'{ext}' files cannot be compressed")
            if not _installed("pandas"):
                raise CaseError("importing and exporting table data needs the 'pandas' package")
            if not _installed(module):
                raise CaseError(f"'{ext}' files need the '{module}' package")
            return fmt,compression
    raise CaseError(f"'{filename}' is not a supported table data file "
        "(use " + ", ".join(table_formats() or [".csv"]) + ")")

def _value(value):
    """Convert a pandas cell to a plain Python value (`None` when missing)"""
    if hasattr(value,"item"):
        value = value.item()
    if value is None or (isinstance(value,float) and math.isnan(value)):
        return None
    return value

def read_rows(filename:str,data:bytes) -> tuple[list[str],list[list]]:
    """Read table data from file contents

    Arguments
    ---------

      - `filename`: file name, whose extension sets the format

      - `data`: file contents

    Returns
    -------

      - `tuple[list[str],list[list]]`: column names (stripped) and rows of
        plain Python values, `None` for missing cells

    Exceptions
    ----------

      - `CaseError`: unsupported format, or the file cannot be read
    """
    import pandas as pd # pylint: disable=import-outside-toplevel
    fmt,compression = table_format(filename)
    buffer = io.BytesIO(data)
    try:
        if fmt in ("csv","tsv"):
            frame = pd.read_csv(buffer,sep="," if fmt == "csv" else "\t",
                compression=compression,skipinitialspace=True)
        elif fmt == "json":
            frame = pd.read_json(buffer,orient="records",compression=compression,
                precise_float=True)
        elif fmt == "parquet":
            frame = pd.read_parquet(buffer)
        elif fmt == "feather":
            frame = pd.read_feather(buffer)
        else:
            frame = pd.read_excel(buffer)
    except Exception as err: # pandas raises many kinds of errors
        raise CaseError(f"cannot read '{filename}': {err}") from err
    columns = [str(c).strip() for c in frame.columns]
    rows = [[_value(v) for v in row] for row in frame.astype(object).itertuples(index=False,name=None)]
    return columns,rows

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
    fmt,compression = table_format(filename)
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

def read_series(filename:str,data:bytes) -> tuple[list[str]|None,list[str],list[list]]:
    """Read a time series from file contents (see the module docstring)

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
        return None,columns,rows
    j = label[0]
    index = ["" if row[j] is None else str(row[j]) for row in rows]
    return index,columns[:j]+columns[j+1:],[row[:j]+row[j+1:] for row in rows]

def dump_series(series:dict,filename:str) -> bytes:
    """Write a time series (`Case.series`) to file contents: a `time` column
    of step labels, then one column per series column"""
    import pandas as pd # pylint: disable=import-outside-toplevel
    fmt,compression = table_format(filename)
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
