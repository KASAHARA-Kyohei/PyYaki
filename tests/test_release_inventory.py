import hashlib
import json
import zipfile

import pytest

from pyyaki.release_inventory import build


def wheel(path, name, version, text=b"Original copyright\r\nPermission text\n"):
    with zipfile.ZipFile(path, "w") as archive:
        base = f"{name}-{version}.dist-info"
        archive.writestr(f"{base}/METADATA", f"Name: {name}\nVersion: {version}\nLicense: MIT\n")
        archive.writestr(f"{base}/licenses/LICENSE", text)
    return path


@pytest.fixture
def inputs(tmp_path):
    framework = wheel(tmp_path / "framework.whl", "pyyaki", "0.1.0.dev0")
    wheelhouse = tmp_path / "wheels"
    wheelhouse.mkdir()
    dependency = wheel(wheelhouse / "dependency.whl", "dependency", "1.2")
    requirements = tmp_path / "requirements.lock"
    digest = hashlib.sha256(dependency.read_bytes()).hexdigest()
    requirements.write_text(f"dependency==1.2 --hash=sha256:{digest}\n")
    crate = tmp_path / "crate"
    crate.mkdir()
    (crate / "LICENSE").write_bytes(b"Crate original license")
    metadata = {
        "resolve": {"root": "root", "nodes": [{"id": "root", "deps": []}]},
        "packages": [
            {
                "id": "root",
                "name": "pyyaki-host",
                "version": "0.1.0-dev.0",
                "manifest_path": str(crate / "Cargo.toml"),
                "license": None,
            }
        ],
    }
    cargo = tmp_path / "cargo.json"
    cargo.write_text(json.dumps(metadata), encoding="utf-8")
    return wheelhouse, framework, requirements, cargo, tmp_path / "output", "a" * 40


def test_inventory_graph_and_original_notice_bytes_without_approval(inputs):
    report = build(*inputs)
    output = inputs[4]
    assert report["status"] == "review_required"
    assert report["components"] == 3
    bom = json.loads((output / "bom.cdx.json").read_text(encoding="utf-8"))
    root = bom["metadata"]["component"]["bom-ref"]
    edges = next(edge["dependsOn"] for edge in bom["dependencies"] if edge["ref"] == root)
    assert len(edges) == 2
    data = b"Original copyright\r\nPermission text\n"
    digest = hashlib.sha256(data).hexdigest()
    assert (output / "licenses" / f"{digest}.txt").read_bytes() == data
    assert not (output / "LICENSE").exists()
    with pytest.raises(ValueError, match="already exists"):
        build(*inputs)


def test_wrong_hash_and_missing_locked_dependency_fail(inputs):
    inputs[2].write_text("dependency==1.2 --hash=sha256:" + "0" * 64)
    with pytest.raises(ValueError, match="locked hash"):
        build(*inputs)


def test_uppercase_hex_lock_digest_is_matched(inputs):
    text = inputs[2].read_text()
    name, digest = text.split("--hash=sha256:")
    inputs[2].write_text(name + "--hash=sha256:" + digest.upper())
    assert build(*inputs)["components"] == 3


def test_missing_dependency_does_not_become_a_complete_inventory(inputs):
    inputs[2].write_text(inputs[2].read_text() + "absent==1 --hash=sha256:" + "b" * 64)
    with pytest.raises(ValueError, match="complete requirements"):
        build(*inputs)
    assert not (inputs[4] / "inventory.json").exists()


def test_unreachable_cargo_package_not_included(inputs):
    cargo = json.loads(inputs[3].read_text(encoding="utf-8"))
    cargo["packages"].append({"id": "unused", "name": "unused", "manifest_path": "absent"})
    inputs[3].write_text(json.dumps(cargo), encoding="utf-8")
    assert build(*inputs)["components"] == 3


def test_cargo_notice_cannot_escape_package(inputs):
    cargo = json.loads(inputs[3].read_text(encoding="utf-8"))
    outside = inputs[3].parent / "outside.txt"
    outside.write_bytes(b"not a package notice")
    cargo["packages"][0]["license_file"] = "../outside.txt"
    inputs[3].write_text(json.dumps(cargo), encoding="utf-8")
    with pytest.raises(ValueError, match="escapes"):
        build(*inputs)
