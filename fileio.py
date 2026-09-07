"""File-writing helpers shared by core.py and every editor backend.

Kept separate from core.py so editor backends can import them without a
circular import (core imports editors, editors import fileio).
"""
import json
import shutil
from pathlib import Path


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def backup_if_exists(path: Path):
    if path.is_file():
        bak = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, bak)


def write_text(path: Path, text: str, dry_run: bool):
    print(f"  write {path}" + (" (dry-run)" if dry_run else ""))
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_if_exists(path)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, data, dry_run: bool):
    write_text(path, json.dumps(data, indent=2) + "\n", dry_run)


def load_json(path: Path):
    """Return parsed JSON, or None if the file is missing/unparseable."""
    if not path.is_file():
        return None
    try:
        return json.loads(read_text(path))
    except json.JSONDecodeError:
        return None


def deep_merge(base: dict, updates: dict) -> dict:
    """Recursively merge updates into base; updates win on conflicts.
    Lists are replaced, not concatenated."""
    out = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def merge_json(path: Path, updates: dict, dry_run: bool, deep: bool = False):
    """Merge updates into an existing JSON object file (or create it).

    deep=False mirrors dict.update() - top-level keys are replaced wholesale.
    deep=True merges nested objects, which is what editors whose settings
    file has one big namespaced object (e.g. Zed's "lsp"/"terminal") need.
    """
    existing = load_json(path)
    if existing is None:
        if path.is_file():
            print(f"  WARNING: {path} was not valid JSON, backing up and replacing it.")
        existing = {}
    merged = deep_merge(existing, updates) if deep else {**existing, **updates}
    write_json(path, merged, dry_run)


def append_gitignore(project_dir: Path, wanted, dry_run: bool):
    gitignore = project_dir / ".gitignore"
    lines = []
    if gitignore.is_file():
        lines = read_text(gitignore).splitlines()
    to_add = [w for w in wanted if w not in lines]
    if not to_add:
        return
    print(f"  update {gitignore} (+{', '.join(to_add)})" + (" (dry-run)" if dry_run else ""))
    if dry_run:
        return
    with gitignore.open("a", encoding="utf-8") as f:
        if lines and lines[-1] != "":
            f.write("\n")
        for w in to_add:
            f.write(w + "\n")
