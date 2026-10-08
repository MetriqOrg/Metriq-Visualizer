# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
"""Verified updates from the public Metriq repository; no downloaded code executes.

Metadata and asset requests use the repository's HTTPS API only. Redirects are
refused. All staging is private, bounded and separate from the installed app.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import plistlib
import re
import ctypes
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import unicodedata
import zipfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

GITHUB_REPOSITORY = "MetriqOrg/Metriq-Visualizer"
_API_ROOT = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases"
GITHUB_RELEASES_URL = _API_ROOT + "?per_page=20"
APP_BUNDLE_IDENTIFIER = "org.metriq.visualizer"
MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024
MAX_EXPANDED_BYTES = 4 * 1024 * 1024 * 1024
MAX_FILES = 30000
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
CHECK_INTERVAL = 24 * 60 * 60
_VERSION_RE = re.compile(r"^v?(\d{1,9})(?:\.(\d{1,9}))?(?:\.(\d{1,9}))?$")
_ASSET_RE = re.compile(r"Metriq-Visualizer-(?:v?\d+(?:\.\d+){0,2}-)?macOS-(arm64|x86_64|universal)(?:\.app)?\.zip", re.I)


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
    match = _VERSION_RE.fullmatch(str(value).strip())
    return tuple(int(part or 0) for part in match.groups()) if match else None


def is_newer_version(candidate: object, current: object) -> bool:
    newer, running = parse_version(candidate), parse_version(current)
    return newer is not None and running is not None and newer > running


def _safe_sha256(value: object) -> str | None:
    digest = str(value or "").lower().removeprefix("sha256:")
    return digest if re.fullmatch(r"[0-9a-f]{64}", digest) else None


def _macos_architecture(machine: str | None = None) -> str | None:
    return {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x86_64", "amd64": "x86_64"}.get(
        (machine or platform.machine()).lower())


def _asset_url_allowed(url: str) -> bool:
    return re.fullmatch(re.escape(_API_ROOT) + r"/assets/[1-9]\d*", url) is not None


def _validate_update(update: UpdateInfo, current_version: str | None = None) -> None:
    if not _asset_url_allowed(update.download_url):
        raise ValueError("Update assets must use this repository's HTTPS GitHub API.")
    match = _ASSET_RE.fullmatch(update.asset_name)
    arch = _macos_architecture()
    if not match or arch is None or match[1].lower() not in {arch, "universal"}:
        raise ValueError("The update is not a compatible macOS app ZIP.")
    if _safe_sha256(update.sha256) is None or not 0 < update.size <= MAX_DOWNLOAD_BYTES:
        raise ValueError("The update has no valid SHA-256 digest or bounded size.")
    if parse_version(update.version) is None or (current_version is not None and not is_newer_version(update.version, current_version)):
        raise ValueError("Updates must be stable and newer than the running version; downgrades are refused.")


def available_update(current_version: str, releases: Iterable[Mapping[str, Any]], *, machine: str | None = None) -> UpdateInfo | None:
    arch = _macos_architecture(machine)
    if arch is None:
        return None
    found = []
    for release in releases:
        if not isinstance(release, Mapping) or release.get("draft") is not False or release.get("prerelease") is not False:
            continue
        version = str(release.get("tag_name", ""))
        if not is_newer_version(version, current_version):
            continue
        assets = release.get("assets", [])
        if not isinstance(assets, list):
            continue
        for asset in assets:
            if not isinstance(asset, Mapping):
                continue
            name = str(asset.get("name", ""))
            match = _ASSET_RE.fullmatch(name)
            digest = _safe_sha256(asset.get("digest"))
            url = str(asset.get("url", ""))
            size = asset.get("size")
            if (not match or match[1].lower() not in {arch, "universal"} or not digest or
                    not _asset_url_allowed(url) or type(size) is not int or not 0 < size <= MAX_DOWNLOAD_BYTES):
                continue
            found.append(UpdateInfo(version.removeprefix("v"), name, url, digest, size,
                                    f"https://github.com/{GITHUB_REPOSITORY}/releases/tag/{version}",
                                    str(release.get("body", ""))[:10000]))
    return max(found, key=lambda item: (parse_version(item.version), "universal" not in item.asset_name.lower()), default=None)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("GitHub redirected this request; strict API-only update policy refused it.")


def _urlopen(request, *, timeout):
    # Ignore ambient proxies and credentials; never leave the approved API host.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    return opener.open(request, timeout=timeout)


def _request(url, accept):
    return urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "Metriq-Visualizer-Updater",
                                                "X-GitHub-Api-Version": "2022-11-28"})


def fetch_releases(*, urlopen: Callable[..., Any] | None = None) -> list[Mapping[str, Any]]:
    with (urlopen or _urlopen)(_request(GITHUB_RELEASES_URL, "application/vnd.github+json"), timeout=8) as response:
        raw = response.read(MAX_METADATA_BYTES + 1)
    if len(raw) > MAX_METADATA_BYTES:
        raise ValueError("Release metadata exceeds the size limit.")
    payload = json.loads(raw)
    if not isinstance(payload, list):
        raise ValueError("GitHub returned an invalid release list.")
    return [item for item in payload if isinstance(item, Mapping)]


def check_for_update(current_version: str, *, urlopen: Callable[..., Any] | None = None) -> UpdateInfo | None:
    return available_update(current_version, fetch_releases(urlopen=urlopen))


def download_verified_asset(update: UpdateInfo, destination: Path, *, urlopen: Callable[..., Any] | None = None) -> Path:
    _validate_update(update)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".download-", suffix=".part", dir=destination.parent)
    temporary = Path(name)
    digest, total = hashlib.sha256(), 0
    try:
        with os.fdopen(descriptor, "wb") as handle:
            with (urlopen or _urlopen)(_request(update.download_url, "application/octet-stream"), timeout=30) as response:
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    total += len(chunk)
                    if total > min(update.size, MAX_DOWNLOAD_BYTES):
                        raise ValueError("Download exceeds the advertised size limit.")
                    digest.update(chunk)
                    handle.write(chunk)
        if digest.hexdigest() != update.sha256.lower():
            raise ValueError("Downloaded update did not match GitHub's SHA-256 digest.")
        if total != update.size:
            raise ValueError("Downloaded update size does not match GitHub metadata.")
        temporary.replace(destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def _verify_archive(archive: Path, update: UpdateInfo) -> None:
    if archive.stat().st_size != update.size or update.size > MAX_DOWNLOAD_BYTES:
        raise ValueError("Archive size does not match the verified asset.")
    with archive.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != update.sha256.lower():
        raise ValueError("Archive SHA-256 digest does not match the verified asset.")


def _safe_extract(archive: Path, destination: Path) -> None:
    """Preflight all entries; permit only contained framework symlinks.

    No regular member may traverse a symlink. Links are created last and every
    resolved target must exist inside the sole application bundle.
    """
    def path_key(name):
        return unicodedata.normalize("NFD", name).casefold()

    with zipfile.ZipFile(archive) as package:
        members = package.infolist()
        if len(members) > MAX_FILES or sum(m.file_size for m in members) > MAX_EXPANDED_BYTES:
            raise ValueError("Update archive exceeds the file count or expanded size limit.")
        names, links, roots = set(), {}, set()
        for member in members:
            name = member.filename.rstrip("/")
            parts = name.split("/")
            mode = member.external_attr >> 16
            if (not name or any(p in {"", ".", ".."} for p in parts) or "\\" in name or ":" in name or
                    any(ord(c) < 32 for c in name) or path_key(name) in names):
                raise ValueError("Update archive contains an unsafe or duplicate path.")
            names.add(path_key(name))
            if (member.flag_bits & 1 or (member.file_size > 1024 * 1024 and member.file_size > MAX_COMPRESSION_RATIO * max(1, member.compress_size))):
                raise ValueError("Update archive contains encrypted data or a compression bomb.")
            kind = stat.S_IFMT(mode)
            if kind not in {0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK}:
                raise ValueError("Update archive contains a special file.")
            if parts[0] == "__MACOSX":
                continue  # ditto resource metadata; validated but never extracted
            if not parts[0].endswith(".app"):
                raise ValueError("Update archive must contain only one top-level application.")
            roots.add(parts[0])
            if stat.S_ISLNK(mode):
                if member.file_size > 4096:
                    raise ValueError("Update archive contains an oversized symbolic link.")
                target = package.read(member).decode("utf-8")
                if not target or target.startswith("/") or "\\" in target or ":" in target or any(ord(c) < 32 for c in target):
                    raise ValueError("Update archive contains an unsafe symbolic link.")
                links[name] = target
        if len(roots) != 1:
            raise ValueError("Update archive must contain exactly one application bundle.")
        # Detect all link ancestors before creating anything, regardless of order.
        link_names = {path_key(name) for name in links}
        for member in members:
            parts = PurePosixPath(member.filename).parts
            if any(path_key("/".join(parts[:i])) in link_names for i in range(1, len(parts))):
                raise ValueError("Update archive writes through a symbolic link.")
        destination.mkdir(mode=0o700)
        for member in members:
            if member.filename.split("/")[0] == "__MACOSX":
                continue
            path = destination / member.filename
            path.parent.mkdir(parents=True, exist_ok=True)
            if member.is_dir():
                path.mkdir(exist_ok=True)
            elif member.filename not in links:
                with package.open(member) as source, path.open("xb") as sink:
                    copied = 0
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        copied += len(chunk)
                        if copied > member.file_size:
                            raise ValueError("Expanded file exceeds its declared size.")
                        sink.write(chunk)
                path.chmod(0o755 if member.external_attr >> 16 & 0o111 else 0o644)
        for name, target in links.items():
            (destination / name).symlink_to(target)
        bundle = destination / next(iter(roots))
        for name in links:
            try:
                resolved = (destination / name).resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise ValueError("Update archive contains a broken or cyclic symbolic link.") from exc
            if not resolved.is_relative_to(bundle):
                raise ValueError("Update archive contains a symbolic link escape.")


def _bundle_info(bundle: Path) -> dict:
    try:
        with (bundle / "Contents" / "Info.plist").open("rb") as handle:
            raw = handle.read(MAX_METADATA_BYTES + 1)
        if len(raw) > MAX_METADATA_BYTES:
            raise ValueError("Bundle metadata exceeds the size limit.")
        info = plistlib.loads(raw)
        if not isinstance(info, dict) or info.get("CFBundleIdentifier") != APP_BUNDLE_IDENTIFIER:
            raise ValueError("The update bundle is not Metriq Visualizer.")
        return info
    except (OSError, plistlib.InvalidFileException) as exc:
        raise ValueError("The bundle has no valid Info.plist.") from exc


def extract_verified_app(archive: Path, destination: Path, update: UpdateInfo, *, current_version: str) -> Path:
    _validate_update(update, current_version)
    _verify_archive(archive, update)  # Never even open the ZIP before digest verification.
    if destination.exists() or destination.is_symlink():
        raise ValueError("Extraction requires a new private staging directory.")
    try:
        _safe_extract(archive, destination)
        bundle = next(p for p in destination.iterdir() if p.name.endswith(".app"))
        info = _bundle_info(bundle)
        if parse_version(info.get("CFBundleShortVersionString")) != parse_version(update.version):
            raise ValueError("The update bundle version does not match its GitHub release.")
        return bundle
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def verify_macos_bundle(bundle: Path, *, runner: Callable[..., Any] | None = None) -> None:
    runner = runner or subprocess.run
    executable_name = _bundle_info(bundle).get("CFBundleExecutable", "")
    if not isinstance(executable_name, str) or not executable_name or Path(executable_name).name != executable_name:
        raise ValueError("The bundle has an invalid executable name.")
    executable = bundle / "Contents" / "MacOS" / executable_name
    if not executable.is_file() or not executable.resolve().is_relative_to(bundle.resolve()):
        raise ValueError("The bundle executable is unavailable or escapes the bundle.")
    for command in (["/usr/bin/codesign", "--verify", "--deep", "--strict", str(bundle)],
                    ["/usr/bin/lipo", "-archs", str(executable)]):
        result = runner(command, capture_output=True, text=True, check=False, timeout=60)
        if result.returncode != 0:
            raise ValueError("Update signature or executable architecture verification failed.")
    if _macos_architecture() not in result.stdout.split():
        raise ValueError("The update executable does not support the running architecture.")


def installed_app_bundle(executable: Path | None = None) -> Path | None:
    if executable is None and not getattr(sys, "frozen", False):
        return None
    candidate = (executable or Path(sys.executable)).resolve()
    return next((parent for parent in candidate.parents if parent.name.endswith(".app")), None)


def _tree_digest(bundle: Path) -> str:
    """Seal all staged bytes, modes and symlink targets, not only signed resources."""
    digest = hashlib.sha256()
    for path in sorted(bundle.rglob("*")):
        relative = str(path.relative_to(bundle)).encode("utf-8")
        mode = path.lstat().st_mode
        digest.update(len(relative).to_bytes(8, "big") + relative + mode.to_bytes(8, "big"))
        if path.is_symlink():
            content = os.readlink(path).encode("utf-8")
            digest.update(hashlib.sha256(content).digest())
        elif path.is_file():
            with path.open("rb") as handle:
                digest.update(hashlib.file_digest(handle, "sha256").digest())
        elif not path.is_dir():
            raise ValueError("Bundle contains a special file.")
    return digest.hexdigest()


def _atomic_exchange(candidate: Path, target: Path) -> None:
    """Darwin RENAME_SWAP: both paths remain present, even across interruption."""
    if sys.platform != "darwin":
        raise ValueError("Atomic app exchange requires macOS.")
    swap = ctypes.CDLL(None, use_errno=True).renamex_np
    swap.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    swap.restype = ctypes.c_int
    if swap(os.fsencode(candidate), os.fsencode(target), 0x00000002) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _wait_for_exit(pid: int) -> None:
    deadline = time.monotonic() + CHECK_INTERVAL
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        # Permission errors fail closed; PID reuse can only delay or abort.
        if time.monotonic() >= deadline:
            raise TimeoutError("Application did not exit within 24 hours.")
        time.sleep(0.2)


def run_deferred_install(manifest: Path) -> None:
    """Run inside the ORIGINAL app's trusted helper process, never the new app.

    Atomic exchange is the final fallible install operation. The old app stays
    at the candidate path inside the private sibling folder for rollback. No
    resource is loaded from the replacement after exchange.
    """
    import fcntl  # macOS helper only; Windows/Linux UI imports stay portable.
    manifest = manifest.resolve()
    with manifest.open("rb") as handle:
        raw = handle.read(MAX_METADATA_BYTES + 1)
    if len(raw) > MAX_METADATA_BYTES:
        raise ValueError("Invalid install manifest.")
    payload = json.loads(raw)
    root = manifest.parent
    target, candidate = Path(payload["target"]), Path(payload["candidate"])
    if (not root.name.startswith(".metriq-update-") or root.parent != target.parent or
            candidate.parent != root or candidate.name != target.name or
            not target.name.endswith(".app") or target.is_symlink() or candidate.is_symlink()):
        raise ValueError("Invalid deferred install paths.")
    pid = int(payload["pid"])
    if pid <= 1:
        raise ValueError("Invalid application process ID.")
    _wait_for_exit(pid)
    lock_path = target.parent / f".{target.name}.update-lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if target.is_symlink() or candidate.is_symlink() or not target.is_dir() or not candidate.is_dir():
            raise ValueError("The application paths changed while waiting for exit.")
        old, new = _bundle_info(target), _bundle_info(candidate)
        if (parse_version(old.get("CFBundleShortVersionString")) != parse_version(payload["current_version"]) or
                parse_version(new.get("CFBundleShortVersionString")) != parse_version(payload["version"]) or
                not is_newer_version(payload["version"], payload["current_version"])):
            raise ValueError("Bundle versions changed or the update is a downgrade.")
        verify_macos_bundle(candidate)
        if _tree_digest(target) != payload["old_digest"] or _tree_digest(candidate) != payload["new_digest"]:
            raise ValueError("An application changed while waiting for exit.")
        _atomic_exchange(candidate, target)
        # The complete original app is now candidate, a permanent rollback copy.


def schedule_macos_install(staged_bundle: Path, installed_bundle: Path, *, current_version: str,
                           process_id: int | None = None, launcher: Callable[..., Any] | None = None) -> Path:
    staged, target = staged_bundle.resolve(), installed_bundle.resolve()
    if (staged == target or target.is_relative_to(staged) or staged.is_relative_to(target) or
            not staged.name.endswith(".app") or not target.name.endswith(".app") or
            installed_bundle.is_symlink() or not target.is_dir()):
        raise ValueError("Invalid application update target.")
    old, new = _bundle_info(target), _bundle_info(staged)
    if parse_version(old.get("CFBundleShortVersionString")) != parse_version(current_version):
        raise ValueError("Installed version differs from the running version.")
    if not is_newer_version(new.get("CFBundleShortVersionString"), current_version):
        raise ValueError("Downgrades and reinstalling the current version are refused.")
    verify_macos_bundle(staged)
    pid = int(process_id if process_id is not None else os.getpid())
    if pid <= 1:
        raise ValueError("Invalid application process ID.")
    root = Path(tempfile.mkdtemp(prefix=".metriq-update-", dir=target.parent))
    candidate = root / target.name
    manifest = root / "install.json"
    try:
        shutil.copytree(staged, candidate, symlinks=True)
        verify_macos_bundle(candidate)
        manifest.write_text(json.dumps(dict(target=str(target), candidate=str(candidate), pid=pid,
            current_version=current_version, version=new["CFBundleShortVersionString"],
            old_digest=_tree_digest(target), new_digest=_tree_digest(candidate))), encoding="utf-8")
        manifest.chmod(0o600)
        # Frozen app: run the currently installed executable in helper-only mode.
        # Source invocation exists for local diagnostics, not user installation.
        command = ([sys.executable] if getattr(sys, "frozen", False) else
                   [sys.executable, str(Path(__file__).resolve())])
        (launcher or subprocess.Popen)(command + ["--metriq-install-helper", str(manifest)],
            env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}, start_new_session=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return manifest
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise


def helper_main(manifest: Path) -> int:
    try:
        run_deferred_install(manifest)
        return 0
    except Exception as exc:
        # Local diagnostics only. Keep failed staging for inspection; never retry
        # a replacement automatically and never send a failure report anywhere.
        try:
            (manifest.parent / "failure.txt").write_text(str(exc)[:2000], encoding="utf-8")
        except OSError:
            pass
        return 1


def prepare_update_install(update: UpdateInfo, installed_bundle: Path, *, current_version: str,
                           confirmed: bool = False) -> Path:
    if not confirmed:
        raise ValueError("Explicit user confirmation is required before downloading an update.")
    if sys.platform != "darwin":
        raise ValueError("Automatic installation is supported only on macOS.")
    _validate_update(update, current_version)
    root = Path(tempfile.mkdtemp(prefix="metriq-update-"))
    try:
        archive = download_verified_asset(update, root / "update.zip")
        bundle = extract_verified_app(archive, root / "extracted", update, current_version=current_version)
        return schedule_macos_install(bundle, installed_bundle, current_version=current_version)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def claim_startup_check(settings, *, now: float | None = None) -> bool:
    """Persist an attempt before HTTP, including offline attempts; fail closed."""
    from PySide6.QtCore import QLockFile
    lock = QLockFile(settings.fileName() + ".updates.lock")
    if not lock.tryLock(0):
        return False
    try:
        settings.sync()  # Reload a claim made by another app instance.
        if not settings.value("updates/enabled", True, type=bool):
            return False
        stamp = time.time() if now is None else now
        previous = float(settings.value("updates/last_check", 0))
        if previous and stamp - previous < CHECK_INTERVAL:
            return False
        settings.setValue("updates/last_check", stamp)
        settings.sync()
        return settings.status() == settings.Status.NoError
    except (ValueError, TypeError, OSError):
        return False
    finally:
        lock.unlock()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--metriq-install-helper":
        raise SystemExit(helper_main(Path(sys.argv[2])))
    raise SystemExit("This module is not an application entry point.")
