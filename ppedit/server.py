"""Editor web server

`create_app()` returns a Flask application that serves the browser editor
(`ppedit/static/`) and a JSON API over one in-memory `ppedit.Case`.

API
---

All `POST` requests must send JSON and the header `X-PPEdit: 1`; browsers
will not send that header cross-origin without a CORS preflight, which the
server does not grant, so other web pages cannot change the case or write
files. Requests whose `Host` is not a loopback name are refused unless the
app was created with `allow_remote=True`.

When the app is created with a `token` (`ppedit serve` always makes one),
every request must also carry it: as the `token` query parameter of the
URL (`http://localhost:8050/?token=...`, what `ppedit serve` prints and
opens) or as the session cookie the server sets in exchange for it
(`ppedit_<hash>`, `HttpOnly`, `SameSite=Strict`, named after a hash of the
token so servers on other ports keep their own cookies). A page `GET`
with the token in the URL is redirected to the same URL without it, so
the token does not stay in the address bar. Requests without a valid
token get status 403.

Every successful call returns the editor state: `case` (see
`ppedit.Case.to_dict`), `path`, `format`, `dirty`, `issues`, `can_undo`,
`can_redo`, `result` (last solution or `null`), `cascade` (whether the
last edit changed cells other than the one edited), and `revision` (a
counter that changes whenever the case changes), `source` (how the
open case was read, see `ppedit.Case.source`, or `null`), and `loads_off`
(loads switched off by `POST /api/load`, as `[bus, Pd, Qd]`). Errors return
`{"error": message}` with status 400.

  - `GET  /api/meta`: columns, result columns (`ppedit.case.RESULT_COLUMNS`:
    the read-only extended columns a solution adds to the tables), tables,
    built-in case names, formats (written),
    input formats (read), table data formats (`table_formats`: file
    extensions `POST /api/table/import` and `GET /api/table/export` accept),
    solvers (`ppedit.case.solvers`: the PF algorithms,
    the installed OPF solvers and the cvxopf solve option choices), version,
    `about` (package metadata shown when the page's "ppedit" title is
    clicked; see `about`), `instance` (a token that changes each time
    the server starts), and `token` (the session token, or `null`; the
    page shows it in the restart command so a restarted server accepts
    its cookie)
  - `GET  /api/state`: current state
  - `GET  /api/ping`: `{"instance", "revision"}`, answered without waiting
    for the editor lock; the page polls it to notice that the server stopped
    or was restarted (a different `instance`)
  - `POST /api/new`: `{}` empty case or `{"builtin": "case14"}`
  - `POST /api/open`: `{"filename": ..., "text": ...}` (upload),
    `{"path": ...}` (server file), or `{"url": ...}` (an `http`/`https` URL
    the server downloads, at most `DOWNLOAD_LIMIT` bytes; the extension of
    the URL path selects the format, and GitHub file page URLs are changed to
    raw file URLs); `.py`, `.json`, `.glm`, or PSS/E
    `.raw` (read only: Save needs a `.py`, `.json` or `.glm` path; uploads
    and downloads have no path until saved)
  - `POST /api/global`: `{"key": "baseMVA"|"version"|"name", "value": ...}`
  - `POST /api/cell`: `{"table", "row", "column", "value"}`
  - `POST /api/load`: `{"bus"}` switch the load at bus number `bus` off
    (`Pd` and `Qd` set to 0, their values kept) or back on (the kept values
    restored), as one undoable edit (see `Editor.toggle_load`); state
    `loads_off` lists the loads switched off as `[bus, Pd, Qd]`
  - `POST /api/rows`: `{"table", "values"?, "after"?}` add a row
  - `POST /api/rows/delete`: `{"table", "rows": [...], "cascade"?}`
  - `POST /api/table/import`: `{"table", "filename", "data", "rows"?}`
    read table data from a file (`data`: its contents in base64; the
    format comes from the `filename` extension, see
    `ppedit._tableio.table_format`) and append the rows, or replace the
    rows listed in `rows` (see `ppedit.Case.import_rows`); the state has
    `imported` (`{"added"|"replaced": n, "cascade"}`)
  - `GET  /api/table/export?table=...&format=csv&rows=1,2`: download the
    rows of a table (`rows`: comma-separated row indexes, default all) as
    a data file in the format of the extension `format` (e.g. `csv`,
    `csv.gz`, `parquet`, `xlsx`), named `<case>_<table>.<format>`; while a
    successful solution exists, the extended (result) columns the page
    shows follow the table columns (`Editor.extended`)
  - `POST /api/undo`, `POST /api/redo`
  - `POST /api/solve`: `{"kind": "pf"|"opf", "algorithm"?, "options"?}`;
    `algorithm` is a PF algorithm (`NR`, the default, `FDXB`, `FDBX`, `GS`)
    or an OPF solver (`PIPS`, the default, `PIPS-SC`, `DC-PIPS`, ...; see
    `ppedit.case.OPF_SOLVERS`); `options` are the `CVXOPF-*` solve options
    (see `ppedit.Case.solve`). The result's `messages` (solver errors and
    warnings, with an optional `detail`: physical meaning and constraints
    involved) are listed on the page's Issues tab
  - `POST /api/apply`: copy the last solution into the case
  - `POST /api/geodata`: `{"filename": ..., "text": ...}` (CSV upload),
    `{"path": ...}` (server CSV file), or `{"clear": true}`; replaces the
    case geodata (see `ppedit.Case.load_geodata`) as one undoable edit
  - `POST /api/save`: `{"path"?, "force"?}` write to the open file or to
    `path`; when the case was read from a GLM file with content ppedit does
    not edit (`case.notes`), overwriting an existing file returns status 409
    with `"confirm": true` unless `force` is true
  - `POST /api/folders`: `{"path"?}` list a folder on the server machine
    (default: the open file's folder, else the server's working directory)
    for the page's folder chooser; returns `{"path", "parent", "folders",
    "files"}` (see `list_folder`), not the editor state
  - `POST /api/folder`: `{"path"}` move the open file's path into that
    folder on the server machine without writing anything (the file name
    is kept, or is the case name with `.glm` when there is no path; `.raw`
    becomes `.glm`); the case is marked unsaved until Save writes it there
  - `GET  /api/export?format=py|json|glm|csv`: download the case (`csv`:
    its geodata)
  - `POST /api/exit`: `{}` stop the server (unsaved changes are lost);
    returns `{"exit": true}` and shuts down shortly after responding

Claude sessions (see `ppedit.claude`); each returns the editor state plus
`claude`, the list of sessions (`ppedit.claude.Sessions.to_dict`):

  - `GET  /api/claude`: the sessions only (polled by the Claude tab)
  - `POST /api/claude/new`: `{"title"?, "model"?, "web"?, "local"?, "root"?}`
  - `POST /api/claude/send`: `{"id", "text"}` add a message; Claude works on
    it in the background, editing the case through undoable edits
  - `POST /api/claude/stop`: `{"id"}` stop after the current step
  - `POST /api/claude/delete`: `{"id"}` stop and remove the session

Caveats
-------

  - The development server started by `ppedit serve` is single-process and
    meant for local use only.
"""

import base64
import binascii
import copy
import hashlib
import hmac
import importlib.metadata
import logging
import os
import re
import secrets
import signal
import threading
import urllib.error
import urllib.parse
import urllib.request

from flask import Flask, jsonify, request, send_from_directory, Response, redirect

from ppedit.case import Case, CaseError, COLUMNS, RESULT_COLUMNS, TABLES, FORMATS, INPUT_FORMATS, \
    builtin_cases, solvers, writable_format
from ppedit.claude import Sessions
from ppedit._tableio import table_formats, read_rows, dump_rows

_logger = logging.getLogger(__file__)

STATIC = os.path.join(os.path.dirname(__file__),"static")
"""Folder of the browser editor files"""

HISTORY = 200
"""Number of undo steps kept"""

DOWNLOAD_LIMIT = 50000000
"""Largest case file `POST /api/open` downloads from a URL, in bytes"""

DOWNLOAD_TIMEOUT = 60
"""Seconds to wait for a URL download to respond"""

_LOOPBACK = {"localhost","127.0.0.1","::1","[::1]"}

_SOURCE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_FUNDING_HEADING = re.compile(r"fund|acknowledg|sponsor",re.IGNORECASE)

def _readme_funding(readme:str) -> str|None:
    """Funding acknowledgment section of a README

    Arguments:

      - `readme`: README text (Markdown)

    Returns:

      - `str|None`: text of the first section whose heading mentions
        funding, an acknowledgment or a sponsor, up to the next heading of
        the same or a higher level, or `None` when there is none

    Example:

        >>> _readme_funding("# Usage\\nrun it\\n# Funding\\nPaid by X.\\n# Other\\n")
        'Paid by X.'
    """
    lines = readme.splitlines()
    for n,line in enumerate(lines):
        head = re.match(r"(#+)\s+(.*)",line)
        if head and _FUNDING_HEADING.search(head.group(2)):
            level = len(head.group(1))
            body = []
            for text in lines[n+1:]:
                more = re.match(r"(#+)\s",text)
                if more and len(more.group(1)) <= level:
                    break
                body.append(text)
            return "\n".join(body).strip() or None
    return None

def _source_text(dists,*names) -> str:
    # a file of an installed distribution, else of the source tree; every
    # distribution found is tried, since a stale in-tree `ppedit.egg-info`
    # (left by `pip install .`) can be found first and has no license file
    for dist in dists:
        for name in names:
            try:
                text = dist.read_text(name)
            except (OSError,ValueError):
                text = None
            if text:
                return text
    for name in names:
        path = os.path.join(_SOURCE,os.path.basename(name))
        if os.path.isfile(path):
            with open(path,encoding="utf-8") as fh:
                return fh.read()
    return ""

def about() -> dict:
    """Package metadata shown by the editor's "ppedit" title

    Read from the installed `ppedit` distribution metadata (`pyproject.toml`
    `[project]`), its `LICENSE` file and its README (the long description),
    or from the source tree when the package is not installed.

    Returns:

      - `dict`: `name`, `version`, `summary`, `copyright` (the `Copyright`
        lines of `LICENSE`), `license` (the first line of `LICENSE`, e.g.
        `MIT License`), `repository`, `documentation`, `issues` and
        `license_url` (URLs, or `None` when not known), and `funding` (the
        funding acknowledgment section of the README, Markdown, or `None`)
    """
    dists = list(importlib.metadata.distributions(name="ppedit"))
    meta = dists[0].metadata if dists else {}
    urls = {}
    for entry in (meta.get_all("Project-URL") or []) if meta else []:
        label,_,url = entry.partition(",")
        urls[label.strip().lower()] = url.strip()
    repository = urls.get("repository") or urls.get("homepage")
    if repository and repository.endswith(".git"):
        repository = repository[:-4]
    license_text = _source_text(dists,"licenses/LICENSE","LICENSE")
    readme = None
    if meta:
        readme = meta.get("Description") or getattr(meta,"get_payload",lambda:None)()
    readme = readme or _source_text([],"README.md")
    try:
        from ppedit import __version__ as version # pylint: disable=import-outside-toplevel
    except ImportError:
        version = "dev"
    return {
        "name": meta.get("Name","ppedit") if meta else "ppedit",
        "version": version,
        "summary": meta.get("Summary") if meta else None,
        "copyright": [x.strip() for x in license_text.splitlines()
            if x.strip().lower().startswith("copyright")],
        "license": license_text.strip().splitlines()[0].strip() if license_text.strip() else None,
        "repository": repository,
        "documentation": urls.get("documentation"),
        "issues": urls.get("issues") or (repository and repository+"/issues"),
        "license_url": repository and repository+"/blob/main/LICENSE",
        "funding": _readme_funding(readme or ""),
        }

def _shutdown():
    """Stop the server process as Ctrl-C would, or exit if `SIGINT` is ignored"""
    if signal.getsignal(signal.SIGINT) in (signal.SIG_IGN,None):
        os._exit(0) # e.g. started in the background, where SIGINT is ignored
    signal.raise_signal(signal.SIGINT)

def github_raw_url(url:str) -> str:
    """Change a GitHub file page URL to the URL of the raw file

    `https://github.com/OWNER/REPO/blob/REF/PATH` (or `/raw/`) becomes
    `https://raw.githubusercontent.com/OWNER/REPO/REF/PATH`; the query and
    fragment are dropped. Other URLs are returned unchanged.

    >>> github_raw_url("https://github.com/o/r/blob/feature/x/d/case.py?plain=1#L9")
    'https://raw.githubusercontent.com/o/r/feature/x/d/case.py'
    >>> github_raw_url("https://example.org/case.glm")
    'https://example.org/case.glm'
    """
    parts = urllib.parse.urlsplit(url)
    if parts.netloc.lower() in ("github.com","www.github.com"):
        path = parts.path.split("/")
        if len(path) > 5 and path[3] in ("blob","raw"):
            return "https://raw.githubusercontent.com/" + "/".join(path[1:3]+path[4:])
    return url

def download_case(url:str) -> Case:
    """Download a case file from an `http` or `https` URL and read it

    Arguments
    ---------

      - `url`: the file URL; the extension of its path selects the format.
        GitHub file page URLs are changed to raw file URLs
        (`github_raw_url`).

    Returns
    -------

      - `Case`: the case, with `Case.source["file"]` set to the URL
        downloaded

    Exceptions
    ----------

      - `CaseError`: other scheme, unsupported extension, HTTP or network
        error, an HTML page (`Content-Type` `text/html`), file larger than
        `DOWNLOAD_LIMIT`, or unreadable content
    """
    url = github_raw_url(url.strip())
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() not in ("http","https") or not parts.netloc:
        raise CaseError("only http and https URLs can be opened")
    filename = os.path.basename(urllib.parse.unquote(parts.path))
    ext = os.path.splitext(filename)[1].lower()
    if ext not in INPUT_FORMATS:
        raise CaseError(f"'{url}' does not name a {', '.join(INPUT_FORMATS)} file")
    req = urllib.request.Request(url,headers={"User-Agent":"ppedit"})
    try:
        with urllib.request.urlopen(req,timeout=DOWNLOAD_TIMEOUT) as res:
            data = res.read(DOWNLOAD_LIMIT+1)
            ctype = res.headers.get("Content-Type","")
    except urllib.error.HTTPError as err:
        raise CaseError(f"HTTP {err.code} from {url}") from err
    except (urllib.error.URLError,OSError) as err:
        raise CaseError(f"cannot download {url}: {getattr(err,'reason',err)}") from err
    if ctype.split(";")[0].strip().lower() in ("text/html","application/xhtml+xml"):
        raise CaseError(f"'{url}' is a web page, not a case file "
            "(use the link to the raw file)")
    if len(data) > DOWNLOAD_LIMIT:
        raise CaseError(f"'{url}' is larger than {DOWNLOAD_LIMIT} bytes")
    try: # PSS/E names may be Latin-1, as in Case.load
        text = data.decode("utf-8",errors="replace" if ext == ".raw" else "strict")
    except UnicodeDecodeError as err:
        raise CaseError(f"'{url}' is not UTF-8 text") from err
    case = Case.load(filename,text)
    case.source["file"] = url
    _logger.info("downloaded %s (%d bytes)",url,len(data))
    return case

def list_folder(path:str) -> dict:
    """List a folder on the server machine for the page's folder chooser

    Arguments
    ---------

      - `path`: the folder; `~` is expanded and relative paths are taken
        from the server's working directory

    Returns
    -------

      - `dict`: `path` (absolute), `parent` (absolute, or `None` at the
        root), `folders` (names of the subfolders not starting with `.`),
        and `files` (names of the files ppedit can save, by `FORMATS`
        extension), each sorted case-insensitively

    Exceptions
    ----------

      - `CaseError`: not a folder, or it cannot be read
    """
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        raise CaseError(f"'{path}' is not a folder")
    try:
        entries = list(os.scandir(path))
    except OSError as err:
        raise CaseError(f"cannot read '{path}': {err.strerror or err}") from err
    def is_dir(entry):
        try:
            return entry.is_dir()
        except OSError:
            return False
    key = str.lower
    folders = sorted((e.name for e in entries if is_dir(e) and not e.name.startswith(".")),key=key)
    files = sorted((e.name for e in entries if not is_dir(e)
        and os.path.splitext(e.name)[1].lower() in FORMATS),key=key)
    parent = os.path.dirname(path)
    return {"path":path,"parent":parent if parent != path else None,
        "folders":folders,"files":files}

class Editor:
    """In-memory editing session: the case, its file, and undo history

    Arguments
    ---------

      - `case`: initial case (default: empty case)

      - `path`: file the case was read from, if any
    """

    def __init__(self,case:Case|None=None,path:str|None=None):
        self.case = case or Case(name="untitled")
        """The case being edited"""
        self.path = path
        """File the case is saved to (or `None`)"""
        self.dirty = False
        """Whether the case changed since it was opened or saved"""
        self.result = None
        """Last solver result (cleared by any edit)"""
        self.undo_stack = []
        self.redo_stack = []
        self.revision = 0
        """Counter incremented whenever the case changes (edit, undo, redo, open)"""
        self.loads_off = {}
        """Loads switched off by `toggle_load`: bus number -> `[Pd, Qd]` to restore"""
        self.lock = threading.RLock()

    def _snapshot(self):
        return (copy.deepcopy(self.case.tables),self.case.baseMVA,
            self.case.version,self.case.name,
            copy.deepcopy(self.case.geodata),list(self.case.geotags),
            copy.deepcopy(self.loads_off))

    def _restore(self,snap):
        tables,base,version,name,geodata,geotags,loads_off = snap
        self.case.tables = copy.deepcopy(tables)
        self.case.baseMVA,self.case.version,self.case.name = base,version,name
        self.case.geodata,self.case.geotags = copy.deepcopy(geodata),list(geotags)
        self.loads_off = copy.deepcopy(loads_off)

    def _prune_loads_off(self):
        # a switched-off load stays off only while its bus exists with Pd = Qd = 0
        zero = {b[0] for b in self.case.tables["bus"] if b[2] == 0 and b[3] == 0}
        for bus in [b for b in self.loads_off if b not in zero]:
            del self.loads_off[bus]

    def edit(self,func):
        """Run `func()` as one undoable edit; roll back if it raises"""
        snap = self._snapshot()
        try:
            out = func()
            self._prune_loads_off()
        except Exception:
            self._restore(snap)
            raise
        if self._snapshot() != snap:
            self.undo_stack.append(snap)
            del self.undo_stack[:-HISTORY]
            self.redo_stack.clear()
            self.dirty = True
            self.result = None
            self.revision += 1
        return out

    def replace(self,case:Case,path:str|None):
        """Open a different case (clears history)"""
        self.case,self.path = case,path
        self.dirty,self.result = False,None
        self.loads_off = {}
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.revision += 1

    def undo(self):
        """Undo the last edit"""
        if not self.undo_stack:
            raise CaseError("nothing to undo")
        self.redo_stack.append(self._snapshot())
        self._restore(self.undo_stack.pop())
        self.dirty,self.result = True,None
        self.revision += 1

    def redo(self):
        """Redo the last undone edit"""
        if not self.redo_stack:
            raise CaseError("nothing to redo")
        self.undo_stack.append(self._snapshot())
        self._restore(self.redo_stack.pop())
        self.dirty,self.result = True,None
        self.revision += 1

    def toggle_load(self,bus:int) -> bool:
        """Switch the load at a bus off or back on, as one undoable edit

        A load with `Pd` or `Qd` not zero is switched off: both are set to 0
        and their values are kept in `loads_off`. A load switched off this way
        is switched back on by restoring those values. The values are kept
        with the undo history but not in the case file, so a saved case has
        `Pd = Qd = 0` there; editing `Pd` or `Qd`, or renumbering or deleting
        the bus, forgets them.

        Arguments
        ---------

          - `bus`: bus number (`bus_i`)

        Returns
        -------

          - `bool`: `True` if the load is now on
        """
        rows = [i for i,b in enumerate(self.case.tables["bus"]) if b[0] == bus]
        if not rows:
            raise CaseError(f"bus {bus} not found")
        row = rows[0]

        def run():
            record = self.case.tables["bus"][row]
            if record[2] != 0 or record[3] != 0:
                saved = [record[2],record[3]]
                self.case.set_value("bus",row,"Pd",0)
                self.case.set_value("bus",row,"Qd",0)
                self.loads_off[bus] = saved
                return False
            if bus not in self.loads_off:
                raise CaseError(f"bus {bus} has no switched-off load to restore")
            pd,qd = self.loads_off.pop(bus)
            self.case.set_value("bus",row,"Pd",pd)
            self.case.set_value("bus",row,"Qd",qd)
            return True

        return self.edit(run)

    def extended(self,table:str,index:list[int]) -> tuple[list[dict],list[list]]|None:
        """Extended (result) columns of the last solution for rows `index`
        of `table`, as `(columns, rows)` for `dump_rows`, or `None`

        Only the columns the solver gave (not all `None` over the whole
        table) are included, as the page shows them (decision #56).
        """
        res = self.result
        ext = res.get("extended",{}).get(table) if res and res.get("success") else None
        if not ext or len(ext) != len(self.case.tables[table]):
            return None
        spec = RESULT_COLUMNS[table]
        keep = [j for j in range(len(spec)) if any(row[j] is not None for row in ext)]
        if not keep:
            return None
        return [spec[j] for j in keep],[[ext[i][j] for j in keep] for i in index]

    def state(self,cascade:bool=False) -> dict:
        """Return the editor state sent to the browser"""
        fmt = INPUT_FORMATS.get(os.path.splitext(self.path)[1].lower()) if self.path else None
        return {
            "case": self.case.to_dict(),
            "path": self.path,
            "format": fmt,
            "dirty": self.dirty,
            "issues": self.case.validate(),
            "can_undo": bool(self.undo_stack),
            "can_redo": bool(self.redo_stack),
            "result": self.result,
            "cascade": cascade,
            "revision": self.revision,
            "source": self.case.source,
            "loads_off": [[bus,pd,qd] for bus,(pd,qd) in sorted(self.loads_off.items())],
        }

def token_cookie(token:str) -> str:
    """Name of the session cookie for `token`

    Arguments
    ---------

      - `token`: session token given to `create_app`

    Returns
    -------

      - `str`: `ppedit_` and the first 12 hex digits of the SHA-256 of the
        token; browsers share `localhost` cookies across ports, so each
        server (token) needs its own cookie name
    """
    return "ppedit_" + hashlib.sha256(token.encode()).hexdigest()[:12]

def create_app(case:Case|None=None,path:str|None=None,allow_remote:bool=False,
        shutdown=None,claude_client=None,token:str|None=None) -> Flask:
    """Create the editor application

    Arguments
    ---------

      - `case`: case to edit (default: empty case)

      - `path`: file `case` was read from; "Save" writes back to it

      - `allow_remote`: accept requests whose `Host` is not a loopback name

      - `shutdown`: function called (shortly after responding) by
        `POST /api/exit` to stop the server (`ppedit serve` passes its
        server's `shutdown`); default raises `SIGINT` in the server
        process, or exits it if `SIGINT` is ignored

      - `claude_client`: Messages API client for the Claude sessions
        (default: `urllib`; tests pass a fake)

      - `token`: session token every request must carry (`?token=` in the
        URL, or the cookie set in exchange for it; see the module
        docstring); default `None` accepts requests without one

    Returns
    -------

      - `Flask`: the application; `app.config["EDITOR"]` is the `Editor`
    """
    app = Flask(__name__,static_folder=None)
    editor = Editor(case,path)
    app.config["EDITOR"] = editor
    claude = Sessions(editor,claude_client)
    app.config["CLAUDE"] = claude
    app.config["SHUTDOWN"] = shutdown or _shutdown
    instance = secrets.token_hex(8) # tells the page a restarted server from the one it loaded from

    cookie = token_cookie(token) if token is not None else None

    def _set_token(res):
        res.set_cookie(cookie,token,httponly=True,samesite="Strict",path="/")
        return res

    @app.before_request
    def _guard():
        host = (request.host or "").rsplit(":",1)[0] if not (request.host or "").startswith("[") \
            else request.host.split("]")[0]+"]"
        if not allow_remote and host not in _LOOPBACK:
            return jsonify(error="remote access is disabled"),403
        if token is not None:
            given = request.args.get("token")
            if given is not None and hmac.compare_digest(given.encode(),token.encode()):
                if request.method == "GET" and not request.path.startswith("/api/"):
                    query = urllib.parse.urlencode([(k,v) for k,v in request.args.items(multi=True) if k != "token"])
                    return _set_token(redirect(request.path + ("?"+query if query else ""),code=303))
            elif not hmac.compare_digest(request.cookies.get(cookie,"").encode(),token.encode()):
                message = "missing or invalid session token: open the URL that ppedit serve printed"
                if request.path.startswith("/api/"):
                    return jsonify(error=message),403
                return Response(message+"\n",status=403,mimetype="text/plain")
        if request.method == "POST":
            if request.headers.get("X-PPEdit") != "1" or not request.is_json:
                return jsonify(error="missing X-PPEdit header or JSON body"),403
        return None

    @app.after_request
    def _remember_token(res):
        # a request authorized by ?token= (scripts, API calls) also gets the cookie
        if token is not None and not any(c.startswith(cookie+"=") for c in res.headers.getlist("Set-Cookie")) \
                and hmac.compare_digest(request.args.get("token","").encode(),token.encode()):
            _set_token(res)
        return res

    @app.errorhandler(CaseError)
    def _case_error(err):
        return jsonify(error=str(err)),400

    @app.errorhandler(KeyError)
    def _key_error(err):
        return jsonify(error=f"missing field {err}"),400

    def body():
        return request.get_json(silent=True) or {}

    def no_cache(res):
        # browsers revalidate (ETag/Last-Modified) on every load, so an
        # upgraded package is picked up without a hard reload
        res.headers["Cache-Control"] = "no-cache"
        return res

    @app.get("/")
    def index():
        return no_cache(send_from_directory(STATIC,"index.html"))

    @app.get("/static/<path:name>")
    def static_file(name):
        return no_cache(send_from_directory(STATIC,name))

    @app.get("/api/meta")
    def meta():
        from ppedit import __version__ # pylint: disable=import-outside-toplevel
        return jsonify(columns=COLUMNS,result_columns=RESULT_COLUMNS,tables=TABLES,
            builtin=builtin_cases(),
            formats=list(FORMATS.values()),input_formats=list(INPUT_FORMATS.values()),
            table_formats=table_formats(),solvers=solvers(),version=__version__,about=about(),
            instance=instance,token=token)

    @app.get("/api/ping")
    def ping():
        # no editor.lock: a long solve or load must not look like a stopped server
        return jsonify(instance=instance,revision=editor.revision)

    @app.get("/api/state")
    def state():
        with editor.lock:
            return jsonify(editor.state())

    @app.post("/api/new")
    def new():
        data = body()
        with editor.lock:
            if data.get("builtin"):
                editor.replace(Case.builtin(data["builtin"]),None)
            else:
                editor.replace(Case(name="untitled"),None)
            return jsonify(editor.state())

    @app.post("/api/open")
    def open_case():
        data = body()
        if "url" in data and "text" not in data: # download outside the lock
            case = download_case(data["url"])
            with editor.lock:
                editor.replace(case,None)
                return jsonify(editor.state())
        with editor.lock:
            if "text" in data:
                case = Case.load(data["filename"],data["text"])
                editor.replace(case,None)
            else:
                path = os.path.abspath(os.path.expanduser(data["path"]))
                if not os.path.isfile(path):
                    raise CaseError(f"'{path}' not found")
                editor.replace(Case.load(path),path)
            return jsonify(editor.state())

    @app.post("/api/global")
    def set_global():
        data = body()
        with editor.lock:
            editor.edit(lambda: editor.case.set_global(data["key"],data["value"]))
            return jsonify(editor.state())

    @app.post("/api/cell")
    def set_cell():
        data = body()
        with editor.lock:
            cascade = editor.edit(lambda: editor.case.set_value(
                data["table"],int(data["row"]),data["column"],data["value"]))
            return jsonify(editor.state(cascade))

    @app.post("/api/load")
    def toggle_load():
        data = body()
        with editor.lock:
            editor.toggle_load(int(data["bus"]))
            return jsonify(editor.state())

    @app.post("/api/rows")
    def add_row():
        data = body()
        with editor.lock:
            after = data.get("after")
            index = editor.edit(lambda: editor.case.add_row(data["table"],
                data.get("values"),None if after is None else int(after)))
            out = editor.state(True)
            out["row"] = index
            return jsonify(out)

    @app.post("/api/rows/delete")
    def delete_rows():
        data = body()
        with editor.lock:
            removed = editor.edit(lambda: editor.case.delete_rows(data["table"],
                [int(x) for x in data["rows"]],bool(data.get("cascade"))))
            out = editor.state(True)
            out["removed"] = removed
            return jsonify(out)

    @app.post("/api/table/import")
    def import_table():
        data = body()
        try:
            content = base64.b64decode(data["data"],validate=True)
        except (binascii.Error,TypeError) as err:
            raise CaseError("data must be the file contents in base64") from err
        columns,rows = read_rows(str(data["filename"]),content)
        replace = data.get("rows")
        with editor.lock:
            result = editor.edit(lambda: editor.case.import_rows(data["table"],columns,rows,
                None if not replace else [int(x) for x in replace]))
            out = editor.state(result["cascade"])
            out["imported"] = result
            return jsonify(out)

    @app.get("/api/table/export")
    def export_table():
        table = request.args.get("table","")
        fmt = request.args.get("format","csv").lstrip(".")
        text = request.args.get("rows","").strip()
        with editor.lock:
            if table not in COLUMNS:
                raise CaseError(f"unknown table '{table}'")
            rows = editor.case.tables[table]
            if text:
                try:
                    index = sorted({int(x) for x in text.split(",")})
                except ValueError as err:
                    raise CaseError("rows must be comma-separated row indexes") from err
                for i in index:
                    editor.case._check(table,i) # pylint: disable=protected-access
            else:
                index = list(range(len(rows)))
            rows = [rows[i] for i in index]
            name = f"{editor.case.name or 'untitled'}_{table}.{fmt}"
            content = dump_rows(table,rows,name,editor.extended(table,index))
        return Response(content,mimetype="application/octet-stream",headers={
            "Content-Disposition":f'attachment; filename="{name}"'})

    @app.post("/api/undo")
    def undo():
        with editor.lock:
            editor.undo()
            return jsonify(editor.state(True))

    @app.post("/api/redo")
    def redo():
        with editor.lock:
            editor.redo()
            return jsonify(editor.state(True))

    @app.post("/api/solve")
    def solve():
        data = body()
        with editor.lock:
            editor.result = editor.case.solve(data.get("kind","pf"),
                data.get("algorithm"),data.get("options"))
            return jsonify(editor.state())

    @app.post("/api/apply")
    def apply():
        with editor.lock:
            if not editor.result:
                raise CaseError("no solution to apply")
            result = editor.result
            editor.edit(lambda: editor.case.apply_result(result))
            return jsonify(editor.state(True))

    @app.post("/api/geodata")
    def geodata():
        data = body()
        with editor.lock:
            if data.get("clear"):
                editor.edit(lambda: editor.case.set_geodata([]))
            elif "text" in data:
                editor.edit(lambda: editor.case.load_geodata(data.get("filename","geodata.csv"),data["text"]))
            else:
                path = os.path.abspath(os.path.expanduser(data["path"]))
                if not os.path.isfile(path):
                    raise CaseError(f"'{path}' not found")
                editor.edit(lambda: editor.case.load_geodata(path))
            return jsonify(editor.state(True))

    @app.post("/api/save")
    def save():
        data = body()
        with editor.lock:
            path = data.get("path") or editor.path
            if not path:
                raise CaseError("no file name: use Save as or Download")
            path = os.path.abspath(os.path.expanduser(path))
            writable_format(path)
            if editor.case.notes and os.path.exists(path) and not data.get("force"):
                return jsonify(error="saving would drop content that ppedit does not edit ("
                    + "; ".join(editor.case.notes) + ")",confirm=True),409
            editor.case.save(path)
            editor.case.notes = []
            editor.path,editor.dirty = path,False
            return jsonify(editor.state())

    @app.post("/api/folders")
    def folders():
        data = body()
        with editor.lock:
            default = os.path.dirname(editor.path) if editor.path else os.getcwd()
        return jsonify(list_folder(data.get("path") or default))

    @app.post("/api/folder")
    def folder():
        data = body()
        path = os.path.abspath(os.path.expanduser(data["path"]))
        if not os.path.isdir(path):
            raise CaseError(f"'{path}' is not a folder")
        with editor.lock:
            name = os.path.basename(editor.path) if editor.path else f"{editor.case.name or 'untitled'}.glm"
            if name.lower().endswith(".raw"):
                name = name[:-4] + ".glm"
            new = os.path.join(path,name)
            if new != editor.path:
                editor.path,editor.dirty = new,True
            return jsonify(editor.state())

    @app.get("/api/export")
    def export():
        fmt = request.args.get("format","py")
        with editor.lock:
            text = editor.case.dumps(fmt)
            name = editor.case.name
        ext = {v:k for k,v in FORMATS.items()}.get(fmt,".csv")
        name = name + "_geodata" if fmt == "csv" else name
        mime = {"json":"application/json","csv":"text/csv"}.get(fmt,"text/plain")
        return Response(text,mimetype=mime,headers={
            "Content-Disposition":f'attachment; filename="{name}{ext}"'})

    def claude_state():
        with editor.lock:
            out = editor.state()
        out["claude"] = claude.to_dict()
        return jsonify(out)

    @app.get("/api/claude")
    def claude_list():
        return jsonify(claude=claude.to_dict())

    @app.post("/api/claude/new")
    def claude_new():
        data = body()
        claude.create(title=str(data.get("title") or ""),model=data.get("model"),
            web=bool(data.get("web")),local=bool(data.get("local")),root=data.get("root") or None)
        return claude_state()

    @app.post("/api/claude/send")
    def claude_send():
        data = body()
        claude.get(data["id"]).send(str(data.get("text","")))
        return claude_state()

    @app.post("/api/claude/stop")
    def claude_stop():
        claude.get(body()["id"]).stop()
        return claude_state()

    @app.post("/api/claude/delete")
    def claude_delete():
        claude.delete(body()["id"])
        return claude_state()

    @app.post("/api/exit")
    def exit_server():
        _logger.info("exit requested by the editor")
        threading.Timer(0.2,app.config["SHUTDOWN"]).start()
        return jsonify(exit=True)

    return app
