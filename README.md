# Tapestry

Long-term memory for AI agents: memory that knows it can be wrong.

Before an agent relies on something it remembers, Tapestry answers one question: **how much should I trust this right now?** Read [`invariants/INTENT.md`](invariants/INTENT.md) for why, [`invariants/`](invariants/) for what must always hold, and [`PLAN.md`](PLAN.md) for how it's being built.

**Status:** Stage 1. Remembering, recall with honest match labels, per-project scopes and supersession work in Claude Code, the Claude Agent SDK and Hermes Agent. Belief tracking (volatility, evidence, reconfirmation) is Stage 2.

## Claude Code

```
/plugin marketplace add willregelmann/tapestry
/plugin install tapestry@tapestry
```

Then install the runtime once with the plugin's `bin/tapestry install`. It creates `~/.tapestry/venv` and downloads a small embedding model, checking its hash. Until it's done, every prompt says memory is unavailable and gives the exact command to run, rather than failing silently.

Each session recalls from your user-wide memory plus the current project's scope, named after the repository's root folder. Every exchange is remembered in the background. Four tools let the agent search, explain, note and load scopes: `tapestry_recall`, `tapestry_why`, `tapestry_note` and `tapestry_scopes`. Your memory lives in `~/.tapestry/minds/<you>.db`.

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
