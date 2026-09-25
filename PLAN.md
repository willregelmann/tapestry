# Tapestry: build plan

How we build what [`invariants/`](invariants/INTENT.md) describes. The invariants say *what* must hold. This plan says *how* and *in what order*. If the two disagree, the invariants win, and this plan gets fixed.

## Context

Tapestry replaces `mnemonic` (`~/.hermes/plugins/mnemonic/`), a single-file Hermes memory provider. mnemonic stores facts in SQLite, finds them with MiniLM ONNX embeddings plus FTS5 keyword search, and labels each result with a cosine "confidence band". It works, but it has several gaps:
- no scoping
- no model of how a fact changes over time
- no way to verify or retire a fact
- no structure between memories
- only one host

Research into Mem0, Honcho, Zep/Graphiti, Letta, Hindsight, Mnemosyne and 2023–2026 papers found:
- **Staleness is the best-evidenced failure.**
- **LLM rewriting of memory content degrades it**, while adding derived memories next to the raw ones doesn't.
- **Nobody handles natural-language invalidation conditions or verification by asking the user.**

Mnemosyne was reviewed as a fork candidate and rejected. Its defaults work against strict isolation, and it has no belief maintenance.

## Principles

1. **Fallibility is a side effect, not a flaw.** Default to the mechanism human memory uses. Deviate only when the pressure that shaped it doesn't apply AND a test shows the deviation helps. Accountability comes from the evidence log, the way people use notebooks.
2. **Nothing ships without a measurement.** Every stage has exit criteria checked by the test harness. Features that fail stay behind a flag.
3. **Add, don't rewrite.** Memory content is never edited. It's superseded, and evidence is only ever added.
4. **Fail loudly.** A mind that wasn't consulted never looks like a mind with nothing to say.

## Architecture

### Packages
- **`tapestry` core:** a Python library that holds all the logic. Hosts never talk to storage directly.
- **Hermes adapter:** a `MemoryProvider` that calls the core in-process. Mnemosyne's `MnemosyneMemoryProvider` is the wiring reference.
- **Claude Code plugin:** hooks that call a `tapestry` command-line tool at each [moment](invariants/interfaces/HOST.md), plus an MCP server for the [agent tools](invariants/interfaces/AGENT.md). The Agent SDK loads the same hooks and MCP server.
- **Scheduler:** a small runner triggered at session end, plus a lightweight timer. It runs [background work](invariants/interfaces/SCHEDULE.md) independently of the host.

### Storage
Each [mind](invariants/primitives/MIND.md) is its own SQLite file, e.g. `~/.tapestry/minds/<owner>.db`. Owners are Ash and Wren (Hermes profiles) and Will (Claude Code and the SDK). Each owner has a keypair from day one and signs its writes. Encrypting each file with its owner's key is deferred.

| Primitive | Storage |
|---|---|
| [Memory](invariants/primitives/MEMORY.md) | `memories`: id, scope_id, content (never edited), embedding + model tag, volatility class, cached belief state (`L`, `t_L`), superseded_by, created_at |
| [Association](invariants/primitives/ASSOCIATION.md) | `associations`: src, dst, strength (0–1), last_used_at. Directed, one kind, unlabeled |
| [Evidence](invariants/primitives/EVIDENCE.md) | `evidence`: memory_id, kind (observe / confirm / contradict / supersede / rescope), source, source reliability used, strength, episode ref, trigger, reasoner model/prompt if any, ts. Only ever added to |
| [Scope](invariants/primitives/SCOPE.md) | `scopes`: id, name, parent (user-wide at the root). A session holds the set of loaded scope ids |

Every memory is the same kind of node. Its role comes from its evidence and connections, not a type column:
- **Observation:** a memory whose first evidence is `observe`.
- **Concept:** a hub, reached by many associations.

### Belief

The belief model is in `research/confidence_sim.py`. On synthetic histories it had the best Brier score and calibration of the models tested.

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
- `L` is a cache and can be rebuilt by replaying the evidence.

### Recall
1. Semantic plus FTS5 keyword search over loaded scopes finds seed memories, with rankings merged by RRF.
2. Activation spreads over associations whose two ends are both loaded (personalized PageRank).
3. Results are ranked and labelled, e.g.
   `[match: LOW] [belief: likely 0.78 · you said it, confirmed 5mo ago · preference]`

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
  - **scope leakage**: anything served, or anything that influenced ranking, from an unloaded scope
  - belief calibration (Brier score, ECE)
- **Fixtures:**
  - mnemonic's 27-query corpus (from `BANDS.md`), ported
  - scenarios where memories change and supersede each other
  - reconfirmation scenarios
  - scenarios that cross scopes
  - a LongMemEval-S subset, with a loader
- **Scenario runner:** drives an agent headlessly (Agent SDK, and Hermes) against a seeded mind, N runs per scenario, saving transcripts. `../fuzzy-assertions` (`test:run`) judges behaviour:
  - hedges on low belief
  - reconfirms when it should
  - doesn't nag
  - doesn't assert superseded memories
- **Results log:** JSONL, keyed by commit and configuration.
- **Exit:** the harness runs end to end against current mnemonic, and the baseline is recorded.

### 1. Core, Remember and Recall, both hosts
- **Core:** storage, scopes, owner keypairs and signed writes, [Remember](invariants/capabilities/REMEMBER.md), [Recall](invariants/capabilities/RECALL.md) (seeds and labels, no spreading yet), and the read-only tools.
- **Hosts:**
  - Hermes adapter
  - Claude Code plugin (hooks + MCP)
  - built-in memory writes recorded as evidence
  - the Agent SDK running the same plugin
- **Migration:** a one-way import from `~/.hermes/memory_store.db` into Ash's mind, with a backup taken first.
- **Exit:**
  - at least as good as mnemonic's baseline on the 27-query corpus and LongMemEval;
  - zero scope leakage and zero cross-mind reads;
  - the same scenario gives the same recall in Hermes and in Claude Code.

### 2. Belief: Revise, Verify, Forget
- **[Revise](invariants/capabilities/REVISE.md):**
  - the hazard filter;
  - volatility classification: the reasoner classifies, and a table of per-predicate defaults overrides it;
  - supersession for single-valued predicates;
  - "no longer valid when" conditions stored as memories, matched by embedding, then confirmed by the reasoner.
- **[Verify](invariants/capabilities/VERIFY.md):**
  - asks only when being wrong would cost more than interrupting;
  - at most 1 ask per conversation and 3 per week, with a cooldown per memory;
  - "assume and flag" phrasing vs asking directly;
  - `record_answer`, plus random audits;
- **[Forget](invariants/capabilities/FORGET.md):**
  - removal is complete;
  - memories derived only from what was forgotten go with it.
- **Exit:**
  - stale-serve rate well below Stage 1;
  - belief ECE < 0.05 on a labelled set;
  - behaviour judges pass on "reconfirms when it should" and "doesn't nag" across N runs.

### 3. Spreading recall
- Personalized PageRank over associations between loaded scopes, seeded by hybrid search. Start with the associations created at write time: each observation linked to what it mentions.
- **Exit:** better on questions that need several related memories (multi-hop), with no loss on single-hop and zero scope leakage. Otherwise it stays behind a flag.

### 4. Consolidate and Calibrate
- **[Consolidate](invariants/capabilities/CONSOLIDATE.md):**
  - the reasoner turns observations into memories, placed in scopes;
  - an entailment check before each write;
  - associations nudged by use, fading with time and capped per memory;
  - triggered by the scheduler.
- **[Calibrate](invariants/capabilities/CALIBRATE.md):**
  - learns source reliability from answers (Dawid–Skene);
  - learns volatility per class from observed changes, starting from the defaults and moving slowly.
- **Soak test:** run consolidation 1, 3, 5 and 10 rounds over a fixed corpus.
  - accuracy must stay ≥ raw observations only;
  - exact match on specifics must drop by ≤ 1 point;
  - unsupported claims must stay < 1%;
  - association strength must not concentrate in a few memories.
- Re-batching the same material must give stable results.
- **Exit:** everything above passes. Otherwise it stays behind a flag.

### Deferred
- [Share](invariants/capabilities/SHARE.md)
- Encrypting each mind with its owner's key
- A profile card that is always injected into context
- Learning volatility per predicate (needs ≥ 20 observed changes)

## Open questions
1. **Nudging:** what counts as "used together"? Candidates, strongest first: both memories contributed to an answer the user accepted; both were retrieved and cited together; both appeared in the same episode.
2. **Reverting:** can a changed memory become true again (pink → blue → pink)? The model assumes not, so a reverted value is a new memory.
3. **Stakes:** should the stakes of acting on a memory be rated by the reasoner per task, or tied to tool risk tiers?
4. **Belief display:** show the agent one number (`p_now`), or both factors (right when last checked, still true now)?
5. **Hermes scopes:** Hermes sessions have no obvious current project. What decides which project scope an Ash session loads?
6. **Cross-host minds:** should Will's mind be shared by Claude Code and Hermes when Will talks to Hermes directly, or should they be separate minds?
7. **Naming:** the Hermes provider's name, and the on-disk layout.

## Key references
- **Staleness and supersession:** Zep/Graphiti arXiv 2501.13956, MemStrata 2606.26511, STALE 2605.06527
- **Consolidation risk:** 2605.12978 (memories degrade under repeated LLM rewrites), HaluMem 2511.03506, LongMemEval 2410.10813 (add derived facts as extra search keys, don't replace)
- **Spreading recall:** HippoRAG 2 2502.14802; RAG vs GraphRAG 2502.11371
- **Belief:** Nous 2606.22030, Hindsight 2512.12818, MemTX 2607.23929; Horvitz CHI'99 (when to interrupt)
- **Human memory:** Schacter, *The Seven Sins of Memory* (1999); Anderson & Schooler (1991); McClelland et al., complementary learning systems (1995); Turrigiano (synaptic scaling); Godden & Baddeley (context-dependent memory)
