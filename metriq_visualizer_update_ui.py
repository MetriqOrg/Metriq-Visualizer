# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
"""One Help action and one reusable, non-modal updater dialog."""
from __future__ import annotations

import sys
import threading
from functools import partial

from PySide6.QtCore import QObject, QSettings, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from metriq_visualizer_updates import (UpdateInfo, check_for_update, claim_startup_check,
                                       installed_app_bundle, prepare_update_install)


class UpdateDialog(QDialog):
    def __init__(self, controller):
        super().__init__(controller.window)
        self.setWindowTitle("Check for Updates")
        self.setModal(False)
        self.setFixedWidth(440)
        self.message = QLabel("Checking for updates…")
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        self.auto_check = QCheckBox("Check for updates automatically (once per day)")
        self.auto_check.setChecked(controller.settings.value("updates/enabled", True, type=bool))
        self.auto_check.toggled.connect(controller.set_automatic)
        self.install_button = QPushButton("Download, install and quit")
        self.install_button.setEnabled(False)
        self.install_button.setAutoDefault(False)
        self.install_button.clicked.connect(controller.confirm_install)
        self.close_button = QPushButton("Close")
        self.close_button.setDefault(True)
        self.close_button.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addWidget(self.install_button)
        buttons.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.message)
        layout.addWidget(self.auto_check)
        layout.addLayout(buttons)

    def set_message(self, text):
        self.message.setText(text)
        margins = self.layout().contentsMargins()
        self.message.setMinimumHeight(self.message.heightForWidth(self.width() - margins.left() - margins.right()))
        self.adjustSize()


class UpdateController(QObject):
    completed = Signal(str, object, str)

    def __init__(self, window, current_version, *, settings=None):
        super().__init__(window)
        self.window = window
        self.current_version = current_version
        self.settings = settings if settings is not None else QSettings("Metriq", "Visualizer")
        self.dialog = None
        self.update = None
        self.busy = False
        self.installing = False
        self.manual = False
        self.completed.connect(self._completed)
        help_menu = window.menuBar().addMenu("&Help")
        self.action = help_menu.addAction("Check for Updates...")
        self.action.triggered.connect(self.check_manual)
        QTimer.singleShot(4000, self.check_startup)

    def _show(self):
        if self.dialog is None:
            self.dialog = UpdateDialog(self)
        self.dialog.show()
        return self.dialog

    def set_automatic(self, enabled):
        self.settings.setValue("updates/enabled", enabled)
        self.settings.sync()

    def check_manual(self):
        self.manual = True
        dialog = self._show()
        dialog.raise_()
        if self.busy:
            return
        self._check()

    def check_startup(self):
        # Source launches do not phone home; only the macOS frozen app does.
        if (self.busy or not self.window.isVisible() or sys.platform != "darwin" or
                installed_app_bundle() is None or not claim_startup_check(self.settings)):
            return
        self.manual = False
        self._check()

    def _check(self):
        self.busy = True
        self.update = None
        if self.dialog:
            self.dialog.set_message("Checking for updates…")
            self.dialog.install_button.setEnabled(False)
        self._run("check", partial(check_for_update, self.current_version))

    def _run(self, kind, operation):
        def work():
            try:
                result, error = operation(), ""
            except Exception as exc:
                result, error = None, str(exc)[:1000]
            try:
                self.completed.emit(kind, result, error)
            except RuntimeError:
                pass  # The window was closed during a metadata-only request.
        threading.Thread(target=work, name="metriq-updater", daemon=True).start()

    @Slot(str, object, str)
    def _completed(self, kind, result, error):
        if kind == "check":
            self._checked(result, error, self.manual)
        else:
            self._installed(result, error)

    def _checked(self, update, error, manual):
        self.busy = False
        if not manual and (error or not isinstance(update, UpdateInfo)):
            return
        # Recheck opt-out in case it changed while the worker was running.
        if not manual and not self.settings.value("updates/enabled", True, type=bool):
            return
        dialog = self._show()
        self.update = update if isinstance(update, UpdateInfo) else None
        if error:
            dialog.set_message(f"Could not check for updates. Try again later.\n{error}")
        elif self.update:
            dialog.set_message(
                f"Version {update.version} is available (running {self.current_version}).\n"
                "Download and install this update? The app will quit after verification. "
                "The previous app will be kept in a rollback folder beside it.")
        else:
            dialog.set_message("No compatible verified update is available.")
        dialog.install_button.setEnabled(self.update is not None)

    def confirm_install(self):
        if self.busy or self.update is None:
            return
        bundle = installed_app_bundle()
        dialog = self._show()
        if sys.platform != "darwin" or bundle is None:
            dialog.set_message("Automatic installation requires a macOS application bundle.")
            dialog.install_button.setEnabled(False)
            return
        self.busy = self.installing = True
        dialog.install_button.setEnabled(False)
        dialog.set_message("Downloading and verifying the update. Please keep the app open…")
        self._run("install", partial(prepare_update_install, self.update, bundle,
                                      current_version=self.current_version, confirmed=True))

    def _installed(self, manifest, error):
        self.busy = self.installing = False
        if error:
            self._show().set_message(f"Update failed; the installed app is unchanged.\n{error}")
        else:
            self._show().set_message("Verified. Quitting to install; reopen the app when installation finishes.")
            self.window.close()

    def can_close(self):
        if self.installing:
            self._show()
            return False
        return True
