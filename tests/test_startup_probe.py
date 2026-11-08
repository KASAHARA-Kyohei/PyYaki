import json
import subprocess
import sys

import pytest
import startup_probe
from startup_checks import PHASES, validate_startup


def trace():
    rows = [
        {"phase": "ui_deadline_started", "data": {"timeout_ms": 10000}},
        {
            "phase": "macos_initial_space_behavior",
            "data": {"action": "applied", "original_bits": 128, "applied_bits": 130},
        },
    ]
    rows += [
        {"phase": "sdk_shell_progress", "data": {"stage": stage}}
        for stage in ["scheduled", "frame1", "frame2"]
    ]
    for phase in PHASES:
        rows.append({"phase": phase, "data": None})
        if phase == "shell_marker":
            rows += [
                {
                    "phase": "macos_initial_space_behavior",
                    "data": {"action": "restored", "reason": "shell", "restored_bits": 128},
                },
                {"phase": "backend_deadline_started", "data": {"timeout_ms": 10000}},
            ]
    return rows


def test_complete_order_and_restore_evidence():
    assert validate_startup(trace(), require_space_restore=True) == []


@pytest.mark.parametrize(
    "phase", ["shell_marker", "python_spawned", "startup_hooks_completed", "sdk_welcome_posted"]
)
def test_missing_or_repeated_phase_cannot_pass(phase):
    rows = trace()
    selected = next(row for row in rows if row["phase"] == phase)
    rows.remove(selected)
    assert validate_startup(rows)
    rows += [selected, selected]
    assert validate_startup(rows)


@pytest.mark.parametrize(
    "fault",
    [
        "early-python",
        "early-shell",
        "wrong-deadline",
        "missing-restore",
        "late-restore",
        "wrong-restore",
        "all-spaces",
        "backend-failed",
    ],
)
def test_false_positive_startup_evidence_is_rejected(fault):
    rows = trace()

    def find(phase):
        return next(row for row in rows if row["phase"] == phase)

    if fault == "early-python":
        row = find("python_spawn_started")
        rows.remove(row)
        rows.insert(0, row)
    elif fault == "early-shell":
        row = find("shell_marker")
        rows.remove(row)
        rows.insert(0, row)
    elif fault == "wrong-deadline":
        find("backend_deadline_started")["data"]["timeout_ms"] = 15000
    elif fault in {"missing-restore", "late-restore", "wrong-restore"}:
        row = next(
            row
            for row in rows
            if row["phase"] == "macos_initial_space_behavior"
            and row["data"]["action"] == "restored"
        )
        if fault == "wrong-restore":
            row["data"]["restored_bits"] = 0
        else:
            rows.remove(row)
            if fault == "late-restore":
                rows.append(row)
    elif fault == "all-spaces":
        find("macos_initial_space_behavior")["data"]["applied_bits"] |= 1
    else:
        rows.append({"phase": "backend_failed", "data": None})
    assert validate_startup(rows, require_space_restore=True)


def test_legacy_probe_can_check_order_without_mac_placement():
    rows = [row for row in trace() if row["phase"] != "macos_initial_space_behavior"]
    assert validate_startup(rows) == []
    assert validate_startup(rows, require_space_restore=True)


def test_interrupt_reaps_only_the_owned_child(monkeypatch):
    child = subprocess.Popen([sys.executable, "-I", "-c", "import time;time.sleep(60)"])
    wait = child.wait
    first = True

    def interrupted_wait(*args, **kwargs):
        nonlocal first
        if first:
            first = False
            raise KeyboardInterrupt
        return wait(*args, **kwargs)

    monkeypatch.setattr(child, "wait", interrupted_wait)
    try:
        with pytest.raises(KeyboardInterrupt):
            startup_probe.wait_owned(child, 1)
        assert child.returncode is not None
    finally:
        if child.poll() is None:
            child.kill()
            wait(timeout=5)


def test_interrupted_observation_preserves_owned_logs(tmp_path, monkeypatch):
    base = tmp_path / "logs"
    base.mkdir()
    folder = tmp_path / "case"
    folder.mkdir()

    class Owned:
        pid = 123456
        returncode = -15

        def __init__(self, *args, **kwargs):
            instance = base / "own"
            instance.mkdir()
            (instance / "host.log").write_text(
                "pyyaki_startup="
                + json.dumps({"phase": "host_started", "data": {"pid": self.pid}})
                + "\n"
            )
            (instance / "worker.log").write_text("原ログ 😀", encoding="utf-8")

    monkeypatch.setattr(startup_probe.subprocess, "Popen", Owned)

    def interrupt(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(startup_probe, "wait_owned", interrupt)
    with pytest.raises(KeyboardInterrupt):
        startup_probe.observe(["owned"], folder, base, 1)
    assert (folder / "worker.log").read_text(encoding="utf-8") == "原ログ 😀"
    assert json.loads((folder / "process.json").read_text())["interrupted"] is True
