"""Read static project settings without importing application code."""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    root: Path
    entry: str
    ui_dir: Path
    ui_entry: str


def load_config(root: Path | None = None) -> Config:
    root = (root or Path.cwd()).resolve()
    with (root / "pyproject.toml").open("rb") as stream:
        settings = tomllib.load(stream).get("tool", {}).get("pyyaki", {})
    entry = settings.get("entry", "app:app")
    if not isinstance(entry, str) or not re.fullmatch(
        r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", entry
    ):
        raise ValueError("entry must have the form module:app")
    ui_name = settings.get("ui-dir", "ui")
    ui_entry = settings.get("ui-entry", "index.html")
    if not isinstance(ui_name, str) or not isinstance(ui_entry, str):
        raise ValueError("UI settings must be relative path strings")
    ui_dir = (root / ui_name).resolve()
    page = (ui_dir / ui_entry).resolve()
    if (
        Path(ui_name).is_absolute()
        or Path(ui_entry).is_absolute()
        or not ui_dir.is_relative_to(root)
        or not page.is_relative_to(ui_dir)
        or not page.is_file()
    ):
        raise ValueError("UI entry must be an existing file inside the project's UI directory")
    return Config(root, entry, ui_dir, ui_entry)
