"""Maintainer-only local dependency inventory; does not approve licenses or redistribution."""

import argparse
import hashlib
import json
import re
import zipfile
from datetime import UTC, datetime
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import quote


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def license_name(name):
    return any(
        word in Path(name).name.lower() for word in ("license", "copying", "notice", "copyright")
    )


class Inventory:
    def __init__(self, output):
        self.output = output
        self.components = {}
        self.dependencies = {}
        self.notices = []
        self.missing = []

    def notice(self, ref, name, data):
        # Never extract paths supplied by a wheel/manifest into the output tree.
        digest = hashlib.sha256(data).hexdigest()
        path = Path("licenses") / f"{digest}.txt"
        target = self.output / path
        if not target.exists():
            target.write_bytes(data)
        self.notices.append(
            {"component": ref, "source_name": name, "file": path.as_posix(), "sha256": digest}
        )

    def add(self, kind, name, version, ecosystem=None, declared=None, digest=None, **properties):
        ref = (
            f"pkg:{ecosystem}/{quote(name, safe='')}@{quote(version, safe='')}"
            if ecosystem
            else f"asset:{name}@{version}"
        )
        if ref in self.components:
            raise ValueError(f"duplicate component: {ref}")
        component = {"type": kind, "bom-ref": ref, "name": name, "version": version}
        if ecosystem:
            component["purl"] = ref
        if digest:
            component["hashes"] = [{"alg": "SHA-256", "content": digest}]
        if declared:
            component["licenses"] = [{"license": {"name": declared}}]
        component["properties"] = [
            {"name": f"pyyaki:{k}", "value": str(v)} for k, v in properties.items()
        ]
        self.components[ref] = component
        self.dependencies[ref] = set()
        return ref

    def wheel(self, path):
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            metadata = [name for name in names if name.endswith(".dist-info/METADATA")]
            if len(metadata) != 1 or len(names) != len(set(names)):
                raise ValueError("wheel requires one metadata file and no duplicate entries")
            meta = BytesParser().parsebytes(archive.read(metadata[0]))
            name, version = meta["Name"], meta["Version"]
            if not name or not version:
                raise ValueError("wheel metadata name/version missing")
            declared = meta.get("License-Expression") or meta.get("License")
            if declared and len(declared) > 200:
                declared = None  # Full text belongs in the byte-preserved evidence below.
            ref = self.add(
                "library",
                name,
                version,
                "pypi",
                declared,
                sha256(path),
                artifact=path.name,
                classifiers="\n".join(meta.get_all("Classifier", [])),
            )
            found = False
            base = metadata[0].split("/")[0] + "/"
            for name in names:
                if name.startswith(base) and license_name(name) and not name.endswith("/"):
                    if archive.getinfo(name).file_size > 8 * 1024 * 1024:
                        raise ValueError("license text exceeds 8 MiB")
                    self.notice(ref, name, archive.read(name))
                    found = True
            if not found:
                self.missing.append({"component": ref, "reason": "no bundled license text found"})
            return ref, meta["Name"], version

    def cargo(self, metadata):
        nodes = {node["id"]: node for node in metadata["resolve"]["nodes"]}
        packages = {package["id"]: package for package in metadata["packages"]}
        root = metadata["resolve"]["root"]
        reached, pending = set(), [root]
        while pending:
            item = pending.pop()
            if item in reached:
                continue
            reached.add(item)
            pending.extend(dep["pkg"] for dep in nodes[item]["deps"])
        refs = {}
        for ident in sorted(reached):
            package = packages[ident]
            ref = self.add(
                "library",
                package["name"],
                package["version"],
                "cargo",
                package.get("license"),
                role="source/build graph; not binary-link proof",
            )
            refs[ident] = ref
            directory = Path(package["manifest_path"]).parent.resolve(strict=True)
            candidates = [p for p in directory.iterdir() if p.is_file() and license_name(p.name)]
            if package.get("license_file"):
                explicit = (directory / package["license_file"]).resolve(strict=True)
                if not explicit.is_relative_to(directory):
                    raise ValueError("Cargo license file escapes its package")
                candidates.append(explicit)
            for path in sorted(set(candidates)):
                if path.is_symlink() or path.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError("license must be a bounded regular file")
                self.notice(ref, path.name, path.read_bytes())
            if not candidates:
                self.missing.append({"component": ref, "reason": "no top-level license text found"})
        for ident, ref in refs.items():
            self.dependencies[ref].update(refs[dep["pkg"]] for dep in nodes[ident]["deps"])
        return refs[root]

    def asset(self, name, version, directory):
        directory = directory.resolve(strict=True)
        ref = self.add("framework", name, version, status="redistribution review pending")
        files = []
        for path in sorted(directory.rglob("*")):
            if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
                raise ValueError("runtime reparse points are unsupported")
            if path.is_file():
                relative = path.relative_to(directory).as_posix()
                files.append(
                    {"path": relative, "size": path.stat().st_size, "sha256": sha256(path)}
                )
                if license_name(relative) and path.suffix.lower() not in (".exe", ".dll", ".bat"):
                    self.notice(ref, relative, path.read_bytes())
        if not files:
            raise ValueError("runtime asset directory is empty")
        (self.output / f"{name}-files.json").write_text(
            json.dumps(files, indent=2), encoding="utf-8"
        )
        self.missing.append(
            {
                "component": ref,
                "reason": "embedded third-party, CRT and redistribution review required",
            }
        )
        return ref


def build(
    wheelhouse,
    framework_wheel,
    requirements,
    cargo_metadata,
    output,
    source_commit,
    *,
    runtime=None,
    runtime_version=None,
    webview2=None,
    webview2_version=None,
):
    if output.exists() or output.is_symlink():
        raise ValueError("output already exists; preserve the previous inventory")
    if not re.fullmatch(r"[a-f0-9]{40}", source_commit):
        raise ValueError("source commit must be a full SHA-1")
    if any(
        (directory is None) != (version is None)
        for directory, version in ((runtime, runtime_version), (webview2, webview2_version))
    ):
        raise ValueError("runtime directories and explicit versions must be supplied together")
    expected = {}
    for line in requirements.read_text(encoding="utf-8").replace("\\\n", " ").splitlines():
        line = line.partition("#")[0].strip()
        if not line:
            continue
        match = re.fullmatch(r"([\w.-]+)==([\w.!+-]+)((?:\s+--hash=sha256:[a-fA-F0-9]{64})+)", line)
        if not match:
            raise ValueError("inventory requires a name==version SHA-256 requirements lock")
        key = (re.sub(r"[-_.]+", "-", match[1]).lower(), match[2])
        if key in expected:
            raise ValueError("duplicate requirements entry")
        expected[key] = {
            value.lower() for value in re.findall(r"sha256:([a-fA-F0-9]{64})", match[3])
        }
    if not expected:
        raise ValueError("requirements lock is empty")
    output.mkdir(parents=True)
    (output / "licenses").mkdir()
    inventory = Inventory(output)
    root, _, _ = inventory.wheel(framework_wheel)
    found = set()
    for path in sorted(wheelhouse.iterdir()):
        if path.is_symlink() or not path.is_file() or path.suffix != ".whl":
            raise ValueError("wheelhouse must contain only regular wheel files")
        ref, name, version = inventory.wheel(path)
        key = (re.sub(r"[-_.]+", "-", name).lower(), version)
        if key not in expected or sha256(path).lower() not in expected[key]:
            raise ValueError(f"wheel not matched by the locked hash: {path.name}")
        found.add(key)
        inventory.dependencies[root].add(ref)
    if found != set(expected):
        raise ValueError("wheelhouse does not cover the complete requirements lock")
    cargo = json.loads(cargo_metadata.read_text(encoding="utf-8"))
    inventory.dependencies[root].add(inventory.cargo(cargo))
    for name, directory, version in (
        ("cpython", runtime, runtime_version),
        ("webview2", webview2, webview2_version),
    ):
        if directory:
            inventory.dependencies[root].add(inventory.asset(name, version, directory))
    components = list(inventory.components.values())
    bom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(UTC).isoformat(),
            "component": inventory.components[root],
            "properties": [
                {"name": "pyyaki:source-commit", "value": source_commit},
                {
                    "name": "pyyaki:status",
                    "value": "draft; license/redistribution approval pending",
                },
            ],
        },
        "components": [c for c in components if c["bom-ref"] != root],
        "dependencies": [
            {"ref": ref, "dependsOn": sorted(deps)}
            for ref, deps in sorted(inventory.dependencies.items())
        ],
    }
    (output / "bom.cdx.json").write_text(json.dumps(bom, indent=2), encoding="utf-8")
    report = {
        "status": "review_required",
        "source_commit": source_commit,
        "components": len(components),
        "notice_files": inventory.notices,
        "unresolved": inventory.missing,
        "scope": (
            "explicit locked wheels, selected Cargo source graph and explicit runtime files; "
            "not an SCA audit"
        ),
    }
    (output / "inventory.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = [
        "# Third-party notice review draft",
        "",
        "Project licensing, binary redistribution and publication are separate pending decisions.",
        "",
        "Declared metadata is preserved without selecting a license alternative "
        "or claiming compatibility.",
        "",
    ]
    for item in inventory.notices:
        lines.append(
            f"- {item['component']}: {item['source_name']} — [{item['sha256']}]({item['file']})"
        )
    lines.extend(
        [
            "",
            "Unresolved items:",
            "",
            *[f"- {item['component']}: {item['reason']}" for item in inventory.missing],
        ]
    )
    (output / "THIRD-PARTY-NOTICES.draft.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("wheelhouse", "framework-wheel", "requirements", "cargo-metadata", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    for name in ("runtime", "webview2"):
        parser.add_argument(f"--{name}", type=Path)
        parser.add_argument(f"--{name}-version")
    args = parser.parse_args()
    try:
        report = build(
            args.wheelhouse,
            args.framework_wheel,
            args.requirements,
            args.cargo_metadata,
            args.output,
            args.source_commit,
            runtime=args.runtime,
            runtime_version=args.runtime_version,
            webview2=args.webview2,
            webview2_version=args.webview2_version,
        )
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        parser.exit(1, f"pyyaki: cannot build release inventory: {error}\n")
    print(json.dumps({key: report[key] for key in ("status", "components", "source_commit")}))


if __name__ == "__main__":
    main()
