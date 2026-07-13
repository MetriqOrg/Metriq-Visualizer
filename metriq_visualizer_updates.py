# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
"""Verified, user-approved updates from Metriq's public GitHub releases.

The updater intentionally accepts only a non-prerelease macOS app archive that
GitHub supplies with a SHA-256 digest.  It never treats a source archive as an
application update and the UI must obtain an explicit confirmation before a
download can replace a running application.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import plistlib
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GITHUB_REPOSITORY = "MetriqOrg/Metriq-Visualizer"
GITHUB_RELEASES_URL = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases?per_page=20"
APP_BUNDLE_IDENTIFIER = "org.metriq.visualizer"
_VERSION_RE = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?$")


@dataclass(frozen=True, slots=True)
class UpdateInfo:
    version: str
    asset_name: str
    download_url: str
    sha256: str
    size: int
    release_url: str
    notes: str = ""


def parse_version(value: object) -> tuple[int, int, int] | None:
    """Parse stable release versions without accepting pre-release labels."""

    match = _VERSION_RE.fullmatch(str(value).strip())
    if match is None:
        return None
    return tuple(int(component or 0) for component in match.groups())  # type: ignore[return-value]


def is_newer_version(candidate: object, current: object) -> bool:
    candidate_version = parse_version(candidate)
    current_version = parse_version(current)
    return bool(candidate_version is not None and current_version is not None and candidate_version > current_version)


def _safe_sha256(value: object) -> str | None:
    text = str(value or "").strip().casefold()
    if text.startswith("sha256:"):
        text = text.partition(":")[2]
    return text if re.fullmatch(r"[0-9a-f]{64}", text) else None


def _macos_architecture(machine: str | None = None) -> str | None:
    """Return the current macOS CPU family using release-artifact labels."""

    name = str(machine or platform.machine()).strip().casefold().replace("-", "_")
    if name in {"arm64", "aarch64"}:
        return "arm64"
    if name in {"x86_64", "amd64", "x64", "intel"}:
        return "x86_64"
    return None


def _asset_macos_architecture(name: str) -> str | None:
    """Read a macOS artifact architecture from its stable release filename."""

    normalized = str(name).casefold().replace("-", "_")
    if "universal" in normalized:
        return "universal"
    if "arm64" in normalized or "aarch64" in normalized:
        return "arm64"
    if "x86_64" in normalized or "amd64" in normalized or "intel" in normalized:
        return "x86_64"
    return None


def _macos_asset(release: Mapping[str, Any], *, machine: str | None = None) -> Mapping[str, Any] | None:
    """Select a signed, CPU-compatible macOS archive, never source code."""

    candidates: list[tuple[int, Mapping[str, Any]]] = []
    target_architecture = _macos_architecture(machine)
    for raw in release.get("assets", ()):
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name", "")).casefold()
        if not name.endswith(".zip") or "source" in name or "complete-source" in name:
            continue
        digest = _safe_sha256(raw.get("digest"))
        url = str(raw.get("browser_download_url", "")).strip()
        if digest is None or not url.startswith("https://"):
            continue
        asset_architecture = _asset_macos_architecture(name)
        if (
            target_architecture is not None
            and asset_architecture is not None
            and asset_architecture not in {target_architecture, "universal"}
        ):
            continue
        score = 0
        if "macos" in name or "mac" in name or "darwin" in name:
            score += 100
        if ".app" in name or "app" in name:
            score += 40
        if asset_architecture == target_architecture:
            score += 30
        elif asset_architecture == "universal":
            score += 20
        elif asset_architecture is None:
            score += 10
        if score:
            candidates.append((score, raw))
    return max(candidates, key=lambda candidate: candidate[0])[1] if candidates else None


def available_update(
    current_version: str,
    releases: Iterable[Mapping[str, Any]],
    *,
    machine: str | None = None,
) -> UpdateInfo | None:
    """Return the newest compatible stable release newer than ``current_version``."""

    found: list[UpdateInfo] = []
    for release in releases:
        if bool(release.get("draft")) or bool(release.get("prerelease")):
            continue
        version = str(release.get("tag_name") or release.get("name") or "").strip()
        if not is_newer_version(version, current_version):
            continue
        asset = _macos_asset(release, machine=machine)
        if asset is None:
            continue
        digest = _safe_sha256(asset.get("digest"))
        if digest is None:  # defensive; _macos_asset already verifies this
            continue
        found.append(
            UpdateInfo(
                version=version.removeprefix("v"),
                asset_name=str(asset.get("name", "Metriq-Visualizer-macos.zip")),
                download_url=str(asset["browser_download_url"]),
                sha256=digest,
                size=max(0, int(asset.get("size", 0))),
                release_url=str(release.get("html_url", f"https://github.com/{GITHUB_REPOSITORY}/releases")),
                notes=str(release.get("body", "")),
            )
        )
    return max(found, key=lambda item: parse_version(item.version) or (0, 0, 0), default=None)


def fetch_releases(*, urlopen: Callable[..., Any] = urllib.request.urlopen) -> list[Mapping[str, Any]]:
    """Fetch public release metadata from GitHub without credentials."""

    request = urllib.request.Request(
        GITHUB_RELEASES_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Metriq-Visualizer-Updater",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=8) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError("GitHub returned an invalid release list.")
    return [item for item in payload if isinstance(item, Mapping)]


def check_for_update(current_version: str, *, urlopen: Callable[..., Any] = urllib.request.urlopen) -> UpdateInfo | None:
    return available_update(current_version, fetch_releases(urlopen=urlopen))


def download_verified_asset(
    update: UpdateInfo,
    destination: Path,
    *,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> Path:
    """Download ``update`` and verify the digest GitHub attached to its asset."""

    if not update.download_url.startswith("https://"):
        raise ValueError("Updates must use HTTPS.")
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    digest = hashlib.sha256()
    request = urllib.request.Request(update.download_url, headers={"User-Agent": "Metriq-Visualizer-Updater"})
    try:
        with urlopen(request, timeout=30) as response, temporary.open("wb") as handle:
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                handle.write(chunk)
                digest.update(chunk)
        if digest.hexdigest().casefold() != update.sha256.casefold():
            raise ValueError("Downloaded update did not match GitHub's SHA-256 digest.")
        temporary.replace(destination)
        return destination
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _safe_extract(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            target = (destination / member.filename).resolve()
            if target != destination and destination not in target.parents:
                raise ValueError("Update archive contains an unsafe path.")
            if stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError("Update archive contains a symbolic link.")
        package.extractall(destination)


def extract_verified_app(archive: Path, destination: Path, update: UpdateInfo) -> Path:
    """Extract one matching app bundle and validate its identity and version."""

    destination.mkdir(parents=True, exist_ok=True)
    _safe_extract(archive, destination)
    bundles = [path for path in destination.rglob("*.app") if path.is_dir()]
    if len(bundles) != 1:
        raise ValueError("The update archive must contain exactly one application bundle.")
    bundle = bundles[0].resolve()
    info_path = bundle / "Contents" / "Info.plist"
    try:
        with info_path.open("rb") as handle:
            info = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException) as exc:
        raise ValueError("The update bundle has no valid Info.plist.") from exc
    if str(info.get("CFBundleIdentifier", "")) != APP_BUNDLE_IDENTIFIER:
        raise ValueError("The update bundle is not Metriq Visualizer.")
    bundle_version = str(info.get("CFBundleShortVersionString", ""))
    if parse_version(bundle_version) != parse_version(update.version):
        raise ValueError("The update bundle version does not match its GitHub release.")
    return bundle


def verify_macos_bundle(bundle: Path, *, runner: Callable[..., Any] = subprocess.run) -> None:
    """Verify the signed staged bundle before it can replace the installed app."""

    result = runner(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(bundle)],
        capture_output=True,
        text=True,
        check=False,
    )
    if int(getattr(result, "returncode", 1)) != 0:
        message = str(getattr(result, "stderr", "")).strip() or "codesign verification failed."
        raise ValueError(message)


def installed_app_bundle(executable: Path | None = None) -> Path | None:
    """Locate this frozen app's bundle; source launches are deliberately not updatable."""

    candidate = (executable or Path(sys.executable)).expanduser().resolve()
    return next((parent for parent in candidate.parents if parent.name.endswith(".app")), None)


def schedule_macos_install(
    staged_bundle: Path,
    installed_bundle: Path,
    *,
    process_id: int | None = None,
    launcher: Callable[..., Any] = subprocess.Popen,
) -> Path:
    """Schedule an atomic replacement after the current application exits."""

    staged = staged_bundle.resolve()
    target = installed_bundle.resolve()
    if staged == target or not staged.name.endswith(".app") or not target.name.endswith(".app"):
        raise ValueError("Invalid application update target.")
    if not staged.is_dir() or not target.parent.is_dir():
        raise ValueError("The staged or installed application bundle is unavailable.")
    support = Path.home() / "Library" / "Application Support" / "Metriq Visualizer" / "updates"
    support.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = target.with_name(f"{target.stem} previous {stamp}.app")
    script = support / f"install-{stamp}.sh"
    pid = int(process_id if process_id is not None else os.getpid())
    script.write_text(
        "#!/bin/sh\nset -eu\n"
        f"while kill -0 {pid} 2>/dev/null; do sleep 0.2; done\n"
        f"if [ -d {shlex.quote(str(target))} ]; then mv {shlex.quote(str(target))} {shlex.quote(str(backup))}; fi\n"
        f"mv {shlex.quote(str(staged))} {shlex.quote(str(target))}\n"
        f"open -n {shlex.quote(str(target))}\n"
        'rm -f "$0"\n',
        encoding="utf-8",
    )
    script.chmod(0o700)
    launcher(["/bin/sh", str(script)], start_new_session=True)
    return script


def prepare_update_install(update: UpdateInfo, installed_bundle: Path) -> Path:
    """Download, check, stage, and schedule a verified replacement."""

    root = Path(tempfile.mkdtemp(prefix="metriq-update-"))
    try:
        archive = download_verified_asset(update, root / update.asset_name)
        bundle = extract_verified_app(archive, root / "extracted", update)
        verify_macos_bundle(bundle)
        return schedule_macos_install(bundle, installed_bundle)
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise


__all__ = [
    "APP_BUNDLE_IDENTIFIER",
    "GITHUB_RELEASES_URL",
    "UpdateInfo",
    "available_update",
    "check_for_update",
    "download_verified_asset",
    "extract_verified_app",
    "installed_app_bundle",
    "is_newer_version",
    "parse_version",
    "prepare_update_install",
    "schedule_macos_install",
    "verify_macos_bundle",
]
