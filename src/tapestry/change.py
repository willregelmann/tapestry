"""Change: a deliberate change to a mind, kept so it can be seen and undone (docs/primitives/CHANGE.md).

Rules this module enforces:
- Every act here is recorded as it is made, in the same step: who made it, when, in which
  session, why, and what was there before and after. A change that can't be recorded isn't made.
- A recorded change is never rewritten. Undoing one is a new change that puts back what was
  there, and can itself be undone.
- An undo applies only where the mind is still as the change left it. Otherwise it changes
  nothing and says what is in the way.
- A change names memories by number and never holds what one says. When a memory is forgotten,
  the reasons given for changes to it go too, since a reason may repeat it.
- What the mind learns by itself is not a change: facts drawn from conversations, and
  associations strengthening with use.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Iterable

from tapestry.mind import BORN_STRENGTH, STRENGTHEN, _faded, topic_name

if TYPE_CHECKING:
    from tapestry.mind import Mind

# Kept out of the mind's own schema, so a mind nothing has been changed in is still one the
# viewer can read without changing it.
_TABLES = """
CREATE TABLE IF NOT EXISTS changes (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      REAL NOT NULL,
    session TEXT,
    by      TEXT NOT NULL,
    act     TEXT NOT NULL,
    reason  TEXT NOT NULL DEFAULT '',
    detail  TEXT NOT NULL,                      -- what was there before and after, by number
    undoes  INTEGER REFERENCES changes(id)
);
CREATE TABLE IF NOT EXISTS change_memories (
    change_id INTEGER NOT NULL REFERENCES changes(id),
    memory_id INTEGER NOT NULL,
    PRIMARY KEY (change_id, memory_id)
);
CREATE TRIGGER IF NOT EXISTS changes_no_edit BEFORE UPDATE OF id, at, session, by, act, detail, undoes ON changes
BEGIN SELECT RAISE(ABORT, 'a change is never rewritten; undo it'); END;
"""
WEAKEN = 0.5   # weakening halves a strength, as half a year unused would


class Stale(RuntimeError):
    """The mind is no longer as a change left it, so the change can't be undone as it stands."""


def _exists(mind: "Mind") -> bool:
    return mind.db.execute("SELECT 1 FROM sqlite_master WHERE name='changes'").fetchone() is not None


def _topic(mind: "Mind", tid: int) -> tuple[str, str] | None:
    return mind.db.execute("SELECT name, description FROM topics WHERE id=?", (tid,)).fetchone()


# -- what each act does, and its opposite -----------------------------------------------
#
# A detail says what was there (`from`) and what is there after (`to`). Applying one checks the
# mind against `from` first, so the same code makes a change, undoes it and makes it again.

def _apply(mind: "Mind", act: str, d: dict, by: str, now: float) -> list[int]:
    """Make the change `d` describes, inside the caller's transaction. Returns the memories it
    names that are no longer there, which it passes over."""
    db, gone = mind.db, []
    if act == "describe":
        row = db.execute("SELECT id, description FROM topics WHERE name=?", (d["name"],)).fetchone()
        if (row[1] if row else None) != d["from"]:
            raise Stale(f"the topic {d['name']!r} has been {'described again' if row else 'removed'} since"
                        if d["from"] is not None else f"a topic {d['name']!r} exists already")
        if d["from"] is None:
            mind.topic(d["name"])   # refuses a topic with nothing above it to sit under
            if d.get("topic") is not None and _topic(mind, d["topic"]) is None:
                db.execute("UPDATE topics SET id=? WHERE name=?", (d["topic"], d["name"]))
            db.execute("UPDATE topics SET description=? WHERE name=?", (d["to"], d["name"]))
            d["topic"] = db.execute("SELECT id FROM topics WHERE name=?", (d["name"],)).fetchone()[0]
        elif d["to"] is None:
            held = db.execute("SELECT 1 FROM memory_topics WHERE topic_id=?", (row[0],)).fetchone()
            under = db.execute("SELECT 1 FROM topics WHERE name LIKE ? ESCAPE '\\'",
                               (d["name"].replace("_", "\\_") + "/%",)).fetchone()
            kept = db.execute("SELECT 1 FROM sqlite_master WHERE name='working_memories'").fetchone() and \
                db.execute("SELECT 1 FROM working_memories WHERE topic_id=?", (row[0],)).fetchone()
            if held or under or kept:
                raise Stale(f"the topic {d['name']!r} holds something now")
            db.execute("DELETE FROM topics WHERE id=?", (row[0],))
        else:
            db.execute("UPDATE topics SET description=? WHERE id=?", (d["to"], row[0]))
    elif act == "rename":
        names = {int(t): pair for t, pair in d["names"].items()}
        for tid, (old, _) in names.items():
            if (_topic(mind, tid) or (None,))[0] != old:
                raise Stale(f"the topic {old!r} has been moved or removed since")
        taken = {n for (n,) in db.execute("SELECT name FROM topics")} - {old for old, _ in names.values()}
        if clash := sorted(taken & {new for _, new in names.values()}):
            raise Stale(f"a topic {clash[0]!r} exists already")
        # Through names no topic can have, so that swapping two never collides halfway.
        for tid in names:
            db.execute("UPDATE topics SET name=? WHERE id=?", (f"\0{tid}", tid))
        for tid, (_, new) in names.items():
            db.execute("UPDATE topics SET name=? WHERE id=?", (new, tid))
    elif act == "file":
        for mid, where in d["memories"].items():
            mid = int(mid)
            if db.execute("SELECT 1 FROM memories WHERE id=?", (mid,)).fetchone() is None:
                gone.append(mid)
                continue
            for tid in where["add"]:
                if (t := _topic(mind, tid)) is None:
                    raise Stale("a topic these memories were filed in has been removed since")
                mind._file(mid, tid, t[0], by, now, None)
            for tid in where["remove"]:
                if (t := _topic(mind, tid)) and db.execute(
                        "DELETE FROM memory_topics WHERE memory_id=? AND topic_id=?", (mid, tid)).rowcount:
                    mind._evidence(mid, "unfile", by, ts=now, note=t[0])
        for wid, (old, new) in (d.get("working") or {}).items():
            db.execute("UPDATE working_memories SET topic_id=? WHERE id=? AND topic_id=?", (new, int(wid), old))
    elif act == "associate":
        for src, dst, was, to in d["links"]:
            if any(db.execute("SELECT 1 FROM memories WHERE id=?", (m,)).fetchone() is None for m in (src, dst)):
                gone += [src, dst]
                continue
            row = db.execute("SELECT strength, last_used_at FROM associations WHERE src=? AND dst=?",
                             (src, dst)).fetchone()
            if (None if row is None else [round(row[0], 6), round(row[1], 3)]) != was:
                raise Stale(f"the association from #{src} to #{dst} has changed since")
            db.execute("DELETE FROM associations WHERE src=? AND dst=?", (src, dst))
            if to is not None:
                db.execute("INSERT INTO associations VALUES (?,?,?,?)", (src, dst, *to))
    else:
        raise ValueError(f"no such act {act!r}")
    return sorted(set(gone))


def _opposite(act: str, d: dict) -> dict:
    if act == "describe":
        return {**d, "from": d["to"], "to": d["from"]}
    if act == "rename":
        return {"names": {t: [new, old] for t, (old, new) in d["names"].items()}}
    if act == "file":
        return {"memories": {m: {"add": w["remove"], "remove": w["add"]} for m, w in d["memories"].items()},
                "working": {w: [new, old] for w, (old, new) in (d.get("working") or {}).items()}}
    return {"links": [[src, dst, to, was] for src, dst, was, to in d["links"]]}


def _named(act: str, d: dict) -> set[int]:
    if act == "file":
        return {int(m) for m in d["memories"]}
    return {m for link in d["links"] for m in link[:2]} if act == "associate" else set()


def _make(mind: "Mind", act: str, d: dict, *, reason: str, session: str | None, by: str,
          undoes: int | None, now: float) -> dict:
    """Make a change and record it, inside the caller's transaction."""
    gone = _apply(mind, act, d, by, now)
    cid = mind.db.execute("INSERT INTO changes(at, session, by, act, reason, detail, undoes) VALUES (?,?,?,?,?,?,?)",
                          (now, session, by, act, reason.strip(), json.dumps(d), undoes)).lastrowid
    mind.db.executemany("INSERT OR IGNORE INTO change_memories VALUES (?,?)", [(cid, m) for m in _named(act, d)])
    return {"change": cid, **({"no_longer_there": gone} if gone else {})}


def _made(mind: "Mind", steps: list[tuple[str, dict]], *, reason: str, session: str | None, by: str,
          now: float | None) -> list[dict]:
    now = time.time() if now is None else now
    # The tables are made outside the transaction: a script would end one that is open.
    if not _exists(mind):
        mind.db.executescript(_TABLES)
    with mind.db:
        mind.db.execute("BEGIN")
        return [_make(mind, act, d, reason=reason, session=session, by=by, undoes=None, now=now)
                for act, d in steps]


# -- the acts ----------------------------------------------------------------------------

def describe(mind: "Mind", topic: str, description: str, *, reason: str = "", session: str | None = None,
             by: str = "agent", now: float | None = None) -> dict | None:
    """Create a topic, or say again what one holds. None when it already says exactly that."""
    name, description = topic_name(topic), description.strip()
    row = mind.db.execute("SELECT id, description FROM topics WHERE name=?", (name,)).fetchone()
    if row and row[1] == description:
        return None
    d = {"topic": row[0] if row else None, "name": name, "from": row[1] if row else None, "to": description}
    return _made(mind, [("describe", d)], reason=reason, session=session, by=by, now=now)[0]


def file(mind: "Mind", memories: Iterable[int], *, add: Iterable[str] = (), remove: Iterable[str] = (),
         reason: str = "", session: str | None = None, by: str = "agent", now: float | None = None) -> dict | None:
    """File memories in topics, take them out, or both. None when they are already filed so."""
    d = _filing(mind, memories, add, remove)
    return _made(mind, [("file", d)], reason=reason, session=session, by=by, now=now)[0] if d["memories"] else None


def _filing(mind: "Mind", memories: Iterable[int], add: Iterable[str], remove: Iterable[str]) -> dict:
    ids = {n: mind.db.execute("SELECT id FROM topics WHERE name=?", (n,)).fetchone() for n in {*add, *remove}}
    if missing := sorted(n for n, row in ids.items() if row is None):
        raise ValueError(f"no topic {', '.join(missing)}")
    out = {}
    for mid in dict.fromkeys(memories):
        if mind.db.execute("SELECT 1 FROM memories WHERE id=?", (mid,)).fetchone() is None:
            raise KeyError(f"no memory {mid}")
        inside = {t for (t,) in mind.db.execute("SELECT topic_id FROM memory_topics WHERE memory_id=?", (mid,))}
        adding = [ids[n][0] for n in dict.fromkeys(add) if ids[n][0] not in inside]
        removing = [ids[n][0] for n in dict.fromkeys(remove) if ids[n][0] in inside and n not in set(add)]
        if adding or removing:
            out[str(mid)] = {"add": adding, "remove": removing}
    return {"memories": out, "working": {}}


def move(mind: "Mind", topic: str, to: str, *, reason: str = "", session: str | None = None,
         by: str = "agent", now: float | None = None) -> list[dict]:
    """Give a topic and every topic under it a new name, which is where it sits. Onto a topic that
    exists, everything filed or kept in view in it goes there and the emptied topic is removed:
    two changes, the filing and then the removal."""
    plan = mind.moving(topic, to)
    name, to = topic_name(topic), topic_name(to)
    tid = lambda n: mind.db.execute("SELECT id FROM topics WHERE name=?", (n,)).fetchone()[0]
    if not plan["into_existing"]:
        d = {"names": {str(tid(old)): [old, new] for old, new in plan["renamed"].items()}}
        return _made(mind, [("rename", d)], reason=reason, session=session, by=by, now=now)
    old, new = tid(name), tid(to)
    filed = [m for (m,) in mind.db.execute("SELECT memory_id FROM memory_topics WHERE topic_id=?", (old,))]
    d = _filing(mind, filed, [to], [])
    for m in filed:
        d["memories"].setdefault(str(m), {"add": [], "remove": []})["remove"] = [old]
    if mind.db.execute("SELECT 1 FROM sqlite_master WHERE name='working_memories'").fetchone():
        d["working"] = {str(w): [old, new] for (w,) in mind.db.execute(
            "SELECT id FROM working_memories WHERE topic_id=?", (old,))}
    gone = {"topic": old, "name": name, "from": _topic(mind, old)[1], "to": None}
    return _made(mind, [("file", d), ("describe", gone)], reason=reason, session=session, by=by, now=now)


def associate(mind: "Mind", a: int, b: int, how: str, *, reason: str, session: str | None = None,
              by: str = "agent", now: float | None = None) -> dict:
    """Link two memories that already exist, in both directions, or strengthen or weaken what
    links them. A reason is required: nothing else says why recall goes from one to the other."""
    if how not in ("link", "strengthen", "weaken"):
        raise ValueError(f"no such way to associate: {how!r}")
    if not reason.strip():
        raise ValueError("an association made by hand needs its reason")
    if a == b:
        raise ValueError("a memory isn't associated with itself")
    now = time.time() if now is None else now
    links = []
    for src, dst in ((a, b), (b, a)):
        for m in (src, dst):
            if mind.db.execute("SELECT 1 FROM memories WHERE id=?", (m,)).fetchone() is None:
                raise KeyError(f"no memory {m}")
        row = mind.db.execute("SELECT strength, last_used_at FROM associations WHERE src=? AND dst=?",
                              (src, dst)).fetchone()
        was = None if row is None else [round(row[0], 6), round(row[1], 3)]
        if how == "link":
            if row is not None:
                continue
            to = BORN_STRENGTH
        elif row is None:
            continue
        else:
            s = _faded(row[0], row[1], now)
            to = s + STRENGTHEN * (1 - s) if how == "strengthen" else s * WEAKEN
        links.append([src, dst, was, [round(to, 6), round(now, 3)]])
    if not links:
        raise ValueError(f"#{a} and #{b} are already associated; strengthen or weaken it" if how == "link"
                         else f"#{a} and #{b} are not associated; link them first")
    return _made(mind, [("associate", {"links": links})], reason=reason, session=session, by=by, now=now)[0]


# -- seeing and undoing ------------------------------------------------------------------

def _in_force(rows: list[tuple]) -> set[int]:
    """The changes still standing: not undone by an undo that itself still stands."""
    undone, standing = set(), set()
    for cid, undoes in sorted(rows, reverse=True):
        if cid not in undone:
            standing.add(cid)
            if undoes is not None:
                undone.add(undoes)
    return standing


def _says(mind: "Mind", act: str, d: dict) -> str:
    name = lambda tid: (_topic(mind, tid) or ("a topic since removed",))[0]
    if act == "describe":
        return (f"created the topic {d['name']}" if d["from"] is None else
                f"removed the emptied topic {d['name']}" if d["to"] is None else f"described the topic {d['name']} again")
    if act == "rename":
        return "; ".join(f"moved the topic {old} to {new}" for old, new in d["names"].values())
    if act == "file":
        added = sorted({name(t) for w in d["memories"].values() for t in w["add"]})
        removed = sorted({name(t) for w in d["memories"].values() for t in w["remove"]})
        n = len(d["memories"])
        return (f"refiled {n} {'memory' if n == 1 else 'memories'}" + (f", into {', '.join(added)}" if added else "")
                + (f", out of {', '.join(removed)}" if removed else ""))
    pairs = sorted({(min(s, t), max(s, t)) for s, t, *_ in d["links"]})
    how = "linked" if all(w is None for *_, w, _ in d["links"]) else \
        "unlinked" if all(t is None for *_, t in d["links"]) else "changed the association between"
    return f"{how} " + ", ".join(f"#{a} and #{b}" for a, b in pairs)


def log(mind: "Mind", *, limit: int = 20, session: str | None = None) -> list[dict]:
    """The latest changes, newest first: what each did, why, and whether it still stands."""
    if not _exists(mind):
        return []
    standing = _in_force(mind.db.execute("SELECT id, undoes FROM changes").fetchall())
    rows = mind.db.execute("SELECT id, at, session, by, act, reason, detail, undoes FROM changes"
                           + (" WHERE session=?" if session else "") + " ORDER BY id DESC LIMIT ?",
                           (*([session] if session else []), limit)).fetchall()
    return [{"change": cid, "at": at, "session": sess, "by": by, "did": _says(mind, act, json.loads(detail)),
             "reason": reason, **({"undoes": undoes} if undoes is not None else {}), "stands": cid in standing}
            for cid, at, sess, by, act, reason, detail, undoes in rows]


def undo(mind: "Mind", change_id: int, *, reason: str = "", session: str | None = None, by: str = "agent",
         now: float | None = None) -> dict:
    """Put back what a change replaced, as a new change. Stale when the mind has moved on from it."""
    now = time.time() if now is None else now
    row = mind.db.execute("SELECT act, detail FROM changes WHERE id=?", (change_id,)).fetchone() \
        if _exists(mind) else None
    if row is None:
        raise KeyError(f"no change {change_id}")
    if change_id not in _in_force(mind.db.execute("SELECT id, undoes FROM changes").fetchall()):
        raise Stale(f"change {change_id} has already been undone")
    with mind.db:
        mind.db.execute("BEGIN")
        return _make(mind, row[0], _opposite(row[0], json.loads(row[1])), reason=reason, session=session,
                     by=by, undoes=change_id, now=now)


def undo_session(mind: "Mind", of: str, *, reason: str = "", session: str | None = None, by: str = "agent",
                 now: float | None = None) -> dict:
    """Undo every change a session made that still stands, newest first. One that can't be
    undone is passed over and named, and the rest are still undone."""
    if not _exists(mind):
        return {"undone": [], "refused": []}
    rows = mind.db.execute("SELECT id, undoes FROM changes").fetchall()
    standing, theirs = _in_force(rows), {c for (c,) in mind.db.execute("SELECT id FROM changes WHERE session=?", (of,))}
    undone, refused = [], []
    for cid, undoes in sorted(rows, reverse=True):
        # An undo of the session's own change is left alone: undoing it would make that change again.
        if cid not in theirs or cid not in standing or undoes in theirs:
            continue
        try:
            undo(mind, cid, reason=reason, session=session, by=by, now=now)
            undone.append(cid)
        except Stale as e:
            refused.append({"change": cid, "why": str(e)})
    return {"undone": undone, "refused": refused}


def forgetting(mind: "Mind", memories: Iterable[int]) -> None:
    """A reason given for changing a memory may repeat it, so it goes when the memory does.
    Inside the caller's transaction."""
    ids = list(memories)
    if ids and _exists(mind):
        mind.db.execute("UPDATE changes SET reason='' WHERE id IN (SELECT change_id FROM change_memories"
                        f" WHERE memory_id IN ({','.join('?' * len(ids))}))", ids)
