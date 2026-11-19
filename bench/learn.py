"""Learning a subject from pages: a throwaway mind reads a wiki, a page at a time.

    python -m bench.learn --api https://site/api.php --topic NAME --about "what the subject is" \
        --seeds "Page one" "Page two" [--pages N] [--arm haiku@low] --out DIR [--log]

A prototype (PLAN.md, Learning from sources): it answers what a page costs to read, how much it
leaves, when reading more stops teaching anything new, and what graph forms. Nothing here is on
the path a real mind takes. The mind is new and thrown away, the pages and what was drawn go to
--out, outside the repository, and the log gets counts only.

Pages come from a MediaWiki site. The seeds are whoever runs it choosing where to start, standing
in for the agent choosing sources; after them it reads the unread page that the most read pages
link to. A page is drawn the way a conversation is, its sections standing for turns, by a drawer
told it is reading a source. Every fact is the web's claim, filed under the one topic, and its
evidence names the page, so two pages that say the same thing are two pieces of evidence.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable

from bench.extract import RESULTS
from tapestry import belief, consolidate, embed, judge as judges
from tapestry.mind import Mind

KNOWN_PER_SECTION = 4
KNOWN = 60
MIN_SECTION_CHARS = 80
SAME = 0.9   # a fact this close in meaning to one the mind holds is that fact, whatever the model called it
AGENT = "tapestry-bench-learn/0 (prototype; reads a few dozen pages once)"

SKIPPED_CLASSES = {"navbox", "reference", "references", "mw-editsection", "toc", "gallery", "noprint",
                   "mw-references-wrap", "reflist", "hatnote", "thumbcaption", "wikia-gallery", "metadata",
                   "mw-empty-elt", "sistersitebox", "navigation-not-searchable"}
SKIPPED_SECTIONS = {"references", "gallery", "external links", "see also", "navigation", "notes", "further reading",
                    "videos", "trivia", "citations", "bibliography", "sources"}
VOID = {"br", "img", "hr", "input", "meta", "link", "wbr", "source", "col", "area"}

SYSTEM = """\
You read part of a page from a reference source and write down what it says that is worth \
knowing about its subject later. What you write becomes long-term memory. Months from now the \
facts that bear on a question will be shown, each labelled as coming from this source, with \
nothing else of the page.

So each fact stands on its own: it names who and what it is about in full, never "he", "this \
item" or "the above", and says one thing the page says, no more. Keep names, numbers and dates \
exactly. Everything in a fact comes from the page: leave out what you know from elsewhere, and \
what the page only hints at. Where the page says something is uncertain, rumoured or one \
reading among several, the fact says so too.

Worth keeping: what something is, who did what and to whom, how things relate, where and when, \
what causes what, quantities, and what the page says is disputed. Not worth keeping: how the \
page is laid out, where to find more, and the same thing said twice.

For each fact give:
- passages: the numbers of the passages it was drawn from.
- volatility: immutable (it can't change), stable (it rarely does), project (a plan or the \
state of something under way) or transient (true right now).
- same_as: the id of a known memory that already says this. Give nothing else for it then.
- builds_on: the ids of known memories this adds to or depends on.
Only ids from the known memories can be used."""
SCHEMA = {"type": "object", "required": ["facts"], "properties": {"facts": {"type": "array", "items": {
    "type": "object", "required": ["content", "passages"], "properties": {
        "content": {"type": "string"},
        "passages": {"type": "array", "items": {"type": "integer"}},
        "volatility": {"type": "string", "enum": ["immutable", "stable", "project", "transient"]},
        "same_as": {"type": "integer"},
        "builds_on": {"type": "array", "items": {"type": "integer"}}}}}}}


class _Sections(HTMLParser):
    """A rendered page as its sections of plain text, without its furniture."""

    def __init__(self) -> None:
        super().__init__()
        self.sections: list[list[str]] = [["", ""]]   # [heading, text]
        self._open: list[bool] = []                   # per open element: is it skipped
        self._heading: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            return
        classes = set((dict(attrs).get("class") or "").split())
        self._open.append(bool(self._open and self._open[-1]) or tag in ("script", "style", "sup")
                          or bool(classes & SKIPPED_CLASSES))
        if tag in ("h2", "h3") and not self._open[-1]:
            self._heading = []
        elif tag in ("p", "li", "tr", "div", "h4", "dd", "dt") and not self._open[-1]:
            self.sections[-1][1] += "\n"

    def handle_endtag(self, tag):
        if tag in VOID or not self._open:
            return
        self._open.pop()
        if tag in ("h2", "h3") and self._heading is not None:
            self.sections.append(["".join(self._heading).strip(), ""])
            self._heading = None
        elif tag in ("td", "th"):
            self.sections[-1][1] += " "

    def handle_data(self, data):
        if self._open and self._open[-1]:
            return
        if self._heading is not None:
            self._heading.append(data)
        else:
            self.sections[-1][1] += data


def sections(html: str) -> list[tuple[str, str]]:
    p = _Sections()
    p.feed(html)
    out = []
    for heading, text in p.sections:
        text = "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())
        if heading.lower() not in SKIPPED_SECTIONS and len(text) >= MIN_SECTION_CHARS:
            out.append((heading, text))
    return out


def fetch(api: str, title: str) -> dict | None:
    """A page of a MediaWiki site: its title, address, sections and the pages it links to."""
    q = urllib.parse.urlencode({"action": "parse", "page": title, "prop": "text|links", "format": "json",
                                "redirects": 1, "formatversion": 2})
    req = urllib.request.Request(f"{api}?{q}", headers={"User-Agent": AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        parsed = json.load(r).get("parse")
    if not parsed:
        return None
    base = api.rsplit("/", 1)[0].removesuffix("/w")
    return {"title": parsed["title"],
            "url": f"{base}/wiki/{urllib.parse.quote(parsed['title'].replace(' ', '_'))}",
            "sections": sections(parsed["text"]),
            "links": [l["title"] for l in parsed["links"] if l.get("ns") == 0 and l.get("exists")]}


class PageDrawer(judges.ClaudeDrawer):
    """Draws what a page says. Who said it and where it is filed aren't the model's to decide
    here: the page said it, and the reader chose the topic."""

    prompt = "learn_v1"

    def __init__(self, model: str = "haiku", **kw) -> None:
        super().__init__(model, turns_per_fact=None, **kw)

    def draw_page(self, *, about: str, title: str, date: str, known: list[tuple[int, str]],
                  passages: list[tuple[int, str, str]]) -> list[dict]:
        argv = ["claude", "-p", "--safe-mode", "--model", self.alias, "--tools", "", "--no-session-persistence",
                "--output-format", "json", "--system-prompt", SYSTEM, "--json-schema", json.dumps(SCHEMA)]
        if self.effort:
            argv += ["--effort", self.effort]
        message = "\n\n".join([
            f"The subject being learned: {about}", f"The page: {title}, read on {date}.",
            "Known memories:\n" + ("\n".join(f"#{i}: {text}" for i, text in known) or "(none)"),
            "The page:\n" + "\n\n".join(f"[{i}] {heading or title}\n{text}" for i, heading, text in passages)])
        t0 = time.monotonic()
        out = json.loads(self._run(argv, message))
        if out.get("is_error"):
            raise RuntimeError(f"claude reported an error: {str(out.get('result'))[:200]}")
        used = list((out.get("modelUsage") or {}))
        self.model = f"claude:{used[0]}" if used else self.model
        self.last = {"cost_usd": out.get("total_cost_usd"), "wall_s": time.monotonic() - t0,
                     "output_tokens": (out.get("usage") or {}).get("output_tokens")}
        answer = out.get("structured_output")
        if not isinstance(answer, dict) or not isinstance(answer.get("facts"), list):
            raise RuntimeError("claude gave no list of facts")
        return answer["facts"]


def read_page(mind: Mind, page: dict, drawer: PageDrawer, *, topic: str, about: str, at: float | None = None) -> dict:
    """Draw one page into `mind`. Returns what was done, and which memory came from which section."""
    at = time.time() if at is None else at
    source = "page:" + page["url"]
    stats = {"facts": 0, "confirmed": 0, "close": 0, "dropped": 0, "calls": 0, "cost_usd": 0.0, "output_tokens": 0,
             "seconds": 0.0, "chars": sum(len(t) for _, t in page["sections"])}
    drawn: list[dict] = []
    for window in consolidate.windows([{"heading": h, "text": t} for h, t in page["sections"]]):
        ids = []
        for s in window:
            ids += [r.id for r in mind.recall(f"{page['title']} {s['heading']}\n{s['text']}"[:1000],
                                              topics=[topic], k=KNOWN_PER_SECTION)]
        known = [(i, mind.content(i)) for i in list(dict.fromkeys(ids))[:KNOWN]]
        known_ids = {i for i, _ in known}
        facts = drawer.draw_page(about=about, title=page["title"], known=known,
                                 date=dt.datetime.fromtimestamp(at, dt.timezone.utc).strftime("%Y-%m-%d"),
                                 passages=[(i, s["heading"], consolidate._shown(s["text"]))
                                           for i, s in enumerate(window, 1)])
        stats["calls"] += 1
        for k in ("cost_usd", "output_tokens"):
            stats[k] += (drawer.last or {}).get(k) or 0
        stats["seconds"] += (drawer.last or {}).get("wall_s") or 0.0
        born: list[tuple[int, set[int]]] = []
        for fact in facts:
            content = str(fact.get("content") or "").strip()
            cited = {i for i in fact.get("passages") or [] if isinstance(i, int) and 1 <= i <= len(window)}
            if not content or not cited:
                stats["dropped"] += 1
                continue
            ok = lambda v: {i for i in (v if isinstance(v, list) else [v]) if isinstance(i, int) and i in known_ids}
            same, builds_on = ok(fact.get("same_as")), ok(fact.get("builds_on"))
            key = "learned:" + hashlib.sha256(f"{source}\0{content}".encode()).hexdigest()
            if mind.db.execute("SELECT 1 FROM memories WHERE key=?", (key,)).fetchone():
                continue
            if not same:
                # The model is shown only some of what the mind holds, so it can't be what decides
                # that a fact is new.
                near = mind.recall(content, topics=[topic], k=1)
                if near and near[0].cosine >= SAME:
                    same = {near[0].id}
                    stats["close"] += 1
            if same:
                mind.confirm(min(same), source="web", at=at, episode=source, reasoner=drawer.model)
                stats["confirmed"] += 1
                continue
            volatility = fact.get("volatility") if fact.get("volatility") in belief.HALF_LIFE else None
            mid = mind.remember(content, source="web", topics=[topic], at=at, episode=source, key=key,
                                volatility=volatility, reasoner=drawer.model,
                                associate=[*(m for m, ps in born if ps & cited), *sorted(builds_on)])
            born.append((mid, cited))
            stats["facts"] += 1
            drawn.append({"id": mid, "page": page["title"], "url": page["url"],
                          "sections": [window[i - 1]["heading"] for i in sorted(cited)],
                          "builds_on": sorted(builds_on), "content": content})
    return {**stats, "drawn": drawn}


def graph(mind: Mind, page_of: dict[int, str]) -> dict:
    """The shape of what formed: how connected it is, and how much of that crosses pages."""
    ids = set(page_of)
    edges = {tuple(sorted(e)) for e in mind.db.execute("SELECT src, dst FROM associations") if set(e) <= ids}
    parent = {i: i for i in ids}

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in edges:
        parent[find(a)] = find(b)
    sizes = sorted(Counter(find(i) for i in ids).values(), reverse=True)
    degree = Counter(i for e in edges for i in e)
    cross = sum(page_of[a] != page_of[b] for a, b in edges)
    confirmed = mind.db.execute("SELECT count(DISTINCT memory_id) FROM evidence WHERE kind='confirm'").fetchone()[0]
    return {"memories": len(ids), "associations": len(edges), "across_pages": cross,
            "components": len(sizes), "largest_share": round(sizes[0] / len(ids), 3) if ids else None,
            "alone": sum(1 for i in ids if not degree[i]),
            "median_degree": sorted(degree[i] for i in ids)[len(ids) // 2] if ids else None,
            "confirmed_by_another_page": confirmed}


def learn(mind: Mind, drawer: PageDrawer, *, topic: str, about: str, seeds: list[str], pages: int,
          fetch_page: Callable[[str], dict | None], out: Path | None = None) -> dict:
    """Read up to `pages` pages into `mind`, the seeds first. Returns counts per page and overall."""
    mind.topic(topic, about)
    done: set[str] = set()   # titles of pages drawn; `read` also holds the names they were asked for by
    queue, read, wanted, rows, drawn, misses = list(seeds), set(), Counter(), [], [], 0
    while len(rows) < pages and misses <= pages:
        title = queue.pop(0) if queue else next((t for t, _ in wanted.most_common() if t not in read), None)
        if title is None:
            break
        page = fetch_page(title)
        read.add(title)
        if page is None or page["title"] in done or not page["sections"]:
            misses += 1
            continue
        did = read_page(mind, page, drawer, topic=topic, about=about)
        drawn += did.pop("drawn")
        links = [l for l in page["links"] if l != page["title"]]
        row = {"page": page["title"], **did, "links": len(links), "links_to_read": sum(l in done for l in links)}
        done.add(page["title"])
        read.add(page["title"])
        wanted.update(l for l in links if l not in read)
        rows.append(row)
        print(json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in row.items()}), flush=True)
        if out is not None:
            (out / "drawn.json").write_text(json.dumps(drawn, indent=1))
    total = {k: round(sum(r[k] for r in rows), 4) for k in
             ("facts", "confirmed", "close", "dropped", "calls", "cost_usd", "output_tokens", "seconds", "chars", "links",
              "links_to_read")}
    share = lambda rs: (round(sum(r["confirmed"] for r in rs) / max(1, sum(r["confirmed"] + r["facts"] for r in rs)), 3))
    q = max(1, len(rows) // 4)
    return {"pages": len(rows), **total, "facts_per_page": round(total["facts"] / max(1, len(rows)), 1),
            "known_already_by_quarter": [share(rows[i * q:(i + 1) * q if i < 3 else None]) for i in range(4)],
            "graph": graph(mind, {d["id"]: d["page"] for d in drawn}), "per_page": rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench.learn", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", required=True, help="the MediaWiki api.php of the site to read")
    ap.add_argument("--topic", required=True)
    ap.add_argument("--about", required=True, help="the subject, in words")
    ap.add_argument("--seeds", nargs="+", required=True, help="pages to start from")
    ap.add_argument("--pages", type=int, default=50)
    ap.add_argument("--arm", default="haiku@low", help="the drawer: a model, optionally with an effort")
    ap.add_argument("--out", type=Path, required=True, help="where the mind and what was drawn go; never the repo")
    ap.add_argument("--log", action="store_true", help="append counts, never content, to bench/results.jsonl")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    cache = args.out / "pages"
    cache.mkdir(exist_ok=True)

    def fetch_page(title: str) -> dict | None:
        f = cache / (hashlib.sha256(title.encode()).hexdigest()[:16] + ".json")
        if not f.exists():
            f.write_text(json.dumps(fetch(args.api, title)))
            time.sleep(1.0)   # a few dozen pages, one at a time: no load worth noticing
        return json.loads(f.read_text())

    model, _, effort = args.arm.partition("@")
    drawer = PageDrawer(model, **({"effort": effort} if effort else {}))
    path = args.out / "mind.db"
    path.unlink(missing_ok=True)
    mind = Mind(path, encode=embed.Encoder(), model_tag=embed.MODEL_TAG)
    try:
        result = learn(mind, drawer, topic=args.topic, about=args.about, seeds=args.seeds, pages=args.pages,
                       fetch_page=fetch_page, out=args.out)
    finally:
        mind.close()
    result = {"site": urllib.parse.urlparse(args.api).netloc, "topic": args.topic, "arm": args.arm,
              "prompt": drawer.prompt, **result}
    print(json.dumps({k: v for k, v in result.items() if k != "per_page"}, indent=1))
    if args.log:
        with open(RESULTS, "a") as fh:
            fh.write(json.dumps({"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                 "kind": "learn", **result}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
