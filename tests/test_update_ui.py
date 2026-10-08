"""Exercise the small updater UI without initializing the media/GL main window."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMainWindow
import pytest

from metriq_visualizer_update_ui import UpdateController
from metriq_visualizer_updates import UpdateInfo


@pytest.fixture
def controller(tmp_path, monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", lambda *a, **k: pytest.fail("Real HTTP in UI test"))
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "updates.ini"), QSettings.Format.IniFormat)
    settings.setValue("updates/enabled", True)
    window = QMainWindow()
    control = UpdateController(window, "1.13.0", settings=settings)
    yield control
    control.dialog.close() if control.dialog else None
    window.close()
    app.processEvents()


def candidate():
    return UpdateInfo("1.14.0", "Metriq-Visualizer-macOS-arm64.zip",
        "https://api.github.com/repos/MetriqOrg/Metriq-Visualizer/releases/assets/42", "a" * 64, 123, "")


# Declining must never initiate the install worker or a download.
def test_user_declines_without_downloading(controller, monkeypatch):
    monkeypatch.setattr(controller, "_run", lambda *a, **k: pytest.fail("Unexpected worker"))
    controller._checked(candidate(), "", False)
    assert controller.dialog.isVisible()
    assert not controller.dialog.isModal()
    assert controller.dialog.install_button.isEnabled()
    controller.dialog.close()
    assert not controller.installing


def test_background_offline_is_silent(controller):
    controller._checked(None, "offline", False)
    assert controller.dialog is None


def test_disable_startup_setting_persists(controller):
    controller._checked(candidate(), "", False)
    controller.dialog.auto_check.setChecked(True)
    controller.dialog.auto_check.setChecked(False)
    controller.settings.sync()
    assert controller.settings.value("updates/enabled", True, type=bool) is False


def test_menu_is_one_action_and_manual_check_uses_same_dialog(controller, monkeypatch):
    starts = []
    monkeypatch.setattr(controller, "_run", lambda *args: starts.append(args))
    actions = controller.window.menuBar().actions()
    assert [a.text() for a in actions] == ["&Help"]
    assert [a.text() for a in actions[0].menu().actions()] == ["Check for Updates..."]
    controller.check_manual()
    assert controller.dialog.isVisible()
    assert len(starts) == 1


def test_confirmation_click_is_required_before_install(controller, monkeypatch, tmp_path):
    import metriq_visualizer_update_ui as ui
    monkeypatch.setattr(ui, "installed_app_bundle", lambda: tmp_path / "Metriq Visualizer.app")
    monkeypatch.setattr(ui.sys, "platform", "darwin")
    starts = []
    monkeypatch.setattr(controller, "_run", lambda *args: starts.append(args))
    controller._checked(candidate(), "", False)
    assert starts == []
    controller.dialog.install_button.click()
    assert controller.installing
    assert len(starts) == 1
    assert not controller.can_close()
    controller._installed(None, "verification failed")
    assert controller.can_close()
    assert "verification failed" in controller.dialog.message.text()


def test_startup_check_is_background_throttled_and_never_installs(controller, monkeypatch, tmp_path):
    import metriq_visualizer_update_ui as ui
    monkeypatch.setattr(ui, "installed_app_bundle", lambda: tmp_path / "Metriq Visualizer.app")
    monkeypatch.setattr(ui.sys, "platform", "darwin")
    starts = []
    monkeypatch.setattr(controller, "_run", lambda *args: starts.append(args))
    controller.window.show()
    controller.check_startup()
    assert len(starts) == 1
    assert controller.dialog is None
    controller._checked(candidate(), "", False)
    assert not controller.dialog.isModal()
    assert len(starts) == 1
    controller.check_startup()
    assert len(starts) == 1


def test_http_worker_does_not_block_gui(controller, monkeypatch):
    import threading
    import time
    from PySide6.QtCore import QTimer
    import metriq_visualizer_update_ui as ui
    started, release = threading.Event(), threading.Event()
    def slow_check(version):
        started.set()
        assert release.wait(timeout=5)
        return None
    monkeypatch.setattr(ui, "check_for_update", slow_check)
    try:
        controller.check_manual()
        assert started.wait(timeout=2)
        heartbeats = []
        QTimer.singleShot(0, lambda: heartbeats.append(True))
        QApplication.instance().processEvents()
        assert heartbeats == [True]
        assert controller.busy
    finally:
        release.set()
        deadline = time.monotonic() + 2
        while controller.busy and time.monotonic() < deadline:
            QApplication.instance().processEvents()
            time.sleep(0.01)
    assert not controller.busy


def test_update_notice_resizes_to_show_all_confirmation_text(controller):
    controller._checked(candidate(), "", False)
    QApplication.instance().processEvents()
    message = controller.dialog.message
    assert message.height() >= message.heightForWidth(message.width())
