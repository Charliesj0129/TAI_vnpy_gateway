#!/usr/bin/env python
"""
Apply deterministic renames defined in rename_map.yml to enforce naming consistency.

Usage:
    python codemod_rename.py <path> [<path> ...]
Options:
    --dry-run    Preview changes without writing files.

Depends on libcst>=1.1.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict

import libcst as cst

ROOT = Path(__file__).resolve().parent


def load_mapping() -> Dict[str, Dict[str, str]]:
    mapping_path = ROOT / "rename_map.yml"
    if not mapping_path.exists():
        raise SystemExit("rename_map.yml not found.")

    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover - fallback if PyYAML unavailable
        raise SystemExit("Install PyYAML to use codemod_rename.py") from exc

    raw = yaml.safe_load(mapping_path.read_text(encoding="utf-8")) or {}
    renames = raw.get("renames", [])
    file_map: Dict[str, Dict[str, str]] = {}
    for entry in renames:
        source = entry.get("source")
        old = entry.get("old")
        new = entry.get("new")
        if not source or not old or not new:
            continue
        rel_path = source.split(":", 1)[0]
        file_map.setdefault(rel_path, {})[old] = new
        file_map.setdefault("__global__", {}).setdefault(old, new)
    return file_map


class RenameTransformer(cst.CSTTransformer):
    def __init__(self, rel_path: str, mapping: Dict[str, Dict[str, str]]) -> None:
        self.rel_path = rel_path.replace("\\", "/")
        self.mapping = mapping.get("__global__", {}).copy()
        self.mapping.update(mapping.get(self.rel_path, {}))

    def leave_Name(self, original_node: cst.Name, updated_node: cst.Name) -> cst.CSTNode:
        rename = self.mapping.get(updated_node.value)
        if rename:
            return updated_node.with_changes(value=rename)
        return updated_node

    def leave_Attribute(
        self, original_node: cst.Attribute, updated_node: cst.Attribute
    ) -> cst.CSTNode:
        if isinstance(updated_node.attr, cst.Name):
            rename = self.mapping.get(updated_node.attr.value)
            if rename:
                return updated_node.with_changes(attr=cst.Name(rename))
        return updated_node


def collect_targets(targets: list[str]) -> list[Path]:
    paths: list[Path] = []
    for target in targets:
        path = Path(target).resolve()
        if path.is_file() and path.suffix == ".py":
            paths.append(path)
        elif path.is_dir():
            paths.extend(sorted(p for p in path.rglob("*.py")))
    return paths


def apply(path: Path, transformer: RenameTransformer, dry_run: bool) -> bool:
    source = path.read_text(encoding="utf-8")
    module = cst.parse_module(source)
    updated = module.visit(transformer)
    if updated.code == source:
        return False
    if dry_run:
        print(f"[DRY-RUN] {path.relative_to(ROOT)} would be updated.")
    else:
        path.write_text(updated.code, encoding="utf-8")
        print(f"[APPLIED] {path.relative_to(ROOT)}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply rename_map.yml transformations.")
    parser.add_argument("targets", nargs="+", help="Files or directories to transform.")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing.")
    args = parser.parse_args()

    mapping = load_mapping()
    any_changes = False
    for path in collect_targets(args.targets):
        rel = str(path.relative_to(ROOT))
        transformer = RenameTransformer(rel, mapping)
        if apply(path, transformer, args.dry_run):
            any_changes = True
    if args.dry_run and not any_changes:
        print("No changes detected.")


if __name__ == "__main__":
    main()
