import copy
import hashlib
import json
from types import SimpleNamespace

import pytest
from macos_bundle_checks import verify_manifest
from macos_regression import completed, ready_control


def artifact(tmp_path):
    bundle = tmp_path / "Example.app"
    bundle.mkdir()
    data = bundle / "payload"
    data.write_bytes(b"payload")
    manifest = {
        "format": 1,
        "files": {"payload": {"size": 7, "sha256": hashlib.sha256(b"payload").hexdigest()}},
    }
    return bundle, manifest


def test_manifest_matches_all_files_and_detects_same_size_corruption(tmp_path):
    bundle, manifest = artifact(tmp_path)
    assert verify_manifest(bundle, manifest)["ok"]
    (bundle / "payload").write_bytes(b"changed")
    result = verify_manifest(bundle, manifest)
    assert not result["ok"] and "SHA-256" in str(result["errors"])


@pytest.mark.parametrize("fault", ["extra", "missing", "parent", "absolute", "empty", "fields"])
def test_manifest_cannot_pass_incomplete_or_escaping_inventory(tmp_path, fault):
    bundle, manifest = artifact(tmp_path)
    if fault == "extra":
        (bundle / "extra").write_bytes(b"extra")
    elif fault == "missing":
        (bundle / "payload").unlink()
    elif fault == "parent":
        manifest["files"]["../outside"] = manifest["files"]["payload"]
    elif fault == "absolute":
        manifest["files"][str(tmp_path / "outside")] = {}
    elif fault == "fields":
        manifest["files"]["payload"]["unexpected"] = True
    else:
        manifest["files"] = {}
    if fault == "empty":
        with pytest.raises(ValueError):
            verify_manifest(bundle, manifest)
    else:
        assert not verify_manifest(bundle, manifest)["ok"]


def test_external_symlink_is_rejected_without_reading_target(tmp_path):
    bundle, manifest = artifact(tmp_path)
    target = tmp_path / "outside"
    target.write_bytes(b"private")
    link = bundle / "link"
    try:
        link.symlink_to(target)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("symlink requires Windows privilege")
        raise
    manifest["files"]["link"] = {"symlink": str(target)}
    assert not verify_manifest(bundle, manifest)["ok"]


@pytest.mark.parametrize("fault", ["zero", "missing", "failure", "command"])
def test_completion_requires_every_expected_case_and_command(fault):
    report = {
        "planned_cases": ["a", "b"],
        "cases": [{"name": "a", "ok": True}, {"name": "b", "ok": True}],
        "commands": [{"exit_code": 0}],
    }
    assert completed(report, 2)
    if fault == "zero":
        assert not completed({"planned_cases": [], "cases": [], "commands": []}, 0)
    elif fault == "missing":
        report["cases"].pop()
        assert not completed(report, 2)
    elif fault == "failure":
        report["cases"][0]["ok"] = False
        assert not completed(report, 2)
    else:
        report["commands"][0]["exit_code"] = 1
        assert not completed(report, 2)


def control_state():
    return {
        "pid": 123456,
        "stable_ms": 2000,
        "state": {
            "fullscreen": True,
            "active_space": True,
            "unoccluded": True,
            "application_active": True,
        },
    }


@pytest.mark.parametrize("fault", ["pid", "duration", "nan", "fullscreen", "space", "occlusion"])
def test_control_must_prove_its_own_stable_fullscreen_state(tmp_path, fault):
    data = copy.deepcopy(control_state())
    if fault == "pid":
        data["pid"] += 1
    elif fault == "duration":
        data["stable_ms"] = 1999
    elif fault == "nan":
        data["stable_ms"] = float("nan")
    else:
        data["state"][
            {"fullscreen": "fullscreen", "space": "active_space", "occlusion": "unoccluded"}[fault]
        ] = False
    (tmp_path / "ready.json").write_text(json.dumps(data))
    process = SimpleNamespace(pid=123456, poll=lambda: None)
    with pytest.raises(ValueError):
        ready_control(process, tmp_path)


def test_control_cannot_pass_readiness_after_exit(tmp_path):
    (tmp_path / "ready.json").write_text(json.dumps(control_state()))
    process = SimpleNamespace(pid=123456, poll=lambda: 0)
    with pytest.raises(RuntimeError):
        ready_control(process, tmp_path)


def test_duplicate_successes_cannot_replace_a_missing_case():
    report = {
        "planned_cases": ["a", "b"],
        "cases": [{"name": "a", "ok": True}, {"name": "a", "ok": True}],
        "commands": [],
    }
    assert not completed(report, 2)
