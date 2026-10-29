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
- **Conflicts.** The background run (`tapestry jobs`, started by the session-end hook in Claude Code and in Hermes, never while another session is in a conversation) compares each current memory with its nearest neighbours. A pair Jev rates at 0.7 or more, or Claude calls a conflict, is recorded as `contradict` evidence on both memories, from the other one's source, naming the prompt and the model. Neither is superseded: which is right takes more than the model's say-so. Recall shows the pair together and labels each CONFLICTS with the other, only when both are visible to the lookup.
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
4. The automatic path (`tapestry.recall`) shows highlights and hints. Before a turn, highlights are up to three results with `match: ok` (cosine 0.45 or more); at session start, the best three for the place. Hints count the other top-ten results at cosine 0.35 or more, or brought to mind by a highlight, by topic, and name up to three unopened topics whose `name: description` is at cosine 0.15 or more with the moment, never reading their contents. On the crowded LongMemEval mind (`python -m bench.extract crowd`) the answer's facts were among the hinted matches for 29 of 30 questions, and the best two topics by description held them for 29 of 30.

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
