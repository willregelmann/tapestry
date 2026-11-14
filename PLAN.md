# Tapestry: build plan

How we build what [`docs/`](docs/INTENT.md) describes. The map says *what* must hold, and this plan says *how* and *in what order*. If the plan and the map disagree, the map wins, and this plan gets fixed.

## Context

Tapestry replaces `mnemonic` (`~/.hermes/plugins/mnemonic/`), a single-file Hermes memory provider. mnemonic stores facts in SQLite, finds them with MiniLM ONNX embeddings plus FTS5 keyword search, and labels each result with a cosine "confidence band". It works, but it has several gaps:
- no scoping
- no model of how a fact changes over time
- no way to verify or retire a fact
- no structure between memories
- only one host

Mnemosyne was reviewed as a fork candidate and rejected. Its defaults work against strict isolation, and it has no belief maintenance.

## Architecture

### Packages
- **`tapestry` core:** a Python library that holds all the logic. Hosts never talk to storage directly.
- **Hermes adapter:** a `MemoryProvider` that calls the core in-process. Mnemosyne's `MnemosyneMemoryProvider` is the wiring reference.
- **Claude Code plugin:** hooks that call a `tapestry` command-line tool at each [moment](docs/interfaces/HOOKS.md), plus an MCP server for the [agent tools](docs/interfaces/TOOLS.md). The Agent SDK loads the same hooks and MCP server.
- **Scheduler:** a small runner triggered at session end, plus a lightweight timer. It runs [background jobs](docs/interfaces/JOBS.md) independently of the host.
- **Viewer:** `tapestry view` serves one static page and a little JSON from a stdlib HTTP server on 127.0.0.1 (`tapestry.viewer`, `viewer.html`), for the [viewer](docs/interfaces/VIEWER.md). The mind is opened with `Mind.reading`: SQLite `mode=ro` plus `query_only`, so the store refuses every write, and a mind on another schema version, or missing a table this version would add, is refused rather than migrated. Labels come from the same `render` and `belief` functions as the agent's; Try recall runs `recall.before_turn` and shows `render.automatic` verbatim, beside a deliberate `Mind.recall` at the tool's limit. Notices about work that is behind are read with `Ledger.peek`, so the next session is still told. Every request carries a random token and a loopback `Host`, and the page's CSP allows connecting only to itself. The web view is a hand-written canvas force layout: no third-party script to vendor, and nothing fetched. In Claude Code, the `view` skill (`/tapestry:view`) runs `bin/tapestry --plugin-data ${CLAUDE_PLUGIN_DATA} view --detach`, since a skill's command can't set the environment: the launcher takes the data dir as an argument. `--detach` starts the server in its own session, prints its address, and the server shuts down after 30 minutes without a request. The launcher's source fingerprint covers `.html` as well as `.py`, so a change to the page rebuilds the runtime.

### Storage
Each [mind](docs/primitives/MIND.md) is its own SQLite file, e.g. `~/.tapestry/minds/<owner>.db`. Owners are Ash and Wren (Hermes profiles) and Will (Claude Code and the SDK). Owner keypairs, signed writes and per-mind encryption are deferred.

| Primitive | Storage |
|---|---|
| [Memory](docs/primitives/MEMORY.md) | `memories`: id, content (never edited, by trigger), embedding + model tag, volatility class, superseded_by, created_at. `drawn_from` names the episode turns a memory came from, so forgetting it can take their words |
| [Episode](docs/primitives/EPISODE.md) | `episodes`: session id, place, started_at, ended_at, `drawn_through` (the last turn facts were drawn from). `turns`: one row per thing the user or the agent said, never edited, keyed so a transcript read twice adds it once. `recalled`: which memories were in mind at each moment. `spent_turns` keeps only the keys of turns that expired or were forgotten, so they never come back |
| [Association](docs/primitives/ASSOCIATION.md) | `associations`: src, dst, strength (0–1), last_used_at. Directed, one kind, unlabeled. Written only by `Mind.remember`, both ways, at 0.5 |
| [Evidence](docs/primitives/EVIDENCE.md) | `evidence`: memory_id, kind (observe / confirm / contradict / supersede / file / unfile), source, strength, episode ref, trigger, reasoner model if any, ts. Only ever added to |
| [Topic](docs/primitives/TOPIC.md) | `topics`: id, name, description. `memory_topics` joins memories to any number of them; a memory in none is in the general topic, which every lookup searches. "general" is never a topic name |

A mind written by version 2, which kept turns as memories in `session.*` namespaces, is migrated the first time it's opened (`tapestry.migrate`): a copy is kept, each turn moves into its session's episode, and the namespace tables become topic tables. A view named `scopes` makes runtimes from before refuse the file rather than recreate their old tables in it.

### Belief

The belief model was chosen from the comparison in `research/confidence_sim.py`.

```
p_now = σ(L) · 2^(−(t − t_L)/h_class)                 # right when last checked × still true now
on evidence: L ← clamp(logit(p⁻) ± w·logit(r_source), ±6);  t_L ← t_evidence
```

- **Half-lives by volatility class:**
  - immutable: 50y
  - stable: 3y
  - preference: 1y
  - project: 60d
  - transient: 3–14d
  - dated: an explicit end date
- **Source reliability defaults:**
  - user confirmed when asked: .98
  - user said it: .95
  - tool: .90
  - web: .75
  - another agent: .70
  - agent inference: .65
  - silence after a stated assumption: .60
  - the agent's own use of a memory: 0
- Nothing is cached: belief is replayed from evidence on every read.

### Judgment
A model proposes and the mind decides ([Memory](docs/primitives/MEMORY.md)). The judge is Jev through the Vercel AI Gateway, pinned to `typesafe-ai/jev-1.13.0` and restricted to TypeSafe on every request. Will agreed to memories going to it (2026-10-20), for judging only. The consent in `bench/gate/README.md` for Foil's transcripts still stands as written for that experiment. No zero-data-retention flag is sent, because the gateway refuses it on the plan the key is on, so protection rests on Vercel's and TypeSafe's published terms.
- **Conflicts.** The background run (`tapestry jobs`, started by the session-end hook in Claude Code and in Hermes, whatever other sessions are open; one at a time, under a file lock, and capped per run) compares each current memory with its nearest neighbours. A pair Jev rates at 0.7 or more, or Claude calls a conflict, is recorded as `contradict` evidence on both memories, from the other one's source, naming the prompt and the model. Neither is superseded: which is right takes more than the model's say-so. Recall shows the pair together and labels each CONFLICTS with the other, only when both are visible to the lookup.
- **Who judges, in the plugin.** The plugin's `userConfig` (`judge`, `jev_key`) reaches the hooks, and so the job, as `CLAUDE_PLUGIN_OPTION_JUDGE` and `CLAUDE_PLUGIN_OPTION_JEV_KEY`, and it takes precedence over the file and the command. The runtime is built by `bin/tapestry` into `${CLAUDE_PLUGIN_DATA}` and rebuilt when a fingerprint of `pyproject.toml` and `src/` changes; minds and the model stay in `~/.tapestry`.
- **Who judges.** `tapestry judge-with jev|claude|none` writes `~/.tapestry/config.json` and prints what is sent where. With no choice, Jev is used for conflicts if its key is there (`$TAPESTRY_JEV_KEY_FILE`, or `~/.tapestry/jev.key`), since placing the key is the owner's act; Claude is used only when chosen; otherwise nothing is sent. A model that was chosen but can't run is reported to the next session and never swapped for another.
- **Measured twice** (`bench/judge_compare.py`; 30 LongMemEval knowledge-update statements with 10 candidates each, then 30 different ones, settings fixed before the second; 31 real old-value turns each; the labels count a changed value as a conflict, which understates every judge). On the held-out set: Jev at a 0.7 cut-off found 15 of 31 with 0 false conflicts in 248, at 0.28s median and $0.000074 a statement; Haiku through `claude -p --effort low` found 17 with 3 false, at 5.3s and $0.00048; Haiku at default effort found 17 with 6 false, at 5.7s and $0.00077. Jev at 0.9 found 2 on both sets, so 0.9 was wrong and the cut-off is now 0.7. Haiku at low effort is the default for `ClaudeJudge`. Neither judge is wired as the default yet.
- **Not done.** Spend isn't tracked beyond a cap of 25 memories and 10 episodes per run.

### Drawing facts
[Consolidate](docs/capabilities/CONSOLIDATE.md) is `tapestry.consolidate`, run by `tapestry jobs`. Only Claude draws (`ClaudeDrawer`: `claude -p --safe-mode`, Haiku, no tools, structured output), and only when the owner chose `claude`; Jev answers yes/no questions and can't write facts. With no drawer, episodes wait and every session start says how many.
- **What the model sees.** One window of turns at a time (48k characters, 8k per turn), numbered; the date; the place; every topic's name and description; and up to 40 known memories: those recalled into those turns, plus the nearest three to each user turn across all topics.
- **What it returns.** Facts, each with the turns it cites, who said it (user, agent or inferred), topics, volatility, and optionally `same_as`, `replaces` and `builds_on`, which must be ids it was shown.
- **What's fixed whatever it says.** A fact that cites no turn is dropped. A claim put in a speaker's mouth that none of its cited turns has them saying becomes `inference`. `same_as` adds confirm evidence instead of a copy. `replaces` supersedes through `Mind.remember`, with the supersede evidence attributed to `inference`. A fact with no topic is filed under the episode's place. Facts from one window are linked to each other and to what they build on; if a window taught anything, associations among what was recalled into it strengthen. Keys are derived from episode, window and content, so a rerun adds nothing.
- **Episodes go** 30 days after they end, and only once every turn has been drawn (`episode.expire`).
- **Measured once**, on a made-up five-turn conversation: Haiku drew four sensible facts, superseded the stale deploy branch, and dated "next week" from the conversation's date, for $0.001 in 7s. Not yet measured on real episodes.

### Recall
1. Semantic plus FTS5 keyword search over the general topic plus the searched topics finds seed memories, with rankings merged by RRF.
2. One hop along associations from the seeds, to memories this lookup can see, above a floor of 0.25 after fading (half-life 180 days) and after scaling down whatever flows into one memory past 2.0, counting only visible sources. Up to half as many again as were asked for, each labelled with the memory that brought it. Personalized PageRank waits until one hop is measured.
3. Results are ranked and labelled, e.g.
   `[match: LOW] [belief: likely 0.78 · you said it, confirmed 5mo ago · preference]`
4. The automatic path (`tapestry.recall`) shows highlights and hints. Before a turn, highlights are up to three results with `match: ok` (cosine 0.45 or more); at session start, the best three for the place. Hints count the other top-ten results at cosine 0.35 or more, or brought to mind by a highlight, by topic, and name up to three unopened topics holding a memory at cosine 0.35 or more with the moment (`Mind.bearing`), best first, by name only. Hints were first judged from each topic's `name: description` at cosine 0.15 or more; the numbers that follow were measured that way. On the crowded LongMemEval mind (`python -m bench.extract crowd`) the answer's facts were among the hinted matches for 29 of 30 questions, and the best two topics by description held them for 29 of 30. With a real agent (`python -m bench.follow`, Sonnet), following hints with `tapestry_recall` answered 57% against 60% for injecting every topic's top five, from 228 characters shown automatically against 1,438; the agent looked for 28 of 30 questions and its lookups held the answer's facts 96% of the time. Once drawing replaced a newer value's older one (`draw_claude_v2`), following hints answered 72% against 62% for injecting, and every knowledge-update question; the misses left are counting across sessions, where some facts were never drawn.

### Host lessons carried over from mnemonic
- **Hermes admission check:** the strings `register_memory_provider` / `MemoryProvider` must appear in the first 8KB of `__init__.py`.
- **Name:** don't reuse a bundled provider's name.
- **Tool schemas:** use `parameters`, not `input_schema`.
- **Escaping:** every write escapes `fence_safe` sequences, because Hermes' sanitizer deletes them from memory content.
- **`is_available()`:** probes the model files and database integrity.
- **Failures:** an initialization failure is written to a log that survives restarts.

## Stages

### 0. Test harness and baseline
- **pytest harness:** seeds a mind from fixture files and reports:
  - recall@k and MRR
  - the rank of the expected memory
  - **stale-serve rate** (how often a superseded memory is served)
  - **topic leakage**: anything served, or anything that influenced ranking, from a topic the query didn't search
  - belief calibration (Brier score, ECE)
- **Fixtures:**
  - mnemonic's 27-query corpus (from `BANDS.md`), ported
  - scenarios where memories change and supersede each other
  - reconfirmation scenarios
  - scenarios that cross topics, including memories filed under several
  - a LongMemEval-S subset, with a loader
- **Scenario runner:** drives an agent headlessly (Agent SDK, and Hermes) against a seeded mind, N runs per scenario, saving transcripts. `../fuzzy-assertions` (`test:run`) judges behaviour:
  - hedges on low belief
  - reconfirms when it should
  - doesn't nag
  - doesn't assert superseded memories
- **Results log:** JSONL, keyed by commit and configuration.
- **Exit:** the harness runs end to end against current mnemonic, and the baseline is recorded.

### 1. Core, Remember and Recall, both hosts
- **Core:** storage, topics, [Remember](docs/primitives/MEMORY.md), [Recall](docs/capabilities/RECALL.md) (seeds and labels, no spreading yet), and the read-only tools.
- **Hosts:**
  - Hermes adapter
  - Claude Code plugin (hooks + MCP)
  - built-in memory writes recorded as evidence
  - the Agent SDK running the same plugin
- **Migration:** a one-way import from `~/.hermes/memory_store.db` into Ash's mind, with a backup taken first.
- **Exit:**
  - at least as good as mnemonic's baseline on the 27-query corpus and LongMemEval;
  - zero topic leakage and zero cross-mind reads;
  - the same scenario gives the same recall in Hermes and in Claude Code.

### 2. Belief: Revise, Verify, Forget
- **[Revise](docs/primitives/MEMORY.md):**
  - the hazard filter;
  - volatility classification: the reasoner classifies, and a table of per-predicate defaults overrides it;
  - supersession for single-valued predicates;
  - "no longer valid when" conditions stored as memories, matched by embedding, then confirmed by the reasoner.
- **[Verify](docs/capabilities/VERIFY.md):**
  - asks only when being wrong would cost more than interrupting;
  - at most 1 ask per conversation and 3 per week, with a cooldown per memory;
  - "assume and flag" phrasing vs asking directly;
  - `record_answer`;
- **[Forget](docs/primitives/MEMORY.md):**
  - removal is complete, including the episode turns a memory was drawn from.
- **Exit:**
  - stale-serve rate well below Stage 1;
  - belief ECE < 0.05 on a labelled set;
  - behaviour judges pass on "reconfirms when it should" and "doesn't nag" across N runs.

### 3. Spreading recall
- One hop is built (see Recall). Associations are born with facts: drawn from one moment, or noted with `builds_on`.
- **Exit:** better on questions that need several related memories (multi-hop), with no loss on single-hop and zero topic leakage. Not yet measured.

### 4. Consolidate
- **[Consolidate](docs/capabilities/CONSOLIDATE.md)** is built (see Drawing facts). Not built yet: an entailment check before each write.
- **Soak test:** run consolidation 1, 3, 5 and 10 rounds over a fixed corpus.
  - accuracy must stay ≥ raw observations only;
  - exact match on specifics must drop by ≤ 1 point;
  - unsupported claims must stay < 1%;
  - association strength must not concentrate in a few memories.
- Re-batching the same material must give stable results.
- **Exit:** everything above passes. Otherwise it stays behind a flag.
- **Recurrence (benchmark only, `bench/recur.py`).** After RecMem (arXiv 2605.16045), with a decay in place of a time window: each exchange scores 1 plus, for every earlier conversation with a similar exchange (cosine 0.3 or more), e^(−age / 7 days) of its latest one, so a conversation counts once however often it repeats itself. Three builds are compared on `bench/patterns` (habits worth keeping, one-offs that aren't) and on the crowded LongMemEval mind:
  - *Draw only what recurs* skips the drawer for anything that hasn't recurred. It found 83% of habits for 22 calls against 58, but drawing nothing from a one-off conversation loses its facts; on LongMemEval every session crossed anyway, and following hints answered 70% against 72%.
  - *Hybrid* draws every episode as now and, when an exchange crosses 3.0, shows the earlier exchanges that put it over, dated, so the drawer can state a habit. A later exchange that recurs with at least two of the conversations a habit rests on is confirm evidence for it, attributed to inference, with no model call. The first prompt drew a fact for every occurrence and restated the habit each time (4 forbidden facts, up to four copies of one habit); with occurrences matched by the exchanges a habit rests on rather than its words, habits shown to the drawer, and habits stated in terms that stay true (`draw_hybrid_v3`), it found 67% of habits with 1 forbidden fact. Ten coffee mornings gave one habit confirmed six times, belief 0.97; a busy afternoon and five requests over a year gave none. The drawer still declines to call five PR reviews in a week a habit.
  - On LongMemEval the hybrid cost 69 calls and $0.20 for 61 sessions (every episode drawn: 61, $0.14). Following hints answered 87% (single-session 90, knowledge-update 100, multi-session 70) against 72% for drawing every episode, in one run of 30 questions. Over two more runs each it held at 87% (single-session 90, knowledge-update 100, multi-session 70) against 74% (75, 83, 65), and the agent's lookups held the answer's facts 100% of the time against 89%. The every-episode mind is missing one session whose drawing timed out, which costs it at most one question in thirty.
  - Not built into Consolidate: it needs exchange embeddings kept in the mind and removed by Forget, and a map change in [Consolidate](docs/capabilities/CONSOLIDATE.md) for habits drawn across episodes.

### 5. Refinement: measure on realistic sessions, then tighten

What real use showed on 2026-11-13, on the owner's mind, once Claude was chosen to draw:
- **Drawing keeps about one fact per turn.** Ten conversations gave 252 facts (one of 68 turns gave 71). Many are session narration ("Will said yes to running the held-out judge comparison"), approvals, install steps and agent proposals nobody accepted; 154 of the first 211 were the agent's own words. The prompt already rules these out and Haiku keeps them.
- **Recall's fixed bar misses good matches.** "rewrite our Dockerfile" scored 0.37 to 0.39 against the Dockerfile practices, under the 0.45 bar, so nothing was highlighted.
- **Topic hints are noise.** Three empty topics outranked the one that held the answer (0.32, 0.22, 0.21 against 0.19), because project topics are described by their path. Unrelated prompts get hints every turn.
- **The pairwise conflict pass found nothing** in 114 pairs, while drawing's own `replaces` superseded 6.
- Background drawing is paused (the plugin's model set to `none`) until step B passes.

None of this was caught beforehand: the extraction fixtures are two-turn conversations and the recall benchmarks are LongMemEval, which is chat about daily life. So the evals come first.

**A. Evals.** Each fails the run past a threshold, fixed and written down before the change it judges.
- *Long sessions* (`bench/extraction`, same harness): hand-labelled conversations of 44 turns with plans, approvals, status chatter and long agent reports, across coding (`work-long-rate-limit`), operations (`ops-long-incident`) and home use (`home-long-guests`), each marked `complete` so anything drawn beyond its expected facts counts against precision and facts per turn. Haiku, two runs each: with short agent turns, recall 0.96, supported 1.0, 4 forbidden, precision 0.78, 0.27 facts per turn; with agent reports as long as real ones (several hundred characters, the longest about 1,500), recall 1.0, supported 0.99, 4 forbidden ("the user approved rolling back", "the agent posted an update", "all 318 tests pass", "the agent pushed the branch"), precision 0.65, 0.35 facts per turn, 17 to 23 facts from each work session. Longer reports raise the rate, but the fixtures still don't reach the one fact per turn the owner's mind shows: they are tidier than real sessions.
  - The forbidden lists of five short fixtures (`home-correction`, `home-guest-visit`, `agent-claims`, `personal-pasted-email`, `work-decision`) sat after an `[[expect]]` table, so TOML read them as part of it and they were never checked: their earlier "0 forbidden" results were vacuous. The loader now refuses a key under the wrong table. Run again with them checked (Haiku, two runs each): 0 forbidden, recall 0.98, supported 0.98, attribution 0.97, filing 1.0, replaced and kept 1.0.
- *Later prompts* (`bench/later.py`): each extraction fixture carries `[[later]]` prompts a later session would plausibly send, naming the expected facts each should bring, or none. Every fixture goes into one crowded mind, the LongMemEval mind of 61 sessions and 29 topics as distractors, either as its expected facts written straight in (*ideal*: recall measured with drawing held still) or drawn by Haiku and judged once for which drawn memory states which expected fact (*drawn*, saved under `bench/data/later`). 45 prompts, 9 wanting nothing. Scored on the automatic path: wanted facts highlighted; wanted facts *led to*, highlighted or returned by a lookup of the topics a hint names; highlights outside the fixture's subject (another conversation's facts, narration); prompts wanting nothing that get a highlight; topics hinted. And on the lookup path, with the right topics named, whether prompts wanting several facts get all of them. Thresholds are fixed from the as-built arm, boundary with the fixed bar, before any change: ideal led to ≥ 0.65, unwanted ≤ 0.15 a prompt, unrelated prompts with a highlight ≤ 0.15; drawn 0.6, 0.35, 0.15.

  | Arm (searched / bar) | Ideal: shown | led to | unwanted a prompt | unrelated with a highlight | topic hints | Drawn: shown | led to | unwanted | unrelated | topic hints |
  |---|---|---|---|---|---|---|---|---|---|---|
  | boundary / fixed, as built | 0.38 | 0.68 | 0.08 | 0.11 | 1.64 | 0.30 | 0.64 | 0.31 | 0.11 | 1.67 |
  | boundary / z3 | 0.38 | 0.68 | 0.14 | 0.33 | 1.64 | 0.21 | 0.64 | 0.19 | 0.11 | 1.67 |
  | peek / fixed | 0.38 | **0.83** | 0.08 | 0.11 | **0.60** | 0.30 | **0.81** | 0.31 | 0.11 | **0.84** |
  | everything / fixed | 0.47 | 0.81 | 0.31 | 0.11 | 0 | 0.49 | 0.79 | 0.58 | 0.11 | 0 |
  | everything / z3 | 0.68 | 0.83 | 0.83 | 0.78 | 0 | 0.64 | 0.79 | 1.28 | 0.67 | 0 |

  - The boundary holds out what isn't wanted: searching everything quadruples unwanted highlights on the ideal mind and nearly doubles them on the drawn one, for about ten more points of facts shown.
  - Where the boundary loses is a session with no place, a home assistant's: facts filed in `house` or `family` are never highlighted there (drawn: 0 of 20), and description hints lead to only 30% of them, against 89% where the session has a place. "I'm baking cookies for Maya's birthday" hints at `cocktails`, `cooking` and `yoga`; `family` is described as "the user's family and friends", which no prompt about Maya matches.
  - *Content hints*, hinting at an unopened topic when one of its memories matches past the hint floor rather than by its description, lead to 81–83% of wanted facts, as many as searching everything, with the boundary's unwanted highlights and half as many topic hints. They name the topic only; which memory matched, and what it says, stay hidden. Built, with [Topic](docs/primitives/TOPIC.md) changed to allow it (the owner's decision, 2026-11-13); the table's *peek* row is the arm as built now, and the arm called `described` keeps the old hints for comparison. Where the session has a place it is slightly worse on the drawn mind (led to 85% against 89%: one prompt), and a topic whose description fits but which holds nothing bearing on the moment is no longer hinted. With a real agent on the crowded LongMemEval mind (`bench.follow`, Sonnet, one run of 30 questions) following content hints answered 72% (single-session 80, knowledge-update 100, multi-session 40) and its lookups held the answer's facts 96% of the time, against 72–74% and 89–96% with description hints; the multi-session misses are counting across sessions with the facts in hand.
  - The relative bar (a highlight stands 3 or 4 deviations above how the moment scores against everything searched, floor 0.30) does nothing for what is shown inside the boundary, and across everything it shows more by showing far more noise. Rejected.
  - On the drawn mind most unwanted highlights are the drawer's own narration: "The user asked for rate limiting on the ledgerly public API because one customer's sync script…" was highlighted for four of the eleven ledgerly prompts, above the facts they wanted. Drawing selectivity shows up in recall.
  - Associations change nothing on the lookup path: as built, none, fan, converge and both find the same on the drawn mind (all of the wanted facts for 6 of 7 prompts); converge finds one more on the ideal mind. Seven prompts is too few to decide on.
- *Crowded soak*: the later-prompts mind already holds 74 sessions and 30-odd topics; what remains is vague and path-like topic descriptions, and more sessions drawn the way the owner's are (open question 8).
- *Own-session replay* (local, never committed): a copy of the owner's mind redrawn under a candidate change; memories per conversation and a judged sample of how many are worth keeping. Held out: it confirms fixture gains, and nothing is tuned on it.

**B. Drawing selectivity**, against the long-session eval. Candidates, cheapest first: the prompt with narration and unaccepted proposals as named negatives; a second pass that keeps or drops each drawn fact; Sonnet against Haiku (open question 9).
- *A second pass* (`bench/keep.py`): one question of each drawn fact on its own, without the conversation, "would this still be worth knowing in a later conversation, weeks from now?", over 94 facts drawn from the complete fixtures (61 that state an expected fact, 33 that don't). Haiku kept 89% of the first and dropped 6% of the rest ($0.005, 30s); Jev at cut-offs 0.3, 0.5 and 0.7 kept 97, 87 and 64% and dropped 12, 24 and 67% (24s). Neither separates them. Reading the disagreements, many facts outside the lists are worth keeping (an alert routed to a retired rotation, a rate limiter that fails open), so the lists were too narrow; and both models drop plans that end soon (guests' dates, a postmortem due Friday), which the question's "weeks from now" invites. Not adopted. Association density (908 links, up to 15 on one memory) follows from how many facts share a turn, so it is measured again after this step before anything about linking changes.
- *Windows* were the first structural suspect, and aren't it: a window reads up to 48,000 characters, so the fixtures and most real sessions are drawn in one call that sees how the conversation ends.
- *A budget* (built, `ClaudeDrawer`, `TURNS_PER_FACT = 4`): at most one fact for every four turns a call reads, and always room for one, stated in one sentence and enforced in the schema and after. People keep the gist of a conversation, not a record of it ([Consolidate](docs/capabilities/CONSOLIDATE.md) now says what a conversation leaves is strictly limited by its length). The value was chosen once and not tuned. Measured on 2026-11-13 against Haiku as it was and Sonnet without a budget:

  | | Haiku | Haiku, budget | Sonnet |
  |---|---|---|---|
  | Long fixtures, two runs each: expected facts found | 1.0 | 1.0 | 1.0 |
  | precision | 0.69 | **0.91** | 0.78 |
  | facts per turn | 0.32 | 0.24 | 0.22 |
  | forbidden drawn | 2 | 1 | 3 |
  | Own-session replay, 5 real conversations, 192 turns: facts per turn | 0.94 | **0.27** | 0.32 |
  | agent's words / user's | 144 / 36 | 37 / 14 | 49 / 13 |
  | cost | $0.07 | $0.04 | $0.69 |
  | Later prompts, drawn mind: off-subject highlights a prompt | 0.31 | **0.06** | |
  | wanted facts led to | 0.81 | 0.85 | |

  The replay reproduces what real use showed (0.94 facts a turn), which the fixtures never did. With the budget, one wanted fact of the later prompts was not drawn. The budget was close to binding on real sessions (51 facts against a limit of about 55), so what it keeps rests on the model choosing well; reading what each arm drew from one 64-turn conversation, the budget kept the decisions, the findings and the unfinished work, and Sonnet's were a little better written. The replay's copies and what was drawn stay on the owner's machine.

**C. Recall and hints**, against later prompts and the soak.
- The topic boundary stays for what is surfaced automatically (the owner's decision, 2026-11-13); an agent crosses it by opening a topic. Measured on the owner's mind, on this machine only: 400 real prompts from conversations not yet drawn, against 279 memories. With the boundary a lookup searches about 32 memories and a prompt of 25 characters or more gets 0.25 highlights; with none it gets 0.58, of which 0.40 are filed under another project, and most of those read as another project's narration. The best cosine against N memories picked at random rises with N alone (0.24 at 10, 0.29 at 30, 0.34 at 100, 0.37 at 279), and the share of prompts where one clears 0.45 rises from 4% to 24%. So a fixed bar loosens as whatever is searched grows, inside a topic as well as across them. Nothing here says which highlights were wanted: that needs the later-prompts eval.
- A clear match judged against how the rest of the mind scores for that moment, in place of a fixed 0.45: measured as a z-score bar on later prompts and rejected (see A).
- Topic hints from what a topic holds: built (see A). A topic that holds nothing is never hinted, so the empty project topics stop crowding out the one that holds the answer.
- "N memories match loosely" when nothing was highlighted.
- A project's topic is made when the first fact is filed there, not when a session opens in the folder. A topic still described by its path gets a description of what it holds when facts are drawn into it.

**D. What the agent is shown.** The guide and the labels on each result cost context every turn: measure both, and trim what the scenario runner shows the agent doesn't use.

**E. Cuts, each decided by a measurement.**
- The two-decimal belief number, if the scenario judges pass as well on the word and the reason alone.
- The pairwise conflict pass and Jev, if drawing's `replaces` finds what the pass finds on the conflicts corpus.
- Strengthening, fading and the inflow cap on associations, if one-hop recall does no better with them on multi-hop questions (Stage 3's exit, still unmeasured).

**F. Housekeeping.**
- The plugin's runtime is rebuilt only at session start, so an update taken mid-session isn't used until the next one.
- A host memory file is recorded whole, frontmatter included, which isn't a fact.
- Clearing a bad draw: `tapestry redraw` takes back what was drawn from conversations and leaves their words to be drawn again ([Memory](docs/primitives/MEMORY.md), [Commands](docs/interfaces/COMMANDS.md)). On 2026-11-13 the owner's mind had 693 drawn memories from 20 conversations, 432 of them from a second run after drawing was paused, because a session open from before the pause still carried the old setting; jobs now read the setting as it stands. All 693 were taken back, leaving the 28 memories noted by hand and 152 conversations waiting for step B.

### Deferred
- [Share](docs/capabilities/SHARE.md)
- [Calibrate](docs/capabilities/CALIBRATE.md): learning source reliability from answers (Dawid–Skene), and volatility per class from observed changes
- Syncing a mind across machines, so Connect and Disconnect in [Commands](docs/interfaces/COMMANDS.md): a mind is a local SQLite file for now
- Checks on who a note says it came from: the agent is trusted to write its own memories accurately
- Owner keypairs and signed writes: add alongside Share, or when a mind first lives off this machine.
- Encrypting each mind with its owner's key
- A profile card that is always injected into context
- Learning volatility per predicate (needs ≥ 20 observed changes)

## Open questions
2. **Reverting:** can a changed memory become true again (pink → blue → pink)? The model assumes not, so a reverted value is a new memory.
3. **Stakes:** should the stakes of acting on a memory be rated by the reasoner per task, or tied to tool risk tiers?
4. **Belief display:** show the agent one number (`p_now`), or both factors (right when last checked, still true now)?
5. **Hermes topics:** Hermes sessions have no place, so none open by default. Should anything open topics for Ash automatically?
6. **Cross-host minds:** should Will's mind be shared by Claude Code and Hermes when Will talks to Hermes directly, or should they be separate minds?
7. **Naming:** the Hermes provider's name, and the on-disk layout.
8. **Topic misses:** measured in the namespace-miss scenario (the agent has the tools and must choose). With the guide, the agent searched the right unopened topic in 3/3 runs, so no hints for now. Worth rechecking with more topics and vaguer descriptions.
9. **Which model draws:** Haiku is cheap and was good on one made-up conversation. Measure it against Sonnet on real episodes before the 1,210 migrated turns are drawn.

## Key references
- **Staleness and supersession:** Zep/Graphiti arXiv 2501.13956, MemStrata 2606.26511, STALE 2605.06527
- **Consolidation risk:** 2605.12978 (memories degrade under repeated LLM rewrites), HaluMem 2511.03506, LongMemEval 2410.10813 (add derived facts as extra search keys, don't replace)
- **Spreading recall:** HippoRAG 2 2502.14802; RAG vs GraphRAG 2502.11371
- **Belief:** Nous 2606.22030, Hindsight 2512.12818, MemTX 2607.23929; Horvitz CHI'99 (when to interrupt)
- **Human memory:** Schacter, *The Seven Sins of Memory* (1999); Anderson & Schooler (1991); McClelland et al., complementary learning systems (1995); Turrigiano (synaptic scaling); Godden & Baddeley (context-dependent memory)
