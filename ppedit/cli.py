"""Command line interface

See `ppedit` for the command syntax.

Examples
--------

    ppedit convert case14.py case14.glm
    ppedit check case14.glm
    ppedit serve case14.glm

`check` outputs `case14.glm: no issues found`; `serve` prints the editor URL
and opens it in a browser.
"""

import os
import sys
import argparse
import logging
import threading
import warnings
import webbrowser

_logger = logging.getLogger(__file__)

def main(argv:list[str]|None=None) -> int:
    """Command line entry point

    Arguments
    ---------

      - `argv`: command line arguments (default `sys.argv[1:]`)

    Returns
    -------

      - `int`: exit code (0 on success, 1 on error or validation errors)
    """
    parser = argparse.ArgumentParser(prog="ppedit",
        description="Browser-based editor for PYPOWER and module pypower models",
        epilog="See https://dchassin.github.io/ppedit for full documentation.")
    parser.add_argument("command",choices=["serve","convert","check"])
    parser.add_argument("files",nargs="*",help="case file(s): .py, .json, or .glm")
    parser.add_argument("-p","--port",type=int,default=8050,help="server port (default 8050)")
    parser.add_argument("--host",default="127.0.0.1",help="interface to listen on (default 127.0.0.1)")
    parser.add_argument("--no-browser",action="store_true",help="do not open a browser")
    parser.add_argument("-d","--debug",action="store_true",help="enable debug logging and traceback on exceptions")
    parser.add_argument("-w","--warning",action="store_true",help="enable warning messages from python")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING)
    if not args.warning:
        warnings.simplefilter("ignore")

    try:
        from ppedit.case import Case # pylint: disable=import-outside-toplevel
        if args.command == "convert":
            if len(args.files) != 2:
                raise ValueError("convert needs INPUT and OUTPUT files")
            case = Case.load(args.files[0])
            for note in case.notes:
                print(f"WARNING [ppedit]: {note}",file=sys.stderr)
            case.save(args.files[1])
            return 0
        if args.command == "check":
            if len(args.files) != 1:
                raise ValueError("check needs one FILE")
            issues = Case.load(args.files[0]).validate()
            for issue in issues:
                where = issue["table"] + (f" row {issue['row']+1}" if issue["row"] is not None else "")
                print(f"{issue['level'].upper()} [{where}]: {issue['message']}")
            if not issues:
                print(f"{args.files[0]}: no issues found")
            return 1 if any(x["level"] == "error" for x in issues) else 0
        # serve
        from ppedit.server import create_app # pylint: disable=import-outside-toplevel
        if len(args.files) > 1:
            raise ValueError("serve takes at most one FILE")
        case,path = None,None
        if args.files:
            path = os.path.abspath(args.files[0])
            if os.path.exists(path):
                case = Case.load(path)
            else:
                case = Case(name=os.path.splitext(os.path.basename(path))[0])
        app = create_app(case,path,allow_remote=args.host not in ("127.0.0.1","localhost","::1"))
        url = f"http://{'localhost' if args.host in ('127.0.0.1','0.0.0.0') else args.host}:{args.port}/"
        print(f"ppedit: editing {path or 'new case'} at {url} (Ctrl-C to stop)")
        if not args.no_browser:
            threading.Timer(1.0,lambda: webbrowser.open(url)).start()
        app.run(host=args.host,port=args.port,debug=False,use_reloader=False)
        return 0
    except Exception as err: # pylint: disable=broad-exception-caught
        if args.debug:
            raise
        print(f"ERROR [ppedit]: {err}",file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
