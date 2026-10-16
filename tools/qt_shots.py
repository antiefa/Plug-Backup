#!/usr/bin/env python3
"""Создать набор согласовательных снимков Qt-интерфейса."""
from __future__ import annotations

import locale
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _child(language: str) -> None:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass
    from plugbackup.qt.compat import QtCore, QtGui, QtWidgets, prepare_environment

    prepare_environment()
    from plugbackup.qt.demo import sample_profile
    from plugbackup.qt.job_dialog import JobDialog
    from plugbackup.qt.main_window import MainWindow

    app = QtWidgets.QApplication([])
    app.setFont(QtGui.QFont("Ubuntu", 10))
    out = Path("temp/qt-shots")
    out.mkdir(parents=True, exist_ok=True)
    app.setStyle("Fusion")
    for theme in ("light", "dark"):
        if theme == "dark":
            palette = QtGui.QPalette()
            palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor(45, 45, 48))
            palette.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor(235, 235, 235))
            palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor(30, 30, 32))
            palette.setColor(QtGui.QPalette.ColorRole.AlternateBase, QtGui.QColor(53, 53, 56))
            palette.setColor(QtGui.QPalette.ColorRole.ToolTipBase, QtGui.QColor(250, 250, 250))
            palette.setColor(QtGui.QPalette.ColorRole.ToolTipText, QtGui.QColor(20, 20, 20))
            palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor(235, 235, 235))
            palette.setColor(QtGui.QPalette.ColorRole.Button, QtGui.QColor(57, 57, 61))
            palette.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor(235, 235, 235))
            palette.setColor(QtGui.QPalette.ColorRole.Highlight, QtGui.QColor(45, 140, 95))
            palette.setColor(QtGui.QPalette.ColorRole.HighlightedText, QtGui.QColor(255, 255, 255))
            app.setPalette(palette)
        else:
            app.setPalette(app.style().standardPalette())
        for state in ("connected", "disconnected", "running", "locked"):
            window = MainWindow(sample_profile())
            window.set_theme(theme)
            window.set_demo_state(state)
            window.show()
            app.processEvents()
            window.grab().save(str(out / ("main-%s-%s-%s.png" % (language, theme, state))))
            if state == "connected":
                window.move_job(0, len(window.profile.jobs) - 1)
                app.processEvents()
                window.grab().save(str(out / ("main-%s-%s-after-move.png" % (language, theme))))
            window.close()
        for mode in ("mirror_versions", "mirror", "accumulate"):
            dialog = JobDialog(None, sample_profile(), theme=theme)
            dialog.mode.setCurrentIndex(dialog.mode.findData(mode))
            dialog.show()
            app.processEvents()
            dialog.grab().save(str(out / ("job-%s-%s-%s.png" % (language, theme, mode))))
            dialog.close()
        window = MainWindow(sample_profile())
        window.set_theme(theme)
        window.show()
        app.processEvents()
        for menu_name, menu in (("file", window.file_menu), ("view", window.view_menu),
                                ("help", window.help_menu)):
            menu.popup(window.menuBar().mapToGlobal(QtCore.QPoint(0, window.menuBar().height())))
            app.processEvents()
            menu.grab().save(str(out / ("menu-%s-%s-%s.png" % (language, theme, menu_name))))
            menu.hide()
        drive_info = window._drive_details_menu()
        drive_info.popup(window.info_button.mapToGlobal(QtCore.QPoint(0, window.info_button.height())))
        app.processEvents()
        drive_info.grab().save(str(out / ("drive-info-%s-%s.png" % (language, theme))))
        drive_info.hide()
        settings = window._general_settings_dialog()
        settings.show()
        app.processEvents()
        settings.grab().save(str(out / ("settings-%s-%s.png" % (language, theme))))
        settings.close()
        window.close()
    app.quit()


def main() -> int:
    if "--child" in sys.argv:
        _child(sys.argv[sys.argv.index("--child") + 1])
        return 0
    script = str(Path(__file__).resolve())
    for language, locale in (("ru", "ru_RU.UTF-8"), ("en", "C.UTF-8")):
        environment = os.environ.copy()
        environment["LANGUAGE"] = language
        environment["LC_ALL"] = locale
        environment["QT_QPA_PLATFORM"] = "offscreen"
        catalog = ROOT / "build/deb/plugbackup_1.1.0~dev1_all/usr/share/locale"
        if catalog.is_dir():
            environment["PLUGBACKUP_LOCALEDIR"] = str(catalog)
        subprocess.run([sys.executable, script, "--child", language], check=True, env=environment)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
