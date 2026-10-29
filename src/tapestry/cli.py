"""The tapestry command.

    tapestry setup                     download and verify the embedding model
    tapestry hook                      handle one Claude Code hook (JSON on stdin)
    tapestry mcp                       run the MCP server on stdio
    tapestry ingest TRANSCRIPT         add a Claude Code transcript to its session's episode
    tapestry recall QUERY [--cwd DIR]  recall from the command line
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
    jb.add_argument("--state", default="", help="where that mind's activity and failure ledgers live")
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
            behind, activity = unrecorded.Ledger(state / "behind.jsonl"), jobs.Activity(state / "active")
        else:
            open_mind, behind, activity = cc.open_mind, cc.behind(), cc.activity()
        print(f"{dt.datetime.now().isoformat(timespec='seconds')} "
              + jobs.run(cc.home(), open_mind, behind, activity))
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
    if args.cmd == "import-mnemonic":
        from tapestry.importers import mnemonic
        return mnemonic.main(args.args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
