# Tapestry

Long-term memory for AI agents: memory that keeps the right things in mind.

Whenever an agent is about to act, Tapestry answers one question: **what should I have in mind right now?** Read [`docs/INTENT.md`](docs/INTENT.md) for why, [`docs/`](docs/) for what must always hold, and [`PLAN.md`](PLAN.md) for how it's being built.

**Status:** Stages 1 and part of 2. Remembering, recall with separate match and belief labels, namespaces, supersession, forgetting and recording the user's answers work in Claude Code, the Claude Agent SDK and Hermes Agent. Background conflict checking runs from Claude Code. Verification, associations, consolidation and calibration aren't built yet; see [`PLAN.md`](PLAN.md).

## Claude Code

```
/plugin marketplace add willregelmann/tapestry
/plugin install tapestry@tapestry
```

There is nothing else to run. The first session after installing builds the runtime in the background, in the plugin's own data directory (`~/.claude/plugins/data/tapestry-tapestry/`), and downloads a small embedding model into `~/.tapestry/model`, checking its hash. It does the same after a plugin update that changes the code, and keeps serving from the old runtime until the new one is ready. Until the first build finishes, every prompt says memory is setting up and where its progress is logged, rather than failing silently; start a new session or run `/reload-plugins` once it's done. Your minds stay in `~/.tapestry/minds`, so removing the plugin never removes your memories.

Each session recalls from the default namespace, which holds what matters everywhere, plus the current project's namespace, named after the repository's root folder, and its own conversation so far. Other namespaces, such as a person or a topic, stay out of sight until the agent searches or opens them; it can always see their names and descriptions. Every exchange is remembered in the background, filed under the conversation's own namespace and the project's, never the default, so later sessions in the same project find it. Six tools let the agent search, explain, note, record the user's answers, forget and manage namespaces: `tapestry_recall`, `tapestry_why`, `tapestry_note`, `tapestry_record_answer`, `tapestry_forget` and `tapestry_namespaces`. Your memory lives in `~/.tapestry/minds/<you>.db`.

### Conflict checking

In the background, after a session ends, Tapestry can ask a model whether memories that sit close together contradict each other. A conflict lowers belief in both and shows the pair together in recall, labelled `CONFLICTS with #N`. Nothing is superseded on the model's say-so. It runs only if you choose who judges, and that is the only time memories leave your machine:

In the plugin, installing or `/plugin configure tapestry@tapestry` asks who judges (`claude`, `jev` or `none`) and for a Jev key, which is kept in the platform's credential store; each choice says where memories go. Outside the plugin:

```
tapestry judge-with claude   # Haiku, through your Claude Code login
tapestry judge-with jev      # TypeSafe's Jev, through the Vercel AI Gateway; put the key in ~/.tapestry/jev.key
tapestry judge-with none     # never
```

Each command prints exactly where memories go. Having a Jev key in place, in either way, counts as choosing Jev. If the judge you chose can't run, the next session is told that background work is behind.

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

Each Hermes profile gets its own mind in `$HERMES_HOME/tapestry/mind.db`. To bring over an existing mnemonic store: `tapestry import-mnemonic ~/.hermes/memory_store.db ~/.hermes/tapestry/mind.db`.

## Development

```
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest                    # unit tests
.venv/bin/python -m bench.run                 # retrieval benchmarks
.venv/bin/python -m bench.scenario run        # agent behaviour, judged
```

Set `TAPESTRY_PYTHON=$PWD/.venv/bin/python` to run the plugin from a checkout.
