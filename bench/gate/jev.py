"""Ask Jev, per turn, whether each recall candidate bears on the message, and whether it constrains
what the agent is about to do.

One request per turn: the state is the incoming message only, and each candidate gets two Noul
questions carrying the memory in its instructions (TypeSafe's re-ranking pattern). The gate's J
score is the bears_on Noul (P_PRIMARY); constrains is descriptive. Jev never sees the reply, the
cosine, or a label, so it can't be scored on anything it was shown.

The key is read from a file and never logged or put in an error. The model is pinned. Requests
carry NO zero-data-retention flag: Will's decision (b), 2026-10-18, because the gateway refuses the
flag on the Hobby plan. See ZDR_FLAG and bench/gate/README.md.

What answered: on the gateway, the top-level `model` only ECHOES the id requested, so it can't show
which build answered. The routing metadata (provider_metadata.gateway.routing) names the provider
and canonical slug that actually ran; it is required, must be TypeSafe, must be the only attempt
(no fallback), and is held for the run like the model. A dated build id isn't exposed by the gateway
as far as its docs say; if the first real response carries one, it is recorded too.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINT = "https://ai-gateway.vercel.sh/typesafe/v1/systemone"   # Vercel AI Gateway, TypeSafe shape
MODEL = "typesafe-ai/jev-1.13.0"   # pinned; listed by GET /v1/models. Never the moving "typesafe-ai/jev"
# Two Nouls per candidate (rev 4). The gate's J score is P_PRIMARY's probability: frozen before
# the pilot, which only reports how much asking both changes the constrains answer (rev 7).
NOULS = {
    "bears_on": {
        "question": "Does `memory` bear on this message?",
        # Rev 9 (Wren b116e8ff): not topical only, since POSITIVE includes standing rules that
        # aren't about the message's topic; and supersession judged from the message alone.
        "criteria": {"true": "The memory bears on how to reply to this message: it's about the same "
                             "thing, or it states a rule, fact or commitment that applies to this "
                             "reply, so a reply drawing on it would be more accurate, more complete "
                             "or more correct",
                     "false": "The memory is about something else and sets no rule for this reply, "
                              "or this message shows it's an older state that no longer holds"}},
    "constrains": {
        "question": "Does `memory` constrain what the agent should do in reply to this message?",
        "criteria": {"true": "The memory states a fact, decision, rule or commitment the reply has to "
                             "respect or act on",
                     "false": "The memory is only loosely related, or the reply would be the same "
                              "without it"}},
}
P_PRIMARY = "bears_on"
BOTH = ("bears_on", "constrains")
# Will's decision (b), 2026-10-18: no zero-data-retention flag. The key's Vercel team is on Hobby,
# where the gateway refuses the flag with 403 ZdrUnauthorizedError. Protection rests on Vercel's
# listed policy (zdr/no_training "all" for jev-1.13.0) and TypeSafe's terms, unverified. Turning
# this on again needs the team on Pro; it is never turned off for another reason without Will.
ZDR_FLAG = False
# Pre-send provider restriction (Wren c206270e). The routing check in gate() reads the RESPONSE,
# so it can only stop the next turn; "only" stops the gateway sending this one anywhere else. The
# per-request filter is on every plan. A synthetic probe must show it's honoured on this endpoint
# (a non-serving provider in "only" must fail) before it counts as a guard.
ONLY = ["typesafe-ai"]


def request(message: str, candidates: list[tuple[str, str]], model: str = MODEL,
            session_id: str | None = None, nouls: tuple[str, ...] = BOTH) -> dict:
    """The request body for one turn. `candidates` is [(item id, memory text)]. Question keys are
    f"{noul}:{i}", so an answer can't be read under the wrong question."""
    body = {"model": model, "state": {"message": message},
            "questions": {f"{n}:{i}": {"type": "noul", "criteria": NOULS[n]["criteria"],
                                       "instructions": {"memory": text,
                                                        "question": NOULS[n]["question"]}}
                          for n in nouls for i, (_, text) in enumerate(candidates)}}
    gateway: dict = {"only": list(ONLY)}
    if ZDR_FLAG:
        gateway["zeroDataRetention"] = True
    body["providerOptions"] = {"gateway": gateway}
    if session_id:
        body["session_id"] = session_id      # run id, for grouping; drop if the gateway rejects it
    return body


class BuildChanged(RuntimeError):
    """The service answered with a different build than earlier in the run: stop, don't mix."""


class Retryable(RuntimeError):
    """A transient failure (429, 529, 5xx, unreachable): safe to retry the same turn."""


class Jev:
    def __init__(self, key_file: Path, model: str = MODEL, timeout: float = 30.0, send=None,
                 session_id: str | None = None) -> None:
        self._key = Path(key_file).read_text().strip()
        if not self._key:
            raise ValueError(f"{key_file} is empty")
        self.model = model
        self.build: str | None = None        # the dated build that answered first
        self._session = session_id
        self._timeout = timeout
        self._send = send or self._http

    def _http(self, body: dict) -> dict:
        req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {self._key}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            msg = f"Jev HTTP {e.code}: {e.read()[:300]!r}"
            if e.code in (429, 529, 500, 502, 503, 504):
                raise Retryable(msg) from None
            raise RuntimeError(msg) from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise Retryable(f"Jev unreachable: {e}") from None

    def gate(self, message: str, candidates: list[tuple[str, str]],
             nouls: tuple[str, ...] = BOTH) -> dict:
        """{"p": {item: P_PRIMARY probability}, "nouls": {noul: {item: probability}}, "model":
        what answered, "usage", "cost", "seconds"}. "p" is None when P_PRIMARY wasn't asked."""
        body = request(message, candidates, self.model, self._session, nouls)
        t0 = time.monotonic()
        resp = self._send(body)
        seconds = time.monotonic() - t0
        routing = ((resp.get("provider_metadata") or {}).get("gateway") or {}).get("routing") or {}
        provider, slug = routing.get("finalProvider"), routing.get("canonicalSlug")
        if not resp.get("model") or not slug:
            raise RuntimeError("Jev response names no model or routing slug, so what answered is unknown")
        if provider != "typesafe-ai":
            raise RuntimeError(f"answered by {provider!r}, not TypeSafe")
        if len(routing.get("modelAttempts") or [None]) != 1:
            raise RuntimeError("more than one model attempt: a fallback may have answered")
        build = f"{resp['model']}|{slug}|{provider}"
        if self.build is None:
            self.build = build
        elif build != self.build:
            raise BuildChanged(f"build changed mid-run: {self.build} -> {build}")
        answers = resp.get("answers") or {}
        got = {}
        for n in nouls:
            got[n] = {}
            for i, (item, _) in enumerate(candidates):
                a = answers.get(f"{n}:{i}")
                if a is None:
                    raise RuntimeError(f"Jev returned no {n} answer for {item}")
                if a.get("type") != "noul" or not 0.0 <= float(a["noul"]) <= 1.0:
                    raise RuntimeError(f"Jev answer for {item} isn't a noul probability: {a!r}")
                got[n][item] = float(a["noul"])
        cost = ((resp.get("provider_metadata") or {}).get("gateway") or {}).get("cost")
        return {"p": got.get(P_PRIMARY), "nouls": got, "model": build, "usage": resp.get("usage"),
                "cost": cost, "seconds": seconds}
