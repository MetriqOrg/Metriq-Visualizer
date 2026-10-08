"""Updater security boundaries; all HTTP and platform verification is mocked."""
import hashlib
import io
import plistlib
import stat
import subprocess
import sys
import zipfile
from email.message import Message
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import metriq_visualizer_updates as updates

API_ASSET = "https://api.github.com/repos/MetriqOrg/Metriq-Visualizer/releases/assets/42"
_REAL_OPENER_OPEN = updates.urllib.request.OpenerDirector.open


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Tests must never make real network calls")
    monkeypatch.setattr(updates.urllib.request.OpenerDirector, "open", forbidden)
    monkeypatch.setattr(updates.urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(updates.platform, "machine", lambda: "arm64")


def release(name="Metriq-Visualizer-macOS-arm64.zip", **changes):
    result = dict(tag_name="v1.14.0", draft=False, prerelease=False, assets=[dict(
        name=name, url=API_ASSET, digest="sha256:" + "a" * 64, size=123,
        browser_download_url="https://github.com/MetriqOrg/Metriq-Visualizer/releases/download/v1.14.0/" + name)])
    result.update(changes)
    return result


# The reference accepts vaguely named, unknown-architecture, or foreign assets.
@pytest.mark.parametrize("name", ["mac-tools.zip", "app.zip", "Metriq-Visualizer-macOS.zip",
    "Metriq-Visualizer-macOS-x86_64.zip", "Metriq-Visualizer-Complete-Source.zip",
    "Metriq-Visualizer-Windows-arm64.zip", "../Metriq-Visualizer-macOS-arm64.zip"])
def test_wrong_asset_is_refused(name):
    assert updates.available_update("1.13.0", [release(name)], machine="arm64") is None


@pytest.mark.parametrize("changes", [dict(prerelease=True), dict(draft=True), dict(tag_name="v1.13.0"),
    dict(tag_name="v1.12.8"), dict(tag_name="v1.14.0-beta")])
def test_prerelease_draft_and_downgrade_are_refused(changes):
    assert updates.available_update("1.13.0", [release(**changes)], machine="arm64") is None


def test_selects_exact_architecture_and_repository_api():
    candidate = updates.available_update("1.13.0", [release()], machine="arm64")
    assert candidate.version == "1.14.0"
    assert candidate.download_url == API_ASSET
    assert updates.available_update("1.13.0", [release()], machine="unknown") is None


@pytest.mark.parametrize("url", ["http://api.github.com/repos/MetriqOrg/Metriq-Visualizer/releases/assets/42",
    "https://api.github.com.evil.test/repos/MetriqOrg/Metriq-Visualizer/releases/assets/42",
    "https://api.github.com/repos/Other/Repo/releases/assets/42",
    "https://api.github.com@evil.test/file", API_ASSET + "?redirect=evil", API_ASSET + "/../99"])
def test_foreign_or_ambiguous_asset_url_is_refused(url):
    data = release()
    data["assets"][0]["url"] = url
    assert updates.available_update("1.13.0", [data], machine="arm64") is None


def app_zip(root, *, version="1.14.0", identifier=updates.APP_BUNDLE_IDENTIFIER, members=()):
    archive = root / "update.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("Metriq Visualizer.app/Contents/Info.plist", plistlib.dumps(dict(
            CFBundleIdentifier=identifier, CFBundleShortVersionString=version,
            CFBundleExecutable="Metriq Visualizer")))
        executable = zipfile.ZipInfo("Metriq Visualizer.app/Contents/MacOS/Metriq Visualizer")
        executable.external_attr = (stat.S_IFREG | 0o755) << 16
        package.writestr(executable, b"pretend Mach-O bytes")
        for name, payload in members:
            package.writestr(name, payload)
    raw = archive.read_bytes()
    update = updates.UpdateInfo("1.14.0", "Metriq-Visualizer-macOS-arm64.zip", API_ASSET,
        hashlib.sha256(raw).hexdigest(), len(raw), "")
    return archive, update


@pytest.fixture
def redirect_transport(monkeypatch):
    """Run urllib's real redirect dispatch, replacing only HTTP transport."""
    def configure(targets, payload):
        requests = []
        def respond(handler, request):
            requests.append(request)
            headers = Message()
            index = len(requests) - 1
            status = 302 if index < len(targets) else 200
            if status == 302:
                headers["Location"] = targets[index]
            response = updates.urllib.request.addinfourl(
                io.BytesIO(b"" if status == 302 else payload), headers, request.full_url, status)
            response.msg = "Found" if status == 302 else "OK"
            return response
        monkeypatch.setattr(updates.urllib.request.OpenerDirector, "open", _REAL_OPENER_OPEN)
        monkeypatch.setattr(updates.urllib.request.HTTPSHandler, "https_open", respond)
        monkeypatch.setattr(updates.urllib.request.HTTPHandler, "http_open",
                            lambda *a: pytest.fail("Unsafe HTTP transport reached"))
        return requests
    return configure


# Rejecting legitimate storage redirects makes every real GitHub update fail.
@pytest.mark.parametrize("targets", [
    ["https://objects.githubusercontent.com/file?token=signed"],
    ["https://github.com/MetriqOrg/Metriq-Visualizer/releases/download/v1.14.0/app.zip",
     "https://api.github.com/redirect", "https://release-assets.githubusercontent.com/file"],
    ["https://github-releases.githubusercontent.com/file"],
    ["https://objects.githubusercontent.com:443/file"],
])
def test_allowed_download_redirect_chain(tmp_path, redirect_transport, targets):
    archive, update = app_zip(tmp_path)
    requests = redirect_transport(targets, archive.read_bytes())
    destination = tmp_path / "download.zip"
    assert updates.download_verified_asset(update, destination).read_bytes() == archive.read_bytes()
    assert [request.full_url for request in requests] == [API_ASSET, *targets]


# Each forbidden target must fail before a second transport call.
@pytest.mark.parametrize("target", [
    "http://objects.githubusercontent.com/file", "https://evil.test/file",
    "https://objects.githubusercontent.com.evil.test/file", "https://127.0.0.1/file",
    "https://[::1]/file", "https://objects.githubusercontent.com:444/file",
    "https://user:secret@objects.githubusercontent.com/file",
    "https://user@objects.githubusercontent.com/file",
])
def test_unsafe_download_redirect_is_refused(tmp_path, redirect_transport, target):
    archive, update = app_zip(tmp_path)
    requests = redirect_transport([target], archive.read_bytes())
    with pytest.raises(ValueError):
        updates.download_verified_asset(update, tmp_path / "download.zip")
    assert len(requests) == 1
    assert not (tmp_path / "download.zip").exists()
    assert not list(tmp_path.glob(".download-*.part"))


@pytest.mark.parametrize("targets", [
    [f"https://objects.githubusercontent.com/file{i}" for i in range(4)],
    ["https://objects.githubusercontent.com/file"] * 4,
])
def test_download_redirect_hop_limit(tmp_path, redirect_transport, targets):
    archive, update = app_zip(tmp_path)
    requests = redirect_transport(targets, archive.read_bytes())
    with pytest.raises(ValueError, match="redirect"):
        updates.download_verified_asset(update, tmp_path / "download.zip")
    assert len(requests) == 4  # Initial request plus three permitted hops.


def test_download_redirect_never_forwards_credentials(redirect_transport):
    targets = [API_ASSET, "https://release-assets.githubusercontent.com/file"]
    requests = redirect_transport(targets, b"verified bytes")
    request = updates._request(API_ASSET, "application/octet-stream")
    request.add_header("Authorization", "Bearer secret")
    request.add_header("Cookie", "session=secret")
    request.add_header("Proxy-Authorization", "Basic secret")
    request.add_unredirected_header("X-Private-Credential", "secret")
    with updates._urlopen(request, timeout=30) as response:
        assert response.read() == b"verified bytes"
    assert len(requests) == 3
    for redirected in requests[1:]:
        assert {key.lower() for key, value in redirected.header_items()} == {
            "accept", "user-agent", "x-github-api-version", "host"}
        assert all("secret" not in value for key, value in redirected.header_items())


def test_digest_mismatch_after_redirect_preserves_destination(tmp_path, redirect_transport):
    archive, update = app_zip(tmp_path)
    requests = redirect_transport(["https://objects.githubusercontent.com/file"], b"x" * update.size)
    destination = tmp_path / "download.zip"
    destination.write_bytes(b"existing")
    with pytest.raises(ValueError, match="SHA-256"):
        updates.download_verified_asset(update, destination)
    assert len(requests) == 2
    assert destination.read_bytes() == b"existing"
    assert not list(tmp_path.glob(".download-*.part"))


def test_metadata_redirect_remains_refused(redirect_transport):
    requests = redirect_transport(["https://api.github.com/other"], b"[]")
    with pytest.raises(ValueError, match="redirect"):
        updates.fetch_releases()
    assert len(requests) == 1


def test_digest_mismatch_preserves_destination(tmp_path):
    _, update = app_zip(tmp_path)
    destination = tmp_path / "download.zip"
    destination.write_bytes(b"existing")
    with pytest.raises(ValueError, match="digest|SHA-256"):
        updates.download_verified_asset(update, destination, urlopen=lambda *a, **k: io.BytesIO(b"tampered"))
    assert destination.read_bytes() == b"existing"
    assert not destination.with_suffix(".zip.part").exists()


@pytest.mark.parametrize("path", ["../escape", "/absolute", "Metriq Visualizer.app/../../escape",
    "Metriq Visualizer.app/Contents/../escape", "C:/escape", "..\\escape"])
def test_malicious_zip_is_refused_before_writing(tmp_path, path):
    archive, update = app_zip(tmp_path, members=[(path, b"bad")])
    with pytest.raises(ValueError):
        updates.extract_verified_app(archive, tmp_path / "stage", update, current_version="1.13.0")
    assert not (tmp_path / "escape").exists()
    assert not (tmp_path / "stage").exists()


def test_offline_propagates_without_modifying_app(tmp_path):
    def offline(*a, **k):
        raise OSError("offline")
    with pytest.raises(OSError, match="offline"):
        updates.check_for_update("1.13.0", urlopen=offline)
    assert list(tmp_path.iterdir()) == []


def test_extraction_rechecks_digest_before_any_writes(tmp_path):
    archive, update = app_zip(tmp_path)
    archive.write_bytes(b"x" * update.size)
    with pytest.raises(ValueError, match="digest"):
        updates.extract_verified_app(archive, tmp_path / "stage", update, current_version="1.13.0")
    assert not (tmp_path / "stage").exists()


@pytest.mark.parametrize("version,identifier,current", [("1.12.8", updates.APP_BUNDLE_IDENTIFIER, "1.13.0"),
    ("1.14.0", "org.attacker.app", "1.13.0"), ("1.14.0", updates.APP_BUNDLE_IDENTIFIER, "1.14.0")])
def test_bundle_identity_version_and_downgrade_fail_closed(tmp_path, version, identifier, current):
    archive, update = app_zip(tmp_path, version=version, identifier=identifier)
    with pytest.raises(ValueError):
        updates.extract_verified_app(archive, tmp_path / "stage", update, current_version=current)
    assert not (tmp_path / "stage").exists()


def link(name):
    info = zipfile.ZipInfo(name)
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    return info


@pytest.mark.parametrize("target", ["/tmp", "../../../outside", "loop", "missing"])
def test_symlink_escape_cycles_and_broken_links_fail_closed(tmp_path, target):
    archive, update = app_zip(tmp_path, members=[(link("Metriq Visualizer.app/Contents/loop"), target)])
    with pytest.raises(ValueError):
        updates.extract_verified_app(archive, tmp_path / "stage", update, current_version="1.13.0")
    assert not (tmp_path / "stage").exists()


def test_safe_framework_symlink_and_executable_permissions_are_preserved(tmp_path):
    archive, update = app_zip(tmp_path, members=[("Metriq Visualizer.app/Contents/Frameworks/Versions/A/library", b"data"),
        (link("Metriq Visualizer.app/Contents/Frameworks/Versions/Current"), "A")])
    bundle = updates.extract_verified_app(archive, tmp_path / "stage", update, current_version="1.13.0")
    assert (bundle / "Contents/Frameworks/Versions/Current/library").read_bytes() == b"data"
    assert (bundle / "Contents/MacOS/Metriq Visualizer").stat().st_mode & 0o111


@pytest.mark.parametrize("limit,value", [("MAX_FILES", 1), ("MAX_EXPANDED_BYTES", 1)])
def test_zip_bomb_limits_before_extraction(tmp_path, monkeypatch, limit, value):
    archive, update = app_zip(tmp_path)
    monkeypatch.setattr(updates, limit, value)
    with pytest.raises(ValueError, match="limit"):
        updates.extract_verified_app(archive, tmp_path / "stage", update, current_version="1.13.0")
    assert not (tmp_path / "stage").exists()


def test_excessive_compression_ratio_is_refused(tmp_path):
    archive, update = app_zip(tmp_path)
    with zipfile.ZipFile(archive, "a", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("Metriq Visualizer.app/Contents/bomb", b"0" * (2 * 1024 * 1024))
    update = replace(update, sha256=hashlib.sha256(archive.read_bytes()).hexdigest(), size=archive.stat().st_size)
    with pytest.raises(ValueError, match="bomb"):
        updates.extract_verified_app(archive, tmp_path / "stage", update, current_version="1.13.0")


def test_metadata_and_download_are_bounded(tmp_path):
    with pytest.raises(ValueError, match="size limit"):
        updates.fetch_releases(urlopen=lambda *a, **k: io.BytesIO(b"x" * (updates.MAX_METADATA_BYTES + 1)))
    _, update = app_zip(tmp_path)
    with pytest.raises(ValueError, match="size limit"):
        updates.download_verified_asset(update, tmp_path / "download", urlopen=lambda *a, **k: io.BytesIO(b"x" * (update.size + 1)))
    assert not (tmp_path / "download").exists()
    assert list(tmp_path.glob("*.part")) == []


def test_api_request_contains_no_credentials_or_user_data():
    requests = []
    def respond(request, **kwargs):
        requests.append((request, kwargs))
        return io.BytesIO(b"[]")
    assert updates.fetch_releases(urlopen=respond) == []
    request, arguments = requests[0]
    assert request.full_url == updates.GITHUB_RELEASES_URL
    assert request.data is None
    assert set(key.lower() for key in request.headers) == {"accept", "user-agent", "x-github-api-version"}
    assert arguments["timeout"] == 8


def test_redirect_refused_without_second_network_request():
    request = updates.urllib.request.Request(updates.GITHUB_RELEASES_URL)
    with pytest.raises(ValueError, match="redirect"):
        updates._NoRedirect().redirect_request(request, None, 302, "Found", {}, "https://evil.test/payload")


def test_user_declines_before_any_staging_or_network(tmp_path, monkeypatch):
    _, update = app_zip(tmp_path)
    monkeypatch.setattr(updates.tempfile, "mkdtemp", lambda **k: pytest.fail("Staging without consent"))
    with pytest.raises(ValueError, match="confirmation"):
        updates.prepare_update_install(update, tmp_path / "app", current_version="1.13.0")


def test_startup_is_throttled_even_when_offline_and_can_be_disabled(tmp_path):
    from PySide6.QtCore import QSettings
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    assert updates.claim_startup_check(settings, now=100000)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    assert not updates.claim_startup_check(settings, now=100001)
    assert not updates.claim_startup_check(settings, now=99999)
    assert updates.claim_startup_check(settings, now=186400)
    settings.setValue("updates/enabled", False)
    assert not updates.claim_startup_check(settings, now=400000)


def test_startup_claim_is_exclusive_across_instances(tmp_path):
    from PySide6.QtCore import QLockFile, QSettings
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    lock = QLockFile(settings.fileName() + ".updates.lock")
    assert lock.tryLock(0)
    try:
        assert not updates.claim_startup_check(settings, now=100000)
    finally:
        lock.unlock()
    assert updates.claim_startup_check(settings, now=100000)


def test_signature_and_actual_executable_architecture_are_checked(tmp_path, monkeypatch):
    archive, update = app_zip(tmp_path)
    bundle = updates.extract_verified_app(archive, tmp_path / "stage", update, current_version="1.13.0")
    monkeypatch.setattr(updates.platform, "machine", lambda: "arm64")
    with pytest.raises(ValueError, match="verification failed"):
        updates.verify_macos_bundle(bundle, runner=lambda *a, **k: SimpleNamespace(returncode=1, stdout=""))
    with pytest.raises(ValueError, match="architecture"):
        updates.verify_macos_bundle(bundle, runner=lambda *a, **k: SimpleNamespace(returncode=0, stdout="x86_64"))
    updates.verify_macos_bundle(bundle, runner=lambda *a, **k: SimpleNamespace(returncode=0, stdout="arm64 x86_64"))


def installed(root, version="1.13.0"):
    target = root / "Applications" / "Metriq Visualizer.app"
    (target / "Contents").mkdir(parents=True)
    (target / "Contents/Info.plist").write_bytes(plistlib.dumps(dict(
        CFBundleIdentifier=updates.APP_BUNDLE_IDENTIFIER, CFBundleShortVersionString=version)))
    (target / "old").write_text("old app")
    return target


@pytest.fixture
def install_setup(tmp_path, monkeypatch):
    target = installed(tmp_path)
    archive, update = app_zip(tmp_path)
    bundle = updates.extract_verified_app(archive, tmp_path / "stage", update, current_version="1.13.0")
    monkeypatch.setattr(updates, "verify_macos_bundle", lambda *a, **k: None)
    return target, bundle


@pytest.mark.skipif(sys.platform != "darwin", reason="exercises the macOS .app bundle exchange")
def test_helper_waits_for_exit_and_keeps_rollback_copy(install_setup, tmp_path, monkeypatch):
    import concurrent.futures
    import threading
    target, bundle = install_setup
    parent = subprocess.Popen(["/bin/sleep", "30"])
    manifest = updates.schedule_macos_install(bundle, target, current_version="1.13.0",
        process_id=parent.pid, launcher=lambda *a, **k: None)
    waiting = threading.Event()
    wait = updates._wait_for_exit
    def observe_wait(pid):
        waiting.set()
        wait(pid)
    monkeypatch.setattr(updates, "_wait_for_exit", observe_wait)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        helper = pool.submit(updates.run_deferred_install, manifest)
        try:
            assert waiting.wait(timeout=2)
            assert not helper.done()
            assert (target / "old").read_text() == "old app"
            parent.terminate()
            parent.wait(timeout=5)
            helper.result(timeout=5)
            assert not (target / "old").exists()
            assert (manifest.parent / target.name / "old").read_text() == "old app"
        finally:
            if parent.poll() is None:
                parent.terminate()
                parent.wait(timeout=5)


def test_atomic_exchange_failure_leaves_original_app_untouched(install_setup, tmp_path, monkeypatch):
    target, bundle = install_setup
    manifest = updates.schedule_macos_install(bundle, target, current_version="1.13.0",
        process_id=99999999, launcher=lambda *a, **k: None)
    def fail(*args):
        raise OSError("exchange failed")
    monkeypatch.setattr(updates, "_atomic_exchange", fail)
    with pytest.raises(OSError, match="exchange failed"):
        updates.run_deferred_install(manifest)
    assert (target / "old").read_text() == "old app"
    assert (manifest.parent / target.name / "Contents/MacOS/Metriq Visualizer").is_file()


def test_staged_or_installed_tampering_while_waiting_is_refused(install_setup, monkeypatch):
    target, bundle = install_setup
    manifest = updates.schedule_macos_install(bundle, target, current_version="1.13.0",
        process_id=99999999, launcher=lambda *a, **k: None)
    (manifest.parent / target.name / "Contents/MacOS/Metriq Visualizer").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="changed while waiting"):
        updates.run_deferred_install(manifest)
    assert (target / "old").read_text() == "old app"


def test_launcher_failure_and_changed_installed_version_leave_app_untouched(install_setup, tmp_path):
    target, bundle = install_setup
    def fail(*a, **k):
        raise OSError("launch failed")
    with pytest.raises(OSError, match="launch failed"):
        updates.schedule_macos_install(bundle, target, current_version="1.13.0", launcher=fail)
    assert (target / "old").read_text() == "old app"
    assert list(target.parent.glob(".metriq-update-*")) == []
    with pytest.raises(ValueError, match="running version"):
        updates.schedule_macos_install(bundle, target, current_version="1.12.0", launcher=fail)
    assert (target / "old").read_text() == "old app"


@pytest.mark.skipif(sys.platform != "darwin", reason="exercises the macOS .app bundle exchange")
def test_abrupt_helper_exit_after_atomic_exchange_keeps_both_apps(install_setup):
    import sys
    target, bundle = install_setup
    manifest = updates.schedule_macos_install(bundle, target, current_version="1.13.0",
        process_id=99999999, launcher=lambda *a, **k: None)
    candidate = manifest.parent / target.name
    result = subprocess.run([sys.executable, "-c",
        "import os, sys; from pathlib import Path; from metriq_visualizer_updates import _atomic_exchange; "
        "_atomic_exchange(Path(sys.argv[1]), Path(sys.argv[2])); os._exit(137)", str(candidate), str(target)], timeout=5)
    assert result.returncode == 137
    assert (target / "Contents/MacOS/Metriq Visualizer").is_file()
    assert (candidate / "old").read_text() == "old app"


def test_case_insensitive_symlink_ancestor_is_refused_in_preflight(tmp_path):
    archive, update = app_zip(tmp_path, members=[
        (link("Metriq Visualizer.app/Contents/alias"), "MacOS"),
        ("Metriq Visualizer.app/Contents/Alias/injected", b"payload")])
    with pytest.raises(ValueError, match="symbolic link"):
        updates.extract_verified_app(archive, tmp_path / "stage", update, current_version="1.13.0")
    assert not (tmp_path / "stage").exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="exercises the macOS .app bundle exchange")
def test_prepare_verifies_entire_pipeline_before_scheduling(tmp_path, monkeypatch):
    archive, update = app_zip(tmp_path)
    target = installed(tmp_path)
    requests, launches = [], []
    def respond(request, **kwargs):
        requests.append(request)
        return io.BytesIO(archive.read_bytes())
    monkeypatch.setattr(updates, "_urlopen", respond)
    monkeypatch.setattr(updates.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="arm64"))
    monkeypatch.setattr(updates.subprocess, "Popen", lambda *a, **k: launches.append((a, k)))
    manifest = updates.prepare_update_install(update, target, current_version="1.13.0", confirmed=True)
    assert (target / "old").read_text() == "old app"
    assert manifest.is_file()
    assert len(requests) == len(launches) == 1
    assert requests[0].full_url == API_ASSET
    assert launches[0][1]["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert launches[0][0][0][-2:] == ["--metriq-install-helper", str(manifest)]
    assert str(manifest.parent / target.name) not in launches[0][0][0]


@pytest.mark.skipif(sys.platform != "darwin", reason="exercises the macOS .app bundle exchange")
def test_bad_download_cannot_schedule_or_change_installed_app(tmp_path, monkeypatch):
    _, update = app_zip(tmp_path)
    target = installed(tmp_path)
    monkeypatch.setattr(updates, "_urlopen", lambda *a, **k: io.BytesIO(b"tampered"))
    monkeypatch.setattr(updates, "schedule_macos_install", lambda *a, **k: pytest.fail("Scheduled unverified data"))
    with pytest.raises(ValueError, match="digest"):
        updates.prepare_update_install(update, target, current_version="1.13.0", confirmed=True)
    assert (target / "old").read_text() == "old app"


def test_helper_entry_runs_without_initializing_ui(tmp_path):
    import sys
    manifest = tmp_path / "install.json"
    manifest.write_text("{}")
    result = subprocess.run([sys.executable, str(Path(updates.__file__).with_name("metriq_visualizer_app.py")),
        "--metriq-install-helper", str(manifest)], capture_output=True, timeout=5)
    assert result.returncode == 1
    assert (tmp_path / "failure.txt").is_file()
    assert result.stderr == b""
