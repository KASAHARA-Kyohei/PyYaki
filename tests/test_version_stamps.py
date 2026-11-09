import shutil
from pathlib import Path

import pytest

from tools.check_versions import check

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def version_tree(tmp_path):
    for name in (
        "pyproject.toml",
        "src/pyyaki/__init__.py",
        "src/pyyaki/static/sdk.js",
        "native/src/lib.rs",
        "native/Cargo.toml",
    ):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    return tmp_path


def test_version_check_does_not_import_python_code(version_tree):
    with (version_tree / "src/pyyaki/__init__.py").open("a", encoding="utf-8") as stream:
        stream.write('\nraise RuntimeError("must not import this fixture")\n')
    assert check(version_tree)["status"] == "matched"


@pytest.mark.parametrize("name", ["src/pyyaki/static/sdk.js", "native/src/lib.rs"])
def test_mixed_sdk_or_host_stamp_rejected(version_tree, name):
    path = version_tree / name
    path.write_text(
        path.read_text(encoding="utf-8").replace("0.1.0.dev0", "0.1.0.dev1"), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="version mismatch"):
        check(version_tree)
