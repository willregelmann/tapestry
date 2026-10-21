"""The plugin launcher builds its runtime in the plugin's data directory by itself
(docs/interfaces/HOOKS.md: a host missing a hook gets a visible fallback, never silent memory loss)."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "bin" / "tapestry"


@pytest.fixture
def plugin(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    installer = tmp_path / "installer.sh"
    installer.write_text('#!/bin/sh\necho "$@" >> "$CLAUDE_PLUGIN_DATA/installer-ran"\n')
    installer.chmod(0o755)
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(ROOT), "CLAUDE_PLUGIN_DATA": str(data),
           "TAPESTRY_HOME": str(tmp_path / "home"), "TAPESTRY_INSTALLER": str(installer)}
    env.pop("TAPESTRY_PYTHON", None)

    class P:
        pass
    p = P()
    p.data, p.env = data, env
    p.ran = data / "installer-ran"

    def run(*args, event=None, extra_env=None, **kw):
        stdin = json.dumps({"hook_event_name": event}) if event else ""
        return subprocess.run([str(LAUNCHER), *args], input=stdin, capture_output=True, text=True,
                              env={**env, **(extra_env or {})}, timeout=30, **kw)
    p.run = run

    def stub_python(name):
        py = data / name / "bin" / "python"
        py.parent.mkdir(parents=True)
        py.write_text('#!/bin/sh\necho "runtime: $@"\ncat\n')
        py.chmod(0o755)
        return py

    def runtime(built="current"):
        built = run("source-hash").stdout.strip() if built == "current" else built
        stub_python(f"runtime-{built}")
        (data / "built-from").write_text(built)
    p.runtime, p.stub_python = runtime, stub_python

    def started(wait=5.0):
        end = time.time() + wait
        while time.time() < end:
            if p.ran.exists():
                return p.ran.read_text().count("install")
            time.sleep(0.05)
        return 0
    p.started = started
    return p


def context(out):
    return json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]


def test_a_first_session_starts_building_the_runtime_and_says_memory_is_setting_up(plugin):
    out = plugin.run("hook", event="SessionStart")
    assert out.returncode == 0 and "setting itself up" in context(out) and "No memories were searched" in context(out)
    assert plugin.started() == 1


def test_a_prompt_with_no_runtime_also_starts_the_build_in_case_the_plugin_was_enabled_mid_session(plugin):
    out = plugin.run("hook", event="UserPromptSubmit")
    assert "setting itself up" in context(out) and plugin.started() == 1


def test_only_one_build_runs_at_a_time(plugin):
    (plugin.data / "install.lock").mkdir()
    plugin.run("hook", event="SessionStart")
    time.sleep(0.5)
    assert not plugin.ran.exists()


def test_a_build_that_died_does_not_block_the_next_one_forever(plugin):
    lock = plugin.data / "install.lock"
    lock.mkdir()
    old = time.time() - 3600
    os.utime(lock, (old, old))
    plugin.run("hook", event="SessionStart")
    assert plugin.started() == 1


def test_a_current_runtime_runs_the_command_and_passes_stdin_through(plugin):
    plugin.runtime()
    out = plugin.run("hook", event="UserPromptSubmit")
    assert out.stdout.count("runtime:") == 1 and "UserPromptSubmit" in out.stdout   # once, with the hook's input
    time.sleep(0.3)
    assert not plugin.ran.exists()


def test_after_a_plugin_update_the_old_runtime_keeps_serving_while_the_new_one_is_built(plugin):
    plugin.runtime(built="an-older-build")
    out = plugin.run("hook", event="SessionStart")
    assert out.stdout.count("runtime:") == 1
    assert plugin.started() == 1


def test_a_change_to_the_source_is_a_change_to_what_the_runtime_is_built_from(tmp_path, plugin):
    root = tmp_path / "root"
    (root / "src").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='t'\n")
    (root / "src" / "a.py").write_text("x = 1\n")
    env = {"extra_env": {"CLAUDE_PLUGIN_ROOT": str(root)}}
    before = plugin.run("source-hash", **env).stdout
    (root / "src" / "a.py").write_text("x = 2\n")
    assert plugin.run("source-hash", **env).stdout != before


def test_other_hooks_stay_silent_while_there_is_no_runtime(plugin):
    out = plugin.run("hook", event="Stop")
    assert out.returncode == 0 and out.stdout == ""
    time.sleep(0.3)
    assert not plugin.ran.exists()


def test_a_development_checkouts_interpreter_skips_the_build(plugin, tmp_path):
    py = tmp_path / "dev-python"
    py.write_text('#!/bin/sh\necho "dev: $@"\n')
    py.chmod(0o755)
    out = plugin.run("hook", event="SessionStart", extra_env={"TAPESTRY_PYTHON": str(py)})
    assert out.stdout.count("dev:") == 1
    time.sleep(0.3)
    assert not plugin.ran.exists()


def test_the_mcp_server_without_a_runtime_says_why_it_cannot_start(plugin):
    out = plugin.run("mcp")
    assert out.returncode == 1 and "setting itself up" in out.stderr


def test_an_install_that_fails_leaves_no_marker_and_frees_the_lock(plugin, tmp_path):
    broken = tmp_path / "broken-root"          # has source to fingerprint, but can't be installed
    (broken / "src").mkdir(parents=True)
    (broken / "pyproject.toml").write_text("[project]\nname = 'broken'\n")
    out = plugin.run("install", extra_env={"CLAUDE_PLUGIN_ROOT": str(broken)})
    assert out.returncode != 0
    assert not (plugin.data / "built-from").exists() and not (plugin.data / "install.lock").exists()


def test_an_install_does_not_start_beside_another(plugin):
    (plugin.data / "install.lock").mkdir()
    out = plugin.run("install")
    assert out.returncode == 0 and "already running" in out.stderr and not (plugin.data / "runtime-x").exists()


def test_a_runtime_that_is_only_partly_built_is_never_run(plugin):
    # The interpreter exists as soon as the build starts; the package isn't in it until the end.
    plugin.stub_python("runtime-halfway")
    out = plugin.run("hook", event="UserPromptSubmit")
    assert "setting itself up" in context(out) and "runtime:" not in out.stdout


def test_a_failed_rebuild_leaves_the_serving_runtime_alone(plugin, tmp_path):
    plugin.runtime(built="the-serving-build")
    broken = tmp_path / "broken-root"
    (broken / "src").mkdir(parents=True)
    (broken / "pyproject.toml").write_text("[project]\nname = 'broken'\n")
    out = plugin.run("install", extra_env={"CLAUDE_PLUGIN_ROOT": str(broken)})
    assert out.returncode != 0
    assert (plugin.data / "built-from").read_text() == "the-serving-build"
    assert (plugin.data / "runtime-the-serving-build" / "bin" / "python").exists()
    assert plugin.run("hook", event="Stop").stdout.count("runtime:") == 1
