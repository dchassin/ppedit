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

Every successful call returns the editor state: `case` (see
`ppedit.Case.to_dict`), `path`, `format`, `dirty`, `issues`, `can_undo`,
`can_redo`, `result` (last solution or `null`), `cascade` (whether the
last edit changed cells other than the one edited), and `revision` (a
counter that changes whenever the case changes). Errors return
`{"error": message}` with status 400.

  - `GET  /api/meta`: columns, tables, built-in case names, formats, version
  - `GET  /api/state`: current state
  - `POST /api/new`: `{}` empty case or `{"builtin": "case14"}`
  - `POST /api/open`: `{"filename": ..., "text": ...}` (upload) or
    `{"path": ...}` (server file)
  - `POST /api/global`: `{"key": "baseMVA"|"version"|"name", "value": ...}`
  - `POST /api/cell`: `{"table", "row", "column", "value"}`
  - `POST /api/rows`: `{"table", "values"?, "after"?}` add a row
  - `POST /api/rows/delete`: `{"table", "rows": [...], "cascade"?}`
  - `POST /api/undo`, `POST /api/redo`
  - `POST /api/solve`: `{"kind": "pf"|"opf", "algorithm": "NR"|...}`
  - `POST /api/apply`: copy the last solution into the case
  - `POST /api/geodata`: `{"filename": ..., "text": ...}` (CSV upload),
    `{"path": ...}` (server CSV file), or `{"clear": true}`; replaces the
    case geodata (see `ppedit.Case.load_geodata`) as one undoable edit
  - `POST /api/save`: `{"path"?, "force"?}` write to the open file or to
    `path`; when the case was read from a GLM file with content ppedit does
    not edit (`case.notes`), overwriting an existing file returns status 409
    with `"confirm": true` unless `force` is true
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

import copy
import logging
import os
import signal
import threading

from flask import Flask, jsonify, request, send_from_directory, Response

from ppedit.case import Case, CaseError, COLUMNS, TABLES, FORMATS, builtin_cases
from ppedit.claude import Sessions

_logger = logging.getLogger(__file__)

STATIC = os.path.join(os.path.dirname(__file__),"static")
"""Folder of the browser editor files"""

HISTORY = 200
"""Number of undo steps kept"""

_LOOPBACK = {"localhost","127.0.0.1","::1","[::1]"}

def _shutdown():
    """Stop the server process as Ctrl-C would, or exit if `SIGINT` is ignored"""
    if signal.getsignal(signal.SIGINT) in (signal.SIG_IGN,None):
        os._exit(0) # e.g. started in the background, where SIGINT is ignored
    signal.raise_signal(signal.SIGINT)

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
        self.lock = threading.RLock()

    def _snapshot(self):
        return (copy.deepcopy(self.case.tables),self.case.baseMVA,
            self.case.version,self.case.name,
            copy.deepcopy(self.case.geodata),list(self.case.geotags))

    def _restore(self,snap):
        tables,base,version,name,geodata,geotags = snap
        self.case.tables = copy.deepcopy(tables)
        self.case.baseMVA,self.case.version,self.case.name = base,version,name
        self.case.geodata,self.case.geotags = copy.deepcopy(geodata),list(geotags)

    def edit(self,func):
        """Run `func()` as one undoable edit; roll back if it raises"""
        snap = self._snapshot()
        try:
            out = func()
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

    def state(self,cascade:bool=False) -> dict:
        """Return the editor state sent to the browser"""
        fmt = FORMATS.get(os.path.splitext(self.path)[1].lower()) if self.path else None
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
        }

def create_app(case:Case|None=None,path:str|None=None,allow_remote:bool=False,
        shutdown=None,claude_client=None) -> Flask:
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

    @app.before_request
    def _guard():
        host = (request.host or "").rsplit(":",1)[0] if not (request.host or "").startswith("[") \
            else request.host.split("]")[0]+"]"
        if not allow_remote and host not in _LOOPBACK:
            return jsonify(error="remote access is disabled"),403
        if request.method == "POST":
            if request.headers.get("X-PPEdit") != "1" or not request.is_json:
                return jsonify(error="missing X-PPEdit header or JSON body"),403
        return None

    @app.errorhandler(CaseError)
    def _case_error(err):
        return jsonify(error=str(err)),400

    @app.errorhandler(KeyError)
    def _key_error(err):
        return jsonify(error=f"missing field {err}"),400

    def body():
        return request.get_json(silent=True) or {}

    @app.get("/")
    def index():
        return send_from_directory(STATIC,"index.html")

    @app.get("/static/<path:name>")
    def static_file(name):
        return send_from_directory(STATIC,name)

    @app.get("/api/meta")
    def meta():
        from ppedit import __version__ # pylint: disable=import-outside-toplevel
        return jsonify(columns=COLUMNS,tables=TABLES,builtin=builtin_cases(),
            formats=list(FORMATS.values()),version=__version__)

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
                data.get("algorithm","NR"))
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
            if editor.case.notes and os.path.exists(path) and not data.get("force"):
                return jsonify(error="saving would drop content that ppedit does not edit ("
                    + "; ".join(editor.case.notes) + ")",confirm=True),409
            editor.case.save(path)
            editor.case.notes = []
            editor.path,editor.dirty = path,False
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
