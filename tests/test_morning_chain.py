"""Executes the REAL scripts/morning_chain.sh against stubbed steps.

R23's bug — a failing step silencing the briefing — was a property of the
shell chain, not of any Python module, so the only test that can catch its
return is one that actually runs the shell. This copies the real script into a
tmp repo skeleton (so its `cd "$(dirname $0)/.."` lands on the skeleton root),
shadows `uv` with a shim, and drives step exit codes through env vars.

Hermetic: no Postgres, no network, no real ingest, no real `uv`.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CHAIN = REPO_ROOT / "scripts" / "morning_chain.sh"

# Every step the chain must run, in order, keyed by the name the stubs echo.
EXPECTED_STEPS = [
    "backup_db",
    "ingest_sleeper",
    "ingest_nflverse",
    "ingest_fantasypros",
    "score_sleeper_projections",
    "build_valuation",
    "ingest_fp_news",
]
BRIEFING = "morning_briefing"

# `uv run python scripts/foo.py --flags` -> echo STEP:foo, exit $FFI_TEST_RC_foo
UV_SHIM = """#!/usr/bin/env bash
# Test shim standing in for `uv`. Drops the leading `run python` and reports
# the target script by basename so the test can assert which steps executed.
[ "$1" = "run" ] && shift
[ "$1" = "python" ] && shift
name="$(basename "$1" .py)"
echo "STEP:$name"
var="FFI_TEST_RC_${name}"
exit "${!var:-0}"
"""

BACKUP_STUB = """#!/usr/bin/env bash
echo "STEP:backup_db"
exit "${FFI_TEST_RC_backup_db:-0}"
"""


@pytest.fixture
def chain_env(tmp_path):
    """A tmp repo skeleton holding the real chain script plus stubbed steps."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy2(CHAIN, scripts / "morning_chain.sh")

    (scripts / "backup_db.sh").write_text(BACKUP_STUB)
    # The uv shim never opens these, but their absence would make a regression
    # that bypasses the shim (e.g. a bare `python`) fail for the wrong reason.
    for name in EXPECTED_STEPS[1:] + [BRIEFING]:
        (scripts / f"{name}.py").write_text("raise SystemExit('stub must not run')\n")

    bindir = tmp_path / "bin"
    bindir.mkdir()
    uv = bindir / "uv"
    uv.write_text(UV_SHIM)
    uv.chmod(0o755)

    return tmp_path, bindir


def _run(chain_env, **rcs):
    tmp_path, bindir = chain_env
    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env['PATH']}"
    for name, rc in rcs.items():
        env[f"FFI_TEST_RC_{name}"] = str(rc)
    return subprocess.run(
        ["bash", str(tmp_path / "scripts" / "morning_chain.sh")],
        capture_output=True,
        text=True,
        env=env,
        cwd="/",  # the script must cd itself; a helpful cwd would hide a bug
    )


def test_all_steps_succeed_exits_zero(chain_env):
    proc = _run(chain_env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for name in EXPECTED_STEPS + [BRIEFING]:
        assert f"STEP:{name}" in proc.stdout


def test_failed_first_step_still_runs_everything_but_exits_nonzero(chain_env):
    """R23 + ADR Domain 5: keep going, render the briefing, still report red."""
    proc = _run(chain_env, backup_db=1)
    out = proc.stdout

    for name in EXPECTED_STEPS[1:]:
        assert f"STEP:{name}" in out, f"step {name} was skipped after a failure"
    assert f"STEP:{BRIEFING}" in out, "briefing was silenced by an upstream failure"
    assert "=== rc=1 :: bash scripts/backup_db.sh" in out
    assert proc.returncode != 0, "launchd would have seen a green run"


def test_midchain_failure_exits_nonzero(chain_env):
    proc = _run(chain_env, score_sleeper_projections=3)
    assert proc.returncode != 0
    assert f"STEP:{BRIEFING}" in proc.stdout
    assert "STEP:ingest_fp_news" in proc.stdout


def test_briefing_rc_takes_precedence(chain_env):
    proc = _run(chain_env, morning_briefing=2)
    assert proc.returncode == 2, proc.stdout + proc.stderr


def test_briefing_rc_wins_over_step_failure(chain_env):
    """A broken dashboard is the more urgent signal than a broken step."""
    proc = _run(chain_env, backup_db=1, morning_briefing=2)
    assert proc.returncode == 2


def test_briefing_renders_exactly_once(chain_env):
    """The EXIT trap must not double-render on the normal path."""
    for rcs in ({}, {"backup_db": 1}, {"morning_briefing": 2}):
        proc = _run(chain_env, **rcs)
        assert proc.stdout.count(f"STEP:{BRIEFING}") == 1, (rcs, proc.stdout)
