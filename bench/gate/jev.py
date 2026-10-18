"""Ask Jev, per turn, whether each recall candidate constrains what the agent is about to do.

One request per turn: the state is the incoming message only, and each candidate is its own Noul
question carrying the memory in its instructions (TypeSafe's re-ranking pattern). Jev never sees
the reply, the cosine, or a label, so it can't be scored on anything it was shown.

The key is read from a file and never logged or put in an error. The model is pinned, and the
model id the service says answered is recorded on every call.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINT = "https://ai-gateway.vercel.sh/typesafe/v1/systemone"   # Vercel AI Gateway, TypeSafe shape
MODEL = "typesafe-ai/jev-1.13.0"   # pinned; listed by GET /v1/models. Never the moving "typesafe-ai/jev"
QUESTION = "Does `memory` constrain what the agent should do in reply to this message?"
CRITERIA = {"true": "The memory states a fact, decision, rule or commitment the reply has to respect "
                    "or act on",
            "false": "The memory is only loosely related, or the reply would be the same without it"}


def request(message: str, candidates: list[tuple[str, str]], model: str = MODEL,
            session_id: str | None = None) -> dict:
    """The request body for one turn. `candidates` is [(item id, memory text)]."""
    body = {"model": model, "state": {"message": message},
            "questions": {f"c{i}": {"type": "noul", "criteria": CRITERIA,
                                    "instructions": {"memory": text, "question": QUESTION}}
                          for i, (_, text) in enumerate(candidates)}}
    if session_id:
        body["session_id"] = session_id      # run id, for grouping; drop if the gateway rejects it
    return body


class BuildChanged(RuntimeError):
    """The service answered with a different build than earlier in the run: stop, don't mix."""


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
            raise RuntimeError(f"Jev HTTP {e.code}: {e.read()[:300]!r}") from None

    def gate(self, message: str, candidates: list[tuple[str, str]]) -> dict:
        """{"p": {item: probability}, "model": answering id, "usage": ..., "seconds": wall time}."""
        body = request(message, candidates, self.model, self._session)
        t0 = time.monotonic()
        resp = self._send(body)
        seconds = time.monotonic() - t0
        build = resp.get("model")
        if not build:
            raise RuntimeError("Jev response names no model, so the build can't be pinned")
        if self.build is None:
            self.build = build
        elif build != self.build:
            raise BuildChanged(f"build changed mid-run: {self.build} -> {build}")
        answers = resp.get("answers") or {}
        p = {}
        for i, (item, _) in enumerate(candidates):
            a = answers.get(f"c{i}")
            if a is None:
                raise RuntimeError(f"Jev returned no answer for {item}")
            if a.get("type") != "noul" or not 0.0 <= float(a["noul"]) <= 1.0:
                raise RuntimeError(f"Jev answer for {item} isn't a noul probability: {a!r}")
            p[item] = float(a["noul"])
        return {"p": p, "model": build, "usage": resp.get("usage"), "seconds": seconds}
