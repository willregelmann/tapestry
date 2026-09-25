"""The tapestry command.

    tapestry setup                     download and verify the embedding model
    tapestry hook                      handle one Claude Code hook (JSON on stdin)
    tapestry mcp                       run the MCP server on stdio
    tapestry ingest TRANSCRIPT         remember a Claude Code transcript
    tapestry recall QUERY [--cwd DIR]  recall from the command line
    tapestry import-mnemonic SRC DST   one-way import from mnemonic's store
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tapestry", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup")
    sub.add_parser("hook")
    sub.add_parser("mcp")
    ing = sub.add_parser("ingest")
    ing.add_argument("transcript")
    ing.add_argument("--cwd", default="")
    ing.add_argument("--session", default="")
    rec = sub.add_parser("recall")
    rec.add_argument("query")
    rec.add_argument("--cwd", default=".")
    rec.add_argument("-k", type=int, default=5)
    imp = sub.add_parser("import-mnemonic")
    imp.add_argument("args", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv)

    if args.cmd == "setup":
        from tapestry import embed
        print(f"model ready in {embed.fetch_model()}")
        from tapestry.hosts import claude_code as cc
        cc.open_mind().close()
        print(f"mind ready at {cc.mind_path()}")
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
    if args.cmd == "ingest":
        import datetime as dt
        from tapestry.hosts import claude_code as cc
        stats = cc.ingest(args.transcript, args.cwd or None, args.session or None)
        print(f"{dt.datetime.now().isoformat(timespec='seconds')} {args.transcript}: {stats}")
        return 0
    if args.cmd == "recall":
        from tapestry import render
        from tapestry.hosts import claude_code as cc
        mind = cc.open_mind(args.cwd)
        try:
            print(render.block(mind.recall(args.query, scopes=cc.loaded_scopes(args.cwd), k=args.k))
                  or "(nothing related found)")
        finally:
            mind.close()
        return 0
    if args.cmd == "import-mnemonic":
        from tapestry.importers import mnemonic
        return mnemonic.main(args.args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
