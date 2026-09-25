"""Run fixtures against memory systems and log the results.

    python -m bench.run                          # every system, every fixture
    python -m bench.run -s mnemonic -k 5 -v      # one system, per-query detail
    python -m bench.run --no-log                 # don't append to the results log
    python -m bench.run --longmemeval            # LongMemEval-S sample, 10 per type
    python -m bench.run --longmemeval --lme-per-type 0   # all 500 questions

Each run appends one line per (system, fixture) to bench/results.jsonl, keyed
by commit and config, so runs can be compared over time.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

from bench import fixture as fixture_mod
from bench import systems
from bench.metrics import FixtureScore, score_query

ROOT = Path(__file__).resolve().parent
DEFAULT_FIXTURES = ROOT / "fixtures"
DEFAULT_LOG = ROOT / "results.jsonl"
COLUMNS = ("recall@{k}", "mrr", "hit@1", "stale_serve", "leakage", "negative_serve",
           "negative_confident")


def _git() -> dict:
    def run(*args: str) -> str:
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                                  text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return ""
    return {"commit": run("rev-parse", "--short", "HEAD") or None,
            "dirty": bool(run("status", "--porcelain"))}


def run_all(system, fixtures: list[fixture_mod.Fixture], k: int) -> list[FixtureScore]:
    """Score every fixture, merging fixtures that share a name into one row."""
    merged: dict[str, FixtureScore] = {}
    for i, fx in enumerate(fixtures, 1):
        score = run_fixture(system, fx, k)
        merged.setdefault(fx.name, FixtureScore(fixture=fx.name, k=k)).results += score.results
        if len(fixtures) > 20 and i % 10 == 0:
            print(f"  {system.name}: {i}/{len(fixtures)} fixtures", file=sys.stderr, flush=True)
    return list(merged.values())


def run_fixture(system, fx: fixture_mod.Fixture, k: int) -> FixtureScore:
    system.seed(fx.memories)
    score = FixtureScore(fixture=fx.name, k=k)
    for q in fx.queries:
        hits = system.recall(q.text, scopes=fx.loaded_scopes(q), k=k, at=q.at)
        score.results.append(score_query(fx, q, hits, k))
    return score


def _fmt(v) -> str:
    return "   -  " if v is None else f"{v:6.3f}"


def _print_table(system_name: str, scores: list[FixtureScore], k: int) -> None:
    cols = [c.format(k=k) for c in COLUMNS]
    print(f"\n{system_name}")
    print(f"  {'fixture':<30}" + "".join(f"{c:>19}" for c in cols))
    for s in scores:
        summary = s.summary()
        print(f"  {s.fixture:<30}" + "".join(f"{_fmt(summary[c]):>19}" for c in cols))


def _print_detail(score: FixtureScore) -> None:
    print(f"\n  [{score.fixture}]")
    for r in score.results:
        flags = []
        if r.stale_served:
            flags.append(f"STALE {r.stale_served}")
        if r.leaked:
            flags.append(f"LEAK {r.leaked}")
        if r.forbidden_served:
            flags.append(f"FORBIDDEN {r.forbidden_served}")
        if r.query.is_negative and r.hits:
            flags.append("SERVED-ON-NEGATIVE")
        rank = "neg" if r.query.is_negative else (r.rank or "miss")
        top = ", ".join(f"{h.id}:{h.score:.2f}" for h in r.hits[:3])
        print(f"    {str(rank):>4}  {r.query.text[:52]:<52}  {top}  {' '.join(flags)}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-s", "--system", action="append", choices=systems.NAMES)
    ap.add_argument("-f", "--fixtures", type=Path, default=DEFAULT_FIXTURES)
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--log", type=Path, default=DEFAULT_LOG)
    ap.add_argument("--no-log", action="store_true")
    ap.add_argument("--longmemeval", nargs="?", type=Path, const=True, default=None,
                    help="run LongMemEval instead of the TOML fixtures (optional data path)")
    ap.add_argument("--lme-per-type", type=int, default=10, help="questions per type; 0 = all")
    ap.add_argument("--lme-granularity", choices=("session", "turn"), default="session")
    args = ap.parse_args(argv)

    if args.longmemeval:
        from bench import longmemeval
        path = longmemeval.DEFAULT_PATH if args.longmemeval is True else args.longmemeval
        if not path.exists():
            print(f"{path} not found; run: python -m bench.longmemeval --download", file=sys.stderr)
            return 2
        fixtures = longmemeval.load(path, per_type=args.lme_per_type or None,
                                    granularity=args.lme_granularity)
    else:
        fixtures = (fixture_mod.load_dir(args.fixtures) if args.fixtures.is_dir()
                    else [fixture_mod.load(args.fixtures)])
    git = _git()
    ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    lines = []
    for name in args.system or systems.NAMES:
        try:
            system = systems.get(name)
        except Exception as e:  # a baseline that can't load is reported, not fatal
            print(f"\n{name}: UNAVAILABLE ({type(e).__name__}: {e})", file=sys.stderr)
            continue
        try:
            scores = run_all(system, fixtures, args.k)
        finally:
            system.close()
        _print_table(name, scores, args.k)
        if args.verbose:
            for s in scores:
                _print_detail(s)
        extra = ({"longmemeval": {"per_type": args.lme_per_type,
                                  "granularity": args.lme_granularity}}
                 if args.longmemeval else {})
        lines += [{"ts": ts, **git, "system": name, "config": system.config(), **extra,
                   "fixture": s.fixture, "k": args.k, "metrics": s.summary()}
                  for s in scores]
    if lines and not args.no_log:
        with args.log.open("a") as fh:
            fh.writelines(json.dumps(line) + "\n" for line in lines)
        print(f"\nlogged {len(lines)} result(s) to {args.log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
