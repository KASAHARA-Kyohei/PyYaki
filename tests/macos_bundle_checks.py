"""Read-only build-manifest verification; does not trust paths to stay inside a bundle."""

import hashlib
import os
import re
from pathlib import Path, PurePosixPath


def verify_manifest(bundle, manifest):
    root = Path(bundle).resolve(strict=True)
    if not isinstance(manifest, dict):
        raise ValueError("Expected a build manifest object")
    files = manifest.get("files")
    if manifest.get("format") != 1 or not isinstance(files, dict) or not files:
        raise ValueError("Expected a nonempty format-1 build manifest")
    errors = []
    regular = links = 0
    for relative, entry in files.items():
        if not isinstance(relative, str) or not isinstance(entry, dict):
            errors.append("Invalid manifest entry")
            continue
        parts = PurePosixPath(relative)
        if (
            parts.is_absolute()
            or ".." in parts.parts
            or not parts.parts
            or parts.as_posix() != relative
        ):
            errors.append(f"Invalid relative manifest path: {relative}")
            continue
        path = root / relative
        try:
            if not path.resolve().is_relative_to(root):
                raise ValueError("Path escapes bundle")
            if set(entry) == {"symlink"}:
                links += 1
                if not path.is_symlink() or os.readlink(path) != entry["symlink"]:
                    raise ValueError("Symlink mismatch")
            elif set(entry) == {"size", "sha256"}:
                regular += 1
                if (
                    type(entry["size"]) is not int
                    or entry["size"] < 0
                    or not isinstance(entry["sha256"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
                ):
                    raise ValueError("Invalid size or hash")
                if not path.is_file() or path.is_symlink() or path.stat().st_size != entry["size"]:
                    raise ValueError("File kind or size mismatch")
                with path.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != entry["sha256"]:
                        raise ValueError("SHA-256 mismatch")
            else:
                raise ValueError("Invalid manifest fields")
        except (OSError, RuntimeError, ValueError) as error:
            errors.append(f"{relative}: {error}")
    actual = {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    difference = sorted(actual.symmetric_difference(key for key in files if isinstance(key, str)))
    if difference:
        errors.append(f"Extra or missing files: {difference}")
    return {
        "ok": not errors,
        "regular_files": regular,
        "symlinks": links,
        "errors": errors,
        "extra_or_missing": difference,
    }
