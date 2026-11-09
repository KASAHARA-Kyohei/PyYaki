"""Check source version stamps without importing the app or compiling native code."""

import argparse
import ast
import json
import re
import tomllib
from pathlib import Path


def check(root):
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    tree = ast.parse((root / "src/pyyaki/__init__.py").read_text(encoding="utf-8"))
    python = [
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        )
    ]
    sdk = re.findall(
        r'^const VERSION = "([^"]+)";',
        (root / "src/pyyaki/static/sdk.js").read_text(encoding="utf-8"),
        re.M,
    )
    native = re.findall(
        r'pub const VERSION: &str = "([^"]+)";',
        (root / "native/src/lib.rs").read_text(encoding="utf-8"),
    )
    cargo = tomllib.loads((root / "native/Cargo.toml").read_text(encoding="utf-8"))["package"][
        "version"
    ]
    expected_cargo = project.replace(".dev", "-dev.")
    if python != [project] or sdk != [project] or native != [project] or cargo != expected_cargo:
        raise ValueError(
            f"version mismatch: project={project}, Python={python}, SDK={sdk}, "
            f"native={native}, Cargo={cargo}"
        )
    return {"version": project, "cargo_version": cargo, "status": "matched"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        result = check(args.root)
    except (OSError, ValueError, KeyError, SyntaxError) as error:
        parser.exit(1, f"pyyaki: {error}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
