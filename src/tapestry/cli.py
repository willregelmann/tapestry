"""The tapestry command.

    tapestry setup                     download and verify the embedding model
    tapestry hook                      handle one Claude Code hook (JSON on stdin)
    tapestry mcp                       run the MCP server on stdio
    tapestry ingest TRANSCRIPT         add a Claude Code transcript to its session's episode
    tapestry recall QUERY [--cwd DIR]  recall from the command line
    tapestry view [--mind DB] [--detach]  look at a mind in the browser, without changing it
    tapestry import-mnemonic SRC DST   one-way import from mnemonic's store
    tapestry jobs [--mind DB --state DIR]  run the background jobs once
    tapestry judge-with jev|claude|none  choose the model that draws facts and checks conflicts
"""

from __future__ import annotations

import argparse
import os
import sys

from tapestry import judge


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tapestry", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup")
    sub.add_parser("hook")
    sub.add_parser("mcp")
    jb = sub.add_parser("jobs")
    jb.add_argument("--mind", default="", help="a mind other than the Claude Code user's, e.g. Hermes'")
    jb.add_argument("--state", default="", help="where that mind's failure ledgers live")
    jw = sub.add_parser("judge-with")
    jw.add_argument("which", choices=judge.CHOICES)
    ing = sub.add_parser("ingest")
    ing.add_argument("transcript")
    ing.add_argument("--cwd", default="")
    ing.add_argument("--session", default="")
    rec = sub.add_parser("recall")
    rec.add_argument("query")
    rec.add_argument("--cwd", default=".")
    rec.add_argument("-k", type=int, default=5)
    vw = sub.add_parser("view")
    vw.add_argument("--mind", default="", help="a mind other than the Claude Code user's, e.g. Hermes'")
    vw.add_argument("--state", default="", help="where that mind's failure ledgers live")
    vw.add_argument("--port", type=int, default=0)
    vw.add_argument("--no-browser", action="store_true", help="print the address without opening it")
    vw.add_argument("--detach", action="store_true",
                    help="serve in the background, stopping once unused for --idle seconds")
    vw.add_argument("--idle", type=float, default=0, help=argparse.SUPPRESS)
    imp = sub.add_parser("import-mnemonic")
    imp.add_argument("args", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv)

    if args.cmd == "setup":
        from tapestry import embed
        print(f"model ready in {embed.fetch_model()}")
        from tapestry.hosts import claude_code as cc
        cc.open_mind().close()
        print(f"mind ready at {cc.mind_path()}")
        how = ("/plugin configure tapestry@tapestry, then choose" if os.environ.get("CLAUDE_PLUGIN_DATA")
               else "tapestry judge-with")
        print("\nTapestry draws lasting facts from your conversations, and checks memories for conflicts, "
              "in the background, with a model you choose. Until you choose, nothing is sent anywhere "
              "(a Jev key in place counts as choosing Jev, for conflicts only).\n"
              f"  {how} claude: " + judge.DISCLOSURE["claude"] + "\n"
              f"  {how} jev: " + judge.DISCLOSURE["jev"] + "\n"
              f"  {how} none: " + judge.DISCLOSURE["none"])
        return 0
    if args.cmd == "hook":
        from tapestry.hosts import claude_code as cc
        out = cc.run_hook(sys.stdin.read())
        if out:
            print(out)
        return 0
    if args.cmd == "mcp":
        from tapestry.mcp_server import serve
        serve()
        return 0
    if args.cmd == "judge-with":
        from tapestry.hosts import claude_code as cc
        print(judge.choose(cc.home(), args.which))
        if os.environ.get(judge.PLUGIN_JUDGE):
            print(f"Note: the plugin's own setting ({os.environ[judge.PLUGIN_JUDGE]}) takes precedence "
                  "where it is set. Change it with /plugin configure tapestry@tapestry.")
        return 0
    if args.cmd == "jobs":
        import datetime as dt
        from pathlib import Path
        from tapestry import jobs, unrecorded
        from tapestry.hosts import claude_code as cc
        if args.mind:
            from tapestry import embed
            from tapestry.mind import Mind
            state = Path(args.state or Path(args.mind).parent)
            open_mind = lambda: Mind(args.mind, encode=embed.Encoder(), model_tag=embed.MODEL_TAG)
            behind = unrecorded.Ledger(state / "behind.jsonl")
        else:
            open_mind, behind = cc.open_mind, cc.behind()
        print(f"{dt.datetime.now().isoformat(timespec='seconds')} "
              + jobs.run(cc.home(), open_mind, behind))
        return 0
    if args.cmd == "ingest":
        import datetime as dt
        from tapestry.hosts import claude_code as cc
        try:
            stats = cc.ingest(args.transcript, args.cwd or None, args.session or None)
        except Exception as e:
            cc.ledger().add(f"recording {args.transcript} failed ({type(e).__name__}: {e})")
            raise
        print(f"{dt.datetime.now().isoformat(timespec='seconds')} {args.transcript}: {stats}")
        return 0
    if args.cmd == "recall":
        from tapestry import render
        from tapestry.hosts import claude_code as cc
        mind = cc.open_mind(args.cwd)
        try:
            print(render.block(mind.recall(args.query, topics=cc.open_topics(args.cwd), k=args.k))
                  or "(nothing related found)")
        finally:
            mind.close()
        return 0
    if args.cmd == "view" and args.detach:
        return _detach(args)
    if args.cmd == "view":
        import webbrowser
        from pathlib import Path
        from tapestry import embed, unrecorded, viewer
        from tapestry.hosts import claude_code as cc
        from tapestry.mind import Mind, Unreadable
        if args.mind:
            path = Path(args.mind)
            state = Path(args.state or path.parent)
            behind, gaps = unrecorded.Ledger(state / "behind.jsonl"), unrecorded.Ledger(path.with_name("unrecorded.jsonl"))
        else:
            path, behind, gaps = cc.mind_path(), cc.behind(), cc.ledger()
        try:
            mind = Mind.reading(path, encode=viewer.LazyEncoder(embed.Encoder), model_tag=embed.MODEL_TAG)
        except Unreadable as e:
            print(f"tapestry view: {e}", file=sys.stderr)
            return 1
        server = viewer.serve(viewer.Viewer(mind, notices=viewer.notices(behind, gaps, cc.home()), name=path.stem),
                              port=args.port)
        print(f"Viewing {path} (read-only) at\n  {server.url}", flush=True)
        if args.idle:
            # Started by --detach, which has what it needs: nothing more is said, to anyone.
            sys.stdout = sys.stderr = open(os.devnull, "w")
            viewer.stop_when_unused(server, args.idle)
        else:
            print("Ctrl-C to stop.", flush=True)
        if not args.no_browser:
            webbrowser.open(server.url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            mind.close()
        return 0
    if args.cmd == "import-mnemonic":
        from tapestry.importers import mnemonic
        return mnemonic.main(args.args)
    return 2


DETACHED_IDLE = 30 * 60


def _detach(args) -> int:
    """Start the viewer in its own process and say where it is, or why it couldn't start."""
    import subprocess
    import webbrowser
    cmd = [sys.executable, "-m", "tapestry.cli", "view", "--no-browser", "--idle", str(args.idle or DETACHED_IDLE),
           "--port", str(args.port), *(["--mind", args.mind] if args.mind else []),
           *(["--state", args.state] if args.state else [])]
    child = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, start_new_session=True)
    said = []
    for line in child.stdout:
        said.append(line.rstrip())
        if line.strip().startswith("http://"):
            break
    url = next((x.strip() for x in said if x.strip().startswith("http://")), None)
    if url is None:
        child.wait()
        print((child.stderr.read().strip() or "\n".join(said) or "the viewer stopped without saying why")
              .removeprefix("tapestry view: "))
        return 1
    child.stdout.close()
    child.stderr.close()
    print(f"{said[0]}\n  {url}\nIt stops by itself after {int((args.idle or DETACHED_IDLE) // 60)} minutes unused.")
    if not args.no_browser:
        webbrowser.open(url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
