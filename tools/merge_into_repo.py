#!/usr/bin/env python3
"""Safely merge this complete source distribution into an existing checkout.

The tool never deletes target files and never touches ``.git``. Existing files
are backed up before atomic replacement. It exists for Codex/agent handoff; the
ZIP itself is also a complete standalone repository tree.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_NAMES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "build-out",
    "dist",
    ".metriq-merge-backup",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".tmp"}
REPORT_NAME = ".metriq-merge-report.json"


@dataclass(frozen=True)
class FileAction:
    path: str
    action: str
    sha256: str
    previous_sha256: str = ""
    backup: str = ""


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def source_files(root: Path) -> Iterable[Path]:
    """Yield only release-manifest files when a package manifest is present.

    This prevents generated build products, caches, virtual environments, or
    unrelated files created after extraction from being merged into a checkout.
    The manifest itself is included after every listed payload file verifies.
    """

    manifest_path = root / "PACKAGE_MANIFEST.json"
    if manifest_path.is_file():
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Could not read package manifest: {exc}") from exc
        if str(payload.get("schema", "")) != "metriq.complete-source-manifest":
            raise ValueError("Unsupported or invalid package manifest schema.")
        entries = payload.get("files")
        if not isinstance(entries, list):
            raise ValueError("Package manifest is missing its file list.")
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Package manifest contains an invalid file entry.")
            text = str(entry.get("path", ""))
            relative = Path(text)
            if not text or relative.is_absolute() or ".." in relative.parts or text in seen:
                raise ValueError(f"Unsafe or duplicate package-manifest path: {text!r}")
            seen.add(text)
            source = root / relative
            if source.is_symlink() or not source.is_file():
                raise ValueError(f"Package-manifest file is missing or unsafe: {text}")
            expected = str(entry.get("sha256", ""))
            if len(expected) != 64 or digest(source) != expected:
                raise ValueError(f"Package checksum mismatch: {text}")
            yield source
        yield manifest_path
        return

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == REPORT_NAME:
            continue
        relative = path.relative_to(root)
        if any(part in EXCLUDED_NAMES or part.endswith(".egg-info") for part in relative.parts):
            continue
        if relative.parts[:2] == ("build", "lib") or any(part.startswith("bdist.") for part in relative.parts):
            continue
        if path.suffix.lower() in EXCLUDED_SUFFIXES:
            continue
        yield path


def assert_safe_target(target: Path) -> None:
    target = target.resolve()
    if target == Path(target.anchor):
        raise ValueError("Refusing to use a filesystem root as the target.")
    if target == SOURCE_ROOT.resolve():
        raise ValueError("The source package is already complete; choose a separate repository checkout.")
    if not target.is_dir():
        raise ValueError(f"Target directory does not exist: {target}")
    if not (target / ".git").exists():
        raise ValueError("Target does not appear to be a Git checkout: .git is missing.")


def safe_destination(target: Path, relative: Path) -> Path:
    target_root = target.resolve()
    raw_destination = target_root / relative
    current = target_root
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Refusing to traverse target symlink: {current}")
        if current.exists() and not current.is_dir():
            raise ValueError(f"Target parent path is not a directory: {current}")
    # Check the unresolved final component first. Resolving before this test
    # follows the link and can silently replace the link target instead.
    if raw_destination.is_symlink():
        raise ValueError(f"Refusing to replace target symlink: {raw_destination}")
    resolved_destination = raw_destination.resolve(strict=False)
    try:
        resolved_destination.relative_to(target_root)
    except ValueError as exc:
        raise ValueError(f"Unsafe path outside target: {relative}") from exc
    if raw_destination.exists() and not raw_destination.is_file():
        raise ValueError(f"Target path exists but is not a regular file: {raw_destination}")
    return raw_destination


def add_local_git_ignores(target: Path) -> None:
    git_path = target / ".git"
    info = git_path / "info"
    if not git_path.is_dir():
        return
    info.mkdir(parents=True, exist_ok=True)
    exclude = info / "exclude"
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    additions = [".metriq-merge-backup/", REPORT_NAME]
    missing = [entry for entry in additions if entry not in existing.splitlines()]
    if missing:
        with exclude.open("a", encoding="utf-8") as handle:
            if existing and not existing.endswith("\n"):
                handle.write("\n")
            handle.write("\n# Metriq source merge local artifacts\n")
            for entry in missing:
                handle.write(entry + "\n")


def merge(target: Path, *, dry_run: bool) -> dict:
    assert_safe_target(target)
    target = target.resolve()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_root = target / ".metriq-merge-backup" / timestamp
    actions: list[FileAction] = []

    # Preflight all destinations before writing anything.
    entries: list[tuple[Path, Path, Path]] = []
    for source in source_files(SOURCE_ROOT):
        relative = source.relative_to(SOURCE_ROOT)
        destination = safe_destination(target, relative)
        entries.append((source, relative, destination))

    for source, relative, destination in entries:
        source_hash = digest(source)
        path_text = relative.as_posix()
        if destination.is_file():
            previous_hash = digest(destination)
            if previous_hash == source_hash:
                actions.append(FileAction(path_text, "unchanged", source_hash, previous_hash))
                continue
            # Preset libraries are user content as well as application content.
            # Never replace an existing creator preset merely because a release
            # ships a built-in file with the same name.  The v1.12.5 loader can
            # read the historical schema directly.
            if relative.parts and relative.parts[0] == "presets" and relative.suffix.lower() == ".mvpreset":
                actions.append(FileAction(path_text, "preserved", source_hash, previous_hash))
                continue
            backup = backup_root / relative
            actions.append(FileAction(path_text, "updated", source_hash, previous_hash, backup.relative_to(target).as_posix()))
        else:
            actions.append(FileAction(path_text, "added", source_hash))

    if not dry_run:
        for source, relative, destination in entries:
            action = next(item for item in actions if item.path == relative.as_posix())
            if action.action in {"unchanged", "preserved"}:
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                backup = backup_root / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destination, backup)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.name}.metriq-",
                suffix=".tmp",
                dir=destination.parent,
            )
            os.close(descriptor)
            temporary = Path(temporary_name)
            try:
                shutil.copy2(source, temporary)
                if digest(temporary) != action.sha256:
                    raise RuntimeError(f"Checksum verification failed while copying {relative}")
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
        add_local_git_ignores(target)

    summary = {
        "schema": "metriq.source-merge-report",
        "schema_version": 1,
        "source_version": (SOURCE_ROOT / "VERSION.txt").read_text(encoding="utf-8").strip(),
        "source_root": str(SOURCE_ROOT),
        "target": str(target),
        "dry_run": dry_run,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            key: sum(1 for action in actions if action.action == key)
            for key in ("added", "updated", "unchanged", "preserved")
        },
        "actions": [asdict(action) for action in actions],
    }
    if not dry_run:
        report = target / REPORT_NAME
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{report.name}.", suffix=".tmp", dir=report.parent
        )
        os.close(descriptor)
        temporary_report = Path(temporary_name)
        try:
            temporary_report.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
            temporary_report.replace(report)
        finally:
            temporary_report.unlink(missing_ok=True)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, type=Path, help="Existing Metriq Visualizer Git checkout")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    parser.add_argument("--json", action="store_true", help="Print the full JSON report")
    arguments = parser.parse_args()
    try:
        result = merge(arguments.target, dry_run=arguments.dry_run)
    except Exception as exc:  # noqa: BLE001
        print(f"merge failed: {exc}", file=sys.stderr)
        return 2
    if arguments.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        counts = result["counts"]
        mode = "DRY RUN" if arguments.dry_run else "APPLIED"
        print(
            f"{mode}: {counts['added']} added, {counts['updated']} updated, "
            f"{counts['unchanged']} unchanged, {counts['preserved']} presets preserved"
        )
        if not arguments.dry_run:
            print(f"Report: {Path(result['target']) / REPORT_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
