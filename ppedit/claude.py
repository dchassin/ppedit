"""Claude assistant sessions

A `Sessions` object holds any number of conversations with Claude (the
Anthropic Messages API) that read and edit the case open in the editor.
Each session runs its own tool-use loop in a background thread; every
change Claude makes goes through the same `ppedit.case.Case` methods and
`ppedit.server.Editor.edit` as the browser, so it is checked, shows up in
the browser on its next refresh, and can be undone like any other edit.

Configuration
-------------

  - API key: the `ANTHROPIC_API_KEY` environment variable, or else the
    first line of `~/.anthropic`. Without a key the Claude tab says so and
    no session can be started.

  - Model: chosen per session from `MODELS`; the default is
    `PPEDIT_CLAUDE_MODEL` from the environment or `DEFAULT_MODEL`.

Data sources
------------

Every session can read and edit the case, validate it and run PF/OPF.
Two optional data sources are chosen when a session is created:

  - `web`: Anthropic's server-side web search tool and a `fetch_url` tool
    that downloads a public `http(s)` page or data file through the
    `ppedit` server (text, at most `FETCH_LIMIT` bytes).

  - `local`: `list_files` and `read_file` tools for files under one
    folder on the server machine (`root`, default: the folder of the open
    case file, or the current folder). Paths that resolve outside `root`
    are refused.

Geodata can be loaded from either source with `load_geodata` (CSV text
or a local CSV file).

Caveats
-------

  - Sessions live in server memory and are lost when the server stops.

  - Several sessions may edit the case at the same time; each tool call is
    one undoable edit, so concurrent sessions interleave their edits.

  - Tool results sent to Claude are cut to `RESULT_LIMIT` characters.
"""

import itertools
import json
import os
import threading
import time
import urllib.error
import urllib.request

from ppedit.case import COLUMNS, TABLES, CaseError

API_URL = "https://api.anthropic.com/v1/messages"
"""Anthropic Messages API endpoint"""

API_VERSION = "2023-06-01"
"""Value of the `anthropic-version` header"""

MODELS = ["claude-sonnet-5-5","claude-opus-5-5","claude-haiku-5-5"]
"""Models offered in the editor (first is the default)"""

DEFAULT_MODEL = os.environ.get("PPEDIT_CLAUDE_MODEL",MODELS[0])
"""Model used when a session does not choose one"""

MAX_TOKENS = 8192
"""Maximum tokens per Claude response"""

MAX_STEPS = 40
"""Maximum API calls in one turn (a user message and the tool use it causes)"""

RESULT_LIMIT = 30000
"""Maximum characters of one tool result sent to Claude"""

FETCH_LIMIT = 2000000
"""Maximum bytes read by `fetch_url`"""

READ_LIMIT = 2000000
"""Maximum bytes read by `read_file`"""

def api_key() -> str|None:
    """Return the Anthropic API key from the environment or `~/.anthropic`"""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key.strip()
    try:
        with open(os.path.expanduser("~/.anthropic"),encoding="utf-8") as fh:
            return fh.readline().strip() or None
    except OSError:
        return None

def http_client(payload:dict) -> dict:
    """Send one Messages API request and return the response

    Arguments
    ---------

      - `payload`: request body (`model`, `system`, `messages`, `tools`, ...)

    Returns
    -------

      - `dict`: the API response

    Raises `RuntimeError` with the API's error message on failure.
    """
    key = api_key()
    if not key:
        raise RuntimeError("no Anthropic API key (set ANTHROPIC_API_KEY or create ~/.anthropic)")
    req = urllib.request.Request(API_URL,data=json.dumps(payload).encode(),method="POST",
        headers={"x-api-key":key,"anthropic-version":API_VERSION,
            "content-type":"application/json"})
    try:
        with urllib.request.urlopen(req,timeout=600) as res:
            return json.loads(res.read())
    except urllib.error.HTTPError as err:
        try:
            msg = json.loads(err.read())["error"]["message"]
        except Exception: # pylint: disable=broad-exception-caught
            msg = str(err)
        raise RuntimeError(f"Claude API error {err.code}: {msg}") from err
    except urllib.error.URLError as err:
        raise RuntimeError(f"cannot reach the Claude API: {err.reason}") from err

# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------

_TABLE = {"type":"string","enum":TABLES}

CASE_TOOLS = [
    {"name":"get_case","description":"Summary of the open case: name, baseMVA, "
        "version, row count of each table, geodata row count, and load notes.",
        "input_schema":{"type":"object","properties":{}}},
    {"name":"get_table","description":"Rows of one table as objects keyed by "
        "column name, with their 0-based row index. Use start/count to page "
        "through large tables.",
        "input_schema":{"type":"object","properties":{"table":_TABLE,
            "start":{"type":"integer","minimum":0},"count":{"type":"integer","minimum":1}},
            "required":["table"]}},
    {"name":"set_cell","description":"Set one cell. Renumbering a bus renumbers "
        "its references. Returns whether other cells changed as a result.",
        "input_schema":{"type":"object","properties":{"table":_TABLE,
            "row":{"type":"integer","minimum":0},"column":{"type":"string"},
            "value":{"type":["number","string"]}},"required":["table","row","column","value"]}},
    {"name":"set_cells","description":"Set several cells as one undoable edit; "
        "all are rolled back if any fails.",
        "input_schema":{"type":"object","properties":{"edits":{"type":"array","items":{
            "type":"object","properties":{"table":_TABLE,"row":{"type":"integer","minimum":0},
                "column":{"type":"string"},"value":{"type":["number","string"]}},
            "required":["table","row","column","value"]}}},"required":["edits"]}},
    {"name":"add_rows","description":"Add rows to a table as one undoable edit. "
        "Each row is an object of column values (missing columns get defaults) "
        "or a full list in column order. Adding a gen or dcline also adds its "
        "cost row when the case has costs. Returns the new row indexes.",
        "input_schema":{"type":"object","properties":{"table":_TABLE,
            "rows":{"type":"array","items":{"type":["object","array"]}},
            "after":{"type":"integer","minimum":0,"description":"insert after this row (default: append)"}},
            "required":["table","rows"]}},
    {"name":"delete_rows","description":"Delete rows by 0-based index. Deleting "
        "buses that have connections needs cascade=true (connections are deleted too).",
        "input_schema":{"type":"object","properties":{"table":_TABLE,
            "rows":{"type":"array","items":{"type":"integer","minimum":0}},
            "cascade":{"type":"boolean"}},"required":["table","rows"]}},
    {"name":"set_global","description":"Set the case name, baseMVA or version.",
        "input_schema":{"type":"object","properties":{"key":{"type":"string",
            "enum":["name","baseMVA","version"]},"value":{"type":["number","string"]}},
            "required":["key","value"]}},
    {"name":"validate","description":"Check the case; returns the list of errors and warnings.",
        "input_schema":{"type":"object","properties":{}}},
    {"name":"solve","description":"Run a PYPOWER power flow (pf) or optimal power "
        "flow (opf, needs gencost). Returns a summary; the full result is shown "
        "in the editor's Results tab.",
        "input_schema":{"type":"object","properties":{"kind":{"type":"string","enum":["pf","opf"]},
            "algorithm":{"type":"string","enum":["NR","FDXB","FDBX","GS"]}}}},
    {"name":"apply_result","description":"Copy the last solution (voltages, "
        "generator outputs) into the case.",
        "input_schema":{"type":"object","properties":{}}},
    {"name":"load_geodata","description":"Replace the case geodata (bus "
        "locations) from CSV with columns BUS_I, LAT, LON (others kept as "
        "auxiliary data), given as text or as a local file path.",
        "input_schema":{"type":"object","properties":{"text":{"type":"string"},
            "path":{"type":"string"}}}},
]

WEB_TOOLS = [
    {"type":"web_search_20250305","name":"web_search","max_uses":10},
    {"name":"fetch_url","description":"Download a public http(s) page or data "
        "file (CSV, JSON, text, HTML) and return its text.",
        "input_schema":{"type":"object","properties":{"url":{"type":"string"}},"required":["url"]}},
]

LOCAL_TOOLS = [
    {"name":"list_files","description":"List files and folders in a folder under "
        "the local data folder (path relative to it; default: the folder itself).",
        "input_schema":{"type":"object","properties":{"path":{"type":"string"}}}},
    {"name":"read_file","description":"Read a text file under the local data folder "
        "(path relative to it). Use offset/limit (characters) for large files.",
        "input_schema":{"type":"object","properties":{"path":{"type":"string"},
            "offset":{"type":"integer","minimum":0},"limit":{"type":"integer","minimum":1}},
            "required":["path"]}},
]

def _columns_text():
    lines = []
    for table,cols in COLUMNS.items():
        lines.append(f"- {table}: " + ", ".join(
            c["name"] + (f" [{c['unit']}]" if c["unit"] else "")
            + (f" {{{', '.join(f'{k}={v}' for k,v in c['options'].items())}}}" if c["options"] else "")
            for c in cols))
    return "\n".join(lines)

SYSTEM = """You are working inside ppedit, a browser editor for PYPOWER power \
system cases (also Arras Energy GridLAB-D `module pypower` models). You read \
and change the case open in the editor only through the tools provided; the \
user sees every change immediately in the editor and can undo each tool call.

Tables and columns (PYPOWER caseformat; row indexes are 0-based):
{columns}

Conventions:
- gencost and dclinecost rows follow gen and dcline row by row; costs is a \
comma-separated string of coefficients (polynomial, highest order first) or \
x,y pairs (piecewise).
- areas rows are [area, price_ref_bus]; bus.area holds each bus's area.
- Infinite limits are the strings "Inf" and "-Inf".
- Enum columns accept the code or the keyword shown in braces.

Work carefully: read the tables you need before editing, prefer one \
set_cells or add_rows call for related changes, validate after editing, and \
tell the user briefly what you changed and why. When you use outside data, \
say where it came from.{sources}"""

def _system(web,local,root):
    sources = []
    if web:
        sources.append("You may search the web and fetch public data with web_search and fetch_url.")
    if local:
        sources.append(f"You may read local files under the data folder {root} with list_files and read_file.")
    if not sources:
        sources.append("No outside data sources are enabled in this session; use only the case and what the user tells you.")
    return SYSTEM.format(columns=_columns_text(),sources="\n\n"+" ".join(sources))

def _row_dict(table,row):
    return {c["name"]:v for c,v in zip(COLUMNS[table],row)}

def _clip(text):
    if len(text) <= RESULT_LIMIT:
        return text
    return text[:RESULT_LIMIT] + f"\n... [cut: {len(text)-RESULT_LIMIT} more characters]"

# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------

class Session:
    """One conversation with Claude about the open case

    Arguments
    ---------

      - `sid`: session id

      - `editor`: the `ppedit.server.Editor` being edited

      - `client`: function sending one API request (see `http_client`)

      - `title`: display title

      - `model`: Claude model name

      - `web`: enable the web data source

      - `local`: enable the local files data source

      - `root`: local data folder
    """

    def __init__(self,sid,editor,client,title="",model=None,web=False,local=False,root=None):
        self.id = sid
        self.editor = editor
        self.client = client
        self.title = title or f"Session {sid}"
        self.model = model or DEFAULT_MODEL
        self.web = bool(web)
        self.local = bool(local)
        self.root = os.path.realpath(os.path.expanduser(root)) if root else None
        if self.local and (not self.root or not os.path.isdir(self.root)):
            raise CaseError(f"local data folder '{root}' not found")
        self.messages = []
        """Conversation sent to the API"""
        self.transcript = []
        """Items shown in the editor: `{"kind": "user"|"assistant"|"tool"|"error", ...}`"""
        self.status = "idle"
        """`idle`, `running`, or `stopping`"""
        self.edits = 0
        """Number of tool calls that changed the case"""
        self.usage = {"input_tokens":0,"output_tokens":0}
        self.created = time.time()
        self._stop = False
        self._thread = None
        self._lock = threading.Lock()

    def summary(self) -> dict:
        """Return the session for the browser"""
        with self._lock:
            return {"id":self.id,"title":self.title,"model":self.model,
                "web":self.web,"local":self.local,"root":self.root,
                "status":self.status,"edits":self.edits,"usage":dict(self.usage),
                "transcript":list(self.transcript)}

    def _add(self,**item):
        with self._lock:
            item["time"] = time.time()
            self.transcript.append(item)

    def send(self,text:str):
        """Add a user message and start working on it in the background"""
        text = (text or "").strip()
        if not text:
            raise CaseError("empty message")
        with self._lock:
            if self.status != "idle":
                raise CaseError("this session is still working")
            self.status = "running"
            self._stop = False
        self.messages.append({"role":"user","content":[{"type":"text","text":text}]})
        self._add(kind="user",text=text)
        self._thread = threading.Thread(target=self._run,daemon=True)
        self._thread.start()

    def stop(self):
        """Ask the session to stop after the current step"""
        with self._lock:
            if self.status == "running":
                self.status = "stopping"
                self._stop = True

    def wait(self,timeout:float|None=None):
        """Wait for the background work to finish (for tests and scripts)"""
        if self._thread:
            self._thread.join(timeout)

    def _tools(self):
        return CASE_TOOLS + (WEB_TOOLS if self.web else []) + (LOCAL_TOOLS if self.local else [])

    def _run(self):
        try:
            for _ in range(MAX_STEPS):
                if self._stop:
                    self._add(kind="error",text="stopped")
                    break
                res = self.client({"model":self.model,"max_tokens":MAX_TOKENS,
                    "system":[{"type":"text","text":_system(self.web,self.local,self.root),
                        "cache_control":{"type":"ephemeral"}}],
                    "tools":self._tools(),"messages":self.messages})
                usage = res.get("usage") or {}
                with self._lock:
                    for k in self.usage:
                        self.usage[k] += int(usage.get(k) or 0)
                content = res.get("content") or []
                self.messages.append({"role":"assistant","content":content})
                results = []
                for block in content:
                    kind = block.get("type")
                    if kind == "text" and block.get("text","").strip():
                        self._add(kind="assistant",text=block["text"])
                    elif kind == "server_tool_use":
                        self._add(kind="tool",name=block.get("name"),input=block.get("input"),
                            result="(searched by Claude)",ok=True)
                    elif kind == "tool_use":
                        results.append(self._call(block))
                stop = res.get("stop_reason")
                if results:
                    self.messages.append({"role":"user","content":results})
                    continue
                if stop == "pause_turn":
                    continue
                if stop == "max_tokens":
                    self._add(kind="error",text="response cut off (max_tokens)")
                break
            else:
                self._add(kind="error",text=f"stopped after {MAX_STEPS} steps")
        except Exception as err: # pylint: disable=broad-exception-caught
            self._add(kind="error",text=str(err))
            # keep the conversation valid for the next message
            if self.messages and self.messages[-1]["role"] == "user" \
                    and any(b.get("type") == "tool_result" for b in self.messages[-1]["content"]):
                self.messages.append({"role":"assistant","content":[{"type":"text","text":"(interrupted)"}]})
        finally:
            if self.messages and self.messages[-1]["role"] == "assistant" \
                    and any(b.get("type") == "tool_use" for b in self.messages[-1]["content"]):
                # stopped before the tool results were sent
                self.messages.append({"role":"user","content":[{"type":"tool_result",
                    "tool_use_id":b["id"],"content":"not run (stopped)","is_error":True}
                    for b in self.messages[-1]["content"] if b.get("type") == "tool_use"]})
                self.messages.append({"role":"assistant","content":[{"type":"text","text":"(stopped)"}]})
            with self._lock:
                self.status = "idle"

    def _call(self,block):
        name,args = block.get("name"),block.get("input") or {}
        try:
            out,changed = self.run_tool(name,args)
            ok = True
        except Exception as err: # pylint: disable=broad-exception-caught
            out,changed,ok = f"error: {err}",False,False
        text = out if isinstance(out,str) else json.dumps(out,default=str)
        if changed:
            with self._lock:
                self.edits += 1
        self._add(kind="tool",name=name,input=args,result=_clip(text)[:2000],ok=ok,changed=changed)
        return {"type":"tool_result","tool_use_id":block["id"],"content":_clip(text),
            **({} if ok else {"is_error":True})}

    def _edit(self,func):
        editor = self.editor
        with editor.lock:
            before = editor.revision
            out = editor.edit(func)
            return out,editor.revision != before

    def run_tool(self,name:str,args:dict):
        """Run one tool call; return `(result, changed)`"""
        # pylint: disable=too-many-return-statements,too-many-branches
        editor = self.editor
        if name == "get_case":
            with editor.lock:
                case = editor.case
                return {"name":case.name,"baseMVA":case.baseMVA,"version":case.version,
                    "rows":{t:len(r) for t,r in case.tables.items()},
                    "geodata_rows":len(case.geodata),"notes":case.notes,
                    "file":editor.path,"unsaved_changes":editor.dirty},False
        if name == "get_table":
            table = args["table"]
            if table not in COLUMNS:
                raise CaseError(f"unknown table '{table}'")
            start = int(args.get("start") or 0)
            count = int(args.get("count") or 200)
            with editor.lock:
                rows = editor.case.tables[table]
                return {"table":table,"total":len(rows),"start":start,
                    "rows":[{"row":i,**_row_dict(table,r)}
                        for i,r in enumerate(rows[start:start+count],start)]},False
        if name == "set_cell":
            cascade,changed = self._edit(lambda: editor.case.set_value(
                args["table"],int(args["row"]),args["column"],args["value"]))
            return {"ok":True,"other_cells_changed":bool(cascade)},changed
        if name == "set_cells":
            def apply():
                for e in args["edits"]:
                    editor.case.set_value(e["table"],int(e["row"]),e["column"],e["value"])
                return len(args["edits"])
            n,changed = self._edit(apply)
            return {"ok":True,"cells":n},changed
        if name == "add_rows":
            def add():
                after = args.get("after")
                out = []
                for values in args["rows"]:
                    out.append(editor.case.add_row(args["table"],values,after))
                    if after is not None:
                        after = out[-1]
                return out
            rows,changed = self._edit(add)
            return {"ok":True,"rows":rows},changed
        if name == "delete_rows":
            removed,changed = self._edit(lambda: editor.case.delete_rows(args["table"],
                [int(x) for x in args["rows"]],bool(args.get("cascade"))))
            return {"ok":True,"removed":removed},changed
        if name == "set_global":
            _,changed = self._edit(lambda: editor.case.set_global(args["key"],args["value"]))
            return {"ok":True},changed
        if name == "validate":
            with editor.lock:
                issues = editor.case.validate()
            return {"issues":issues} if issues else "no issues found",False
        if name == "solve":
            with editor.lock:
                result = editor.case.solve(args.get("kind","pf"),args.get("algorithm","NR"))
                editor.result = result
            return {k:result.get(k) for k in ("success","kind","iterations","objective",
                "losses_MW","areas")} | {"log":(result.get("log") or "")[-6000:]},False
        if name == "apply_result":
            def apply_result():
                if not editor.result:
                    raise CaseError("no solution to apply")
                editor.case.apply_result(editor.result)
            _,changed = self._edit(apply_result)
            return {"ok":True},changed
        if name == "load_geodata":
            if args.get("text"):
                n,changed = self._edit(lambda: editor.case.load_geodata("geodata.csv",args["text"]))
            elif args.get("path"):
                path = self._local(args["path"])
                n,changed = self._edit(lambda: editor.case.load_geodata(path))
            else:
                raise CaseError("give text or path")
            return {"ok":True,"rows":n},changed
        if name == "fetch_url" and self.web:
            return _fetch(args["url"]),False
        if name == "list_files" and self.local:
            path = self._local(args.get("path") or ".")
            return [e.name + ("/" if e.is_dir() else "") for e in sorted(os.scandir(path),
                key=lambda e: e.name) if not e.name.startswith(".")][:1000],False
        if name == "read_file" and self.local:
            path = self._local(args["path"])
            with open(path,encoding="utf-8",errors="replace") as fh:
                text = fh.read(READ_LIMIT)
            offset = int(args.get("offset") or 0)
            limit = int(args.get("limit") or RESULT_LIMIT)
            return {"path":args["path"],"size":len(text),"offset":offset,
                "text":text[offset:offset+limit]},False
        raise CaseError(f"tool '{name}' is not available in this session")

    def _local(self,path):
        """Resolve a path under the local data folder"""
        if not self.local:
            raise CaseError("local files are not enabled in this session")
        full = os.path.realpath(os.path.join(self.root,os.path.expanduser(path)))
        if os.path.commonpath([full,self.root]) != self.root:
            raise CaseError(f"'{path}' is outside the local data folder")
        if not os.path.exists(full):
            raise CaseError(f"'{path}' not found")
        return full

def _fetch(url):
    if not url.lower().startswith(("http://","https://")):
        raise CaseError("only http and https URLs can be fetched")
    req = urllib.request.Request(url,headers={"User-Agent":"ppedit"})
    try:
        with urllib.request.urlopen(req,timeout=60) as res:
            data = res.read(FETCH_LIMIT+1)
            ctype = res.headers.get("Content-Type","")
    except urllib.error.HTTPError as err:
        raise CaseError(f"HTTP {err.code} from {url}") from err
    except urllib.error.URLError as err:
        raise CaseError(f"cannot fetch {url}: {err.reason}") from err
    cut = len(data) > FETCH_LIMIT
    text = data[:FETCH_LIMIT].decode("utf-8",errors="replace")
    return {"url":url,"content_type":ctype,"truncated":cut,"text":text}

class Sessions:
    """All Claude sessions of one editor

    Arguments
    ---------

      - `editor`: the `ppedit.server.Editor` the sessions edit

      - `client`: function sending one API request (default `http_client`);
        tests pass a fake
    """

    def __init__(self,editor,client=None):
        self.editor = editor
        self.client = client or http_client
        self.items = {}
        self._ids = itertools.count(1)
        self.lock = threading.Lock()

    def available(self) -> bool:
        """Whether sessions can reach Claude (an API key is set, or a test client)"""
        return self.client is not http_client or bool(api_key())

    def default_root(self) -> str:
        """Default local data folder: the open case's folder, else the current folder"""
        return os.path.dirname(self.editor.path) if self.editor.path else os.getcwd()

    def create(self,**options) -> Session:
        """Start a new session (see `Session` for the options)"""
        if not self.available():
            raise CaseError("no Anthropic API key (set ANTHROPIC_API_KEY or create ~/.anthropic)")
        model = options.get("model") or DEFAULT_MODEL
        if options.get("local") and not options.get("root"):
            options["root"] = self.default_root()
        with self.lock:
            sid = next(self._ids)
            session = Session(sid,self.editor,self.client,options.get("title",""),model,
                options.get("web"),options.get("local"),options.get("root"))
            self.items[sid] = session
        return session

    def get(self,sid) -> Session:
        """Return a session by id"""
        try:
            return self.items[int(sid)]
        except (KeyError,TypeError,ValueError) as err:
            raise CaseError(f"no Claude session {sid}") from err

    def delete(self,sid):
        """Stop and forget a session"""
        session = self.get(sid)
        session.stop()
        with self.lock:
            del self.items[session.id]

    def to_dict(self) -> dict:
        """Return all sessions for the browser"""
        with self.lock:
            items = list(self.items.values())
        return {"available":self.available(),"models":MODELS,"default_model":DEFAULT_MODEL,
            "default_root":self.default_root(),"revision":self.editor.revision,
            "sessions":[s.summary() for s in items]}
