# Tapestry

Long-term memory for AI agents: facts that come to mind when they matter, and stay out of sight when they don't.

Whenever an agent is about to act, Tapestry answers one question: **what should I have in mind right now?** Read [`docs/INTENT.md`](docs/INTENT.md) for why, [`docs/`](docs/) for what must always hold, and [`PLAN.md`](PLAN.md) for how it's being built.

**Status:** Conversations are kept as episodes and the facts they taught are drawn from them in the background; facts are linked as they're written, and recall follows those links. Recall with separate match and belief labels, topics, supersession, forgetting and recording the user's answers work in Claude Code, the Claude Agent SDK and Hermes Agent. Verification and calibration aren't built yet; see [`PLAN.md`](PLAN.md).

## Claude Code

```
/plugin marketplace add willregelmann/tapestry
/plugin install tapestry@tapestry
```

There is nothing else to run. The first session after installing builds the runtime in the background, in the plugin's own data directory (`~/.claude/plugins/data/tapestry-tapestry/`), and downloads a small embedding model into `~/.tapestry/model`, checking its hash. It does the same after a plugin update that changes the code, and keeps serving from the old runtime until the new one is ready. Until the first build finishes, every prompt says memory is setting up and where its progress is logged, rather than failing silently; start a new session or run `/reload-plugins` once it's done. Your minds stay in `~/.tapestry/minds`, so removing the plugin never removes your memories.

Each session starts with a few highlights about where things stand in its project. Before every prompt the agent is shown only the few memories that clearly bear on it, from the general topic, which holds what matters everywhere, and the current project's topic, named after the repository's root folder. Hints say where more may be: further matches, and other topics whose descriptions suggest they bear on the prompt. The agent follows them with a search when it decides to, so context isn't spent on everything that might be relevant. Other topics, such as a person or a subject, stay out of sight until the agent searches or opens them; it can always see their names and descriptions.

The conversation itself is kept as the session's episode, word for word, and is never recalled. After the session ends, a model draws the lasting facts it taught, files each in the topic where it matters, links it to the memories it builds on, and lets the episode go after a while. The agent can also note a fact at once. Six tools let it search, explain, note, record the user's answers, forget and manage topics: `tapestry_recall`, `tapestry_why`, `tapestry_note`, `tapestry_record_answer`, `tapestry_forget` and `tapestry_topics`. Your memory lives in `~/.tapestry/minds/<you>.db`.

### Choosing the model

Drawing facts from conversations, and checking whether memories that sit close together contradict each other, happen in the background with a model you choose. Until you choose, nothing leaves your machine, conversations wait unread, and each session says how many are waiting. A conflict lowers belief in both memories and shows the pair together in recall, labelled `CONFLICTS with #N`; nothing is superseded on the model's say-so.

In the plugin, installing or `/plugin configure tapestry@tapestry` asks which model (`claude`, `jev` or `none`) and for a Jev key, which is kept in the platform's credential store; each choice says what is sent where. Outside the plugin:

```
tapestry judge-with claude   # Haiku, through your Claude Code login: draws facts and checks conflicts
tapestry judge-with jev      # TypeSafe's Jev, through the Vercel AI Gateway: checks conflicts only
tapestry judge-with none     # nothing is sent; only what the agent notes is remembered
```

Each command prints exactly what is sent where. Having a Jev key in place, in either way, counts as choosing Jev. If the model you chose can't run, the next session is told that background work is behind.

A mind written by an earlier version, which kept every turn as a memory, is brought forward the first time it's opened: its turns move into episodes, to be drawn from like any other, and a copy of the old file is kept beside it.

## Claude Agent SDK

Load the same plugin:

```python
ClaudeAgentOptions(plugins=[{"type": "local", "path": "/path/to/tapestry"}])
```

## Hermes Agent

Install into Hermes' environment and select the provider:

```
~/.hermes/hermes-agent/venv/bin/pip install /path/to/tapestry
# config.yaml
memory:
  provider: tapestry
```

Each Hermes profile gets its own mind in `$HERMES_HOME/tapestry/mind.db`. A Hermes session has no place, so it starts from where things stand overall, and its session end starts the same background jobs, with the model chosen in `~/.tapestry`. To bring over an existing mnemonic store: `tapestry import-mnemonic ~/.hermes/memory_store.db ~/.hermes/tapestry/mind.db`.

## Development

```
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest                    # unit tests
.venv/bin/python -m bench.run                 # retrieval benchmarks
.venv/bin/python -m bench.scenario run        # agent behaviour, judged
```

Set `TAPESTRY_PYTHON=$PWD/.venv/bin/python` to run the plugin from a checkout.
