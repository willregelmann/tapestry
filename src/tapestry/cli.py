"""The tapestry command.

    tapestry setup                     download and verify the embedding model
    tapestry hook                      handle one Claude Code hook (JSON on stdin)
    tapestry mcp                       run the MCP server on stdio
    tapestry ingest TRANSCRIPT         add a Claude Code transcript to its session's episode
    tapestry recall QUERY [--cwd DIR]  recall from the command line
    tapestry view [--mind DB] [--detach]  look at a mind in the browser, without changing it
    tapestry import-mnemonic SRC DST   one-way import from mnemonic's store
    tapestry jobs [--mind DB --state DIR]  run the background jobs once
    tapestry judge-with jev|claude|claude-api|none  choose the model that draws facts
    tapestry connect URL --token TOKEN  reach this machine's mind on a Tapestry server
    tapestry disconnect                use the mind on this machine again
    tapestry server run [--port N]     serve minds and the viewer (server mind add | key add | owner add | ...)
    tapestry sample [--mind DB] [-n N] [--report]  rate recent highlights: relevant, and still true?
    tapestry redraw (CONVERSATION... | --all) [--mind DB] [--yes]
                                       take back what was drawn from conversations, to draw them again
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
    ing.add_argument("--end", action="store_true", help="end the session's episode once it is recorded")
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
    rd = sub.add_parser("redraw")
    rd.add_argument("conversations", nargs="*", help="the session ids of the conversations")
    rd.add_argument("--all", action="store_true", help="every kept conversation anything was drawn from")
    rd.add_argument("--mind", default="", help="a mind other than the Claude Code user's, e.g. Hermes'")
    rd.add_argument("--state", default="", help="where that mind's background work keeps its lock")
    rd.add_argument("--yes", action="store_true", help="don't ask before taking anything back")
    cn = sub.add_parser("connect", help="reach this machine's mind on a Tapestry server")
    cn.add_argument("url")
    cn.add_argument("--token", required=True)
    cn.add_argument("--hermes", nargs="?", const="", default=None, metavar="HERMES_HOME",
                    help="connect Hermes' Tapestry instead of Claude Code's (default $HERMES_HOME or ~/.hermes)")
    dc = sub.add_parser("disconnect", help="use the mind on this machine again")
    dc.add_argument("--hermes", nargs="?", const="", default=None, metavar="HERMES_HOME")
    sv = sub.add_parser("server", help="serve minds to other machines")
    sv.add_argument("--home", default="", help="where the server keeps its minds and keys")
    svs = sv.add_subparsers(dest="action", required=True)
    run = svs.add_parser("run")
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, default=8650)
    ma = svs.add_parser("mind")
    ma.add_argument("verb", choices=["add", "list"])
    ma.add_argument("name", nargs="?")
    ma.add_argument("--path", default="", help="an existing mind file to serve, instead of a new one")
    oa = svs.add_parser("owner", help="sign-in tokens for the viewer, which reaches every mind here")
    oa.add_argument("verb", choices=["add", "list", "revoke"])
    oa.add_argument("id", nargs="?", help="the owner token to revoke")
    ka = svs.add_parser("key")
    ka.add_argument("verb", choices=["add", "list", "revoke"])
    ka.add_argument("id", nargs="?", help="the key to revoke")
    ka.add_argument("--mind", default="")
    ka.add_argument("--agent", default="")
    ka.add_argument("--machine", default="")
    ka.add_argument("--read-only", action="store_true")
    sm = sub.add_parser("sample", help="rate a few recent highlights: relevant, and still true?")
    sm.add_argument("--mind", default="", help="a mind other than the Claude Code user's, e.g. Hermes'")
    sm.add_argument("--state", default="", help="where that mind's ratings are kept")
    sm.add_argument("-n", type=int, default=10, help="how many highlights to rate")
    sm.add_argument("--days", type=float, default=7.0, help="how far back to look, and to report")
    sm.add_argument("--report", action="store_true", help="only report precision so far")
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
            stats = cc.ingest(args.transcript, args.cwd or None, args.session or None, end=args.end)
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
    if args.cmd == "redraw":
        return _redraw(args)
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
    if args.cmd in ("connect", "disconnect"):
        from tapestry import remote
        from pathlib import Path
        from tapestry.hosts import claude_code as cc
        if args.hermes is None:
            home, who_uses = cc.home(), "Sessions on this machine"
        else:
            home = Path(args.hermes or os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser() / "tapestry"
            who_uses = "Hermes on this machine"
        if args.cmd == "disconnect":
            held = remote.Outbox(home / "outbox.jsonl").waiting()
            print(f"Disconnected: {who_uses} will use the mind kept on this machine."
                  if remote.disconnect(home) else "Not connected to a server; nothing changed.")
            if held:
                print(f"{held} turns held for the server weren't sent. Connect again to send them.")
            return 0
        try:
            who = remote.connect(home, args.url, args.token)
        except remote.Unreachable as e:
            print(f"Not connected: {e}", file=sys.stderr)
            return 1
        print(f"Connected: {who_uses} will reach the mind {who['mind']!r} on {args.url}, "
              f"as {who['agent']} on {who['machine']} (key {who['id']}, {who['access']}).")
        if args.hermes is not None:
            print("Restart the Hermes gateway for it to take effect.")
        return 0
    if args.cmd == "server":
        return _server(args)
    if args.cmd == "sample":
        from pathlib import Path
        from tapestry import sample
        from tapestry.hosts import claude_code as cc
        from tapestry.mind import Mind
        path = Path(args.mind) if args.mind else cc.mind_path()
        log = Path(args.state or (path.parent if args.mind else cc.home() / "state")) / "samples.jsonl"
        if not args.report:
            mind = Mind.reading(path, encode=lambda texts: None, model_tag="unused")
            try:
                items = sample.pick(mind, args.n, days=args.days)
            finally:
                mind.close()
            if not items:
                print(f"No highlights were recalled in the last {args.days:g} days.")
            else:
                sample.rate(items, input, log)
        print(sample.report(log, days=args.days))
        return 0
    if args.cmd == "import-mnemonic":
        from tapestry.importers import mnemonic
        return mnemonic.main(args.args)
    return 2


DETACHED_IDLE = 30 * 60


def _server(args) -> int:
    from pathlib import Path
    from tapestry import server
    home = Path(args.home or os.environ.get("TAPESTRY_SERVER_HOME") or Path.home() / ".tapestry-server")
    reg = server.Registry(home)
    if args.action == "mind":
        if args.verb == "list":
            for name, path in reg.minds().items():
                print(f"{name}\t{path}")
            return 0
        if not args.name:
            print("Name the mind: tapestry server mind add NAME [--path FILE]", file=sys.stderr)
            return 2
        print(f"Serving the mind {args.name!r} from {reg.add_mind(args.name, args.path or None)}")
        return 0
    if args.action == "owner":
        if args.verb == "list":
            for o in reg.owners():
                print(f"{o['id']}\towner\t{'revoked' if o['revoked_at'] else 'live'}")
            return 0
        if args.verb == "revoke":
            if not (args.id or "").startswith("o"):
                print("Name the owner token to revoke, such as o1: tapestry server owner list", file=sys.stderr)
                return 2
            print(f"Revoked {reg.revoke(args.id)['id']}.")
            return 0
        o, token = reg.add_owner()
        print(f"Owner token {o['id']}, shown this once (the server keeps only its hash). It opens the viewer,\n"
              f"which can read and change every mind here, so keep it like a password:\n\n  {token}\n\n"
              f"Open <this server's address>/#t={token}, or open the address and paste it.")
        return 0
    if args.action == "key":
        if args.verb == "list":
            for k in reg.keys():
                print(f"{k['id']}\t{k['mind']}\t{k['agent']} on {k['machine']}\t{k['access']}"
                      f"\t{'revoked' if k['revoked_at'] else 'live'}")
            return 0
        if args.verb == "revoke":
            k = reg.revoke(args.id or "")
            print(f"Revoked {k['id']} ({k['agent']} on {k['machine']}). What it wrote stays in {k['mind']!r}.")
            return 0
        if not (args.mind and args.agent and args.machine):
            print("A key names one mind, one agent and one machine: --mind, --agent and --machine.", file=sys.stderr)
            return 2
        key, token = reg.add_key(mind=args.mind, agent=args.agent, machine=args.machine, read_only=args.read_only)
        print(f"Key {key['id']}: {key['agent']} on {key['machine']} may {key['access']} {key['mind']!r}.\n"
              f"Its token, shown this once (the server keeps only its hash):\n\n  {token}\n\n"
              f"On that machine: tapestry connect <this server's address> --token {token}")
        return 0
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    app = server.Server(home)
    httpd = server.http_server(app, args.host, args.port)
    print(f"Serving {len(reg.minds())} minds from {home} on http://{args.host}:{args.port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
    return 0


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


def _redraw(args) -> int:
    """Take back what was drawn from conversations so the background draws them again
    (docs/interfaces/COMMANDS.md). Waits for any background run to finish, so it never takes
    back a conversation halfway through being drawn, and asks before taking anything."""
    import fcntl
    from pathlib import Path
    from tapestry import embed
    from tapestry.hosts import claude_code as cc
    from tapestry.mind import Mind
    if bool(args.conversations) == args.all:
        print("tapestry redraw: name the conversations, or say --all", file=sys.stderr)
        return 2
    if args.mind:
        home = Path(args.state or Path(args.mind).parent)
        open_mind = lambda: Mind(args.mind, encode=embed.Encoder(), model_tag=embed.MODEL_TAG)
    else:
        home, open_mind = cc.home(), cc.open_mind
    home.mkdir(parents=True, exist_ok=True)
    with open(home / "jobs.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        mind = open_mind()
        try:
            chosen = mind.drawn_conversations() if args.all else args.conversations
            if not chosen:
                print("Nothing has been drawn from any conversation that is still kept.")
                return 0
            try:
                plan = mind.redrawing(chosen)
            except (KeyError, ValueError) as e:
                print(f"tapestry redraw: {e.args[0]}; nothing was taken back", file=sys.stderr)
                return 1
            print(f"{len(plan.episodes)} conversation(s) would wait to be drawn again. "
                  f"{len(plan.taken)} memories drawn from them would go"
                  + (f", {len(plan.restored)} they had replaced would be current again" if plan.restored else "")
                  + (f", and {plan.confirmations} confirmations they gave other memories would go"
                     if plan.confirmations else "")
                  + ". Their words stay, and nothing you or the agent noted is touched.")
            if not args.yes and input("Take these back? [y/N] ").strip().lower() not in ("y", "yes"):
                print("Nothing was taken back.")
                return 0
            mind.redraw(plan.episodes)
            print("Taken back. They will be drawn again by the background work, with the model chosen for drawing.")
            return 0
        finally:
            mind.close()


if __name__ == "__main__":
    sys.exit(main())
