"""Where a turn is filed: one rule in the core for every host (docs/primitives/NAMESPACE.md, ADR-0019)."""

from __future__ import annotations

import re

import pytest

from tapestry import filing


@pytest.mark.parametrize("session,place,want", [
    ("s1", "atlas", ["session.s1", "atlas"]),
    ("s1", None, ["session.s1"]),
    ("s1", "", ["session.s1"]),
    (None, "atlas", ["session.unknown", "atlas"]),
    ("s1", "session.s1", ["session.s1"]),
])
def test_a_turn_is_filed_in_its_session_and_place_never_the_default(session, place, want):
    got = filing.turn_namespaces(session, place)
    assert got == want
    assert got and all(n for n in got)   # never empty, so never the default namespace


@pytest.mark.parametrize("sid", ["20260831_210533_01629a01", "api_1789786014_c0a07a8f",
                                 "Weird ID/with spaces", "", None,
                                 "3f2a9c1e-55b7-4c1e-9f2d-0b6a7d1e2c34"])
def test_session_namespace_is_a_valid_name(sid):
    name = filing.session_namespace(sid)
    assert name.startswith(filing.SESSION_PREFIX)
    assert re.fullmatch(r"[a-z0-9][a-z0-9._-]*", name)


def test_listing_shows_this_session_and_counts_the_others():
    ns = [{"name": n} for n in ("atlas", "session.a", "session.b", "session.c", "finance")]
    listed, hidden = filing.listing(ns, "a")
    assert [n["name"] for n in listed] == ["atlas", "session.a", "finance"] and hidden == 2
