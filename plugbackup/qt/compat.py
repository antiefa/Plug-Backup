"""Единая точка импорта и подготовки Qt 5/6."""
from __future__ import annotations

import os
import sys
from pathlib import Path

QT_API = ""

if os.environ.get("PLUGBACKUP_QT") == "6":
    try:
        from PyQt6 import QtCore, QtGui, QtWidgets
        QT_API = "PyQt6"
    except ImportError:
        from PyQt5 import QtCore, QtGui, QtWidgets
        QT_API = "PyQt5"
else:
    try:
        from PyQt5 import QtCore, QtGui, QtWidgets
        QT_API = "PyQt5"
    except ImportError:
        from PyQt6 import QtCore, QtGui, QtWidgets
        QT_API = "PyQt6"

Signal = QtCore.pyqtSignal if hasattr(QtCore, "pyqtSignal") else QtCore.Signal
Slot = QtCore.pyqtSlot if hasattr(QtCore, "pyqtSlot") else QtCore.Slot
QAction = getattr(QtWidgets, "QAction", None) or QtGui.QAction


def exec_dialog(dialog) -> int:
    """Вызвать модальный цикл в Qt 5 и Qt 6."""
    method = getattr(dialog, "exec", None) or dialog.exec_
    return method()


def _plugin_candidates() -> list[Path]:
    candidates = []
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidates.extend((base / "PyQt5" / "Qt5" / "plugins", base / "PyQt6" / "Qt6" / "plugins"))
    module = Path(QtCore.__file__).resolve()
    package = module.parent
    candidates.extend((package / "Qt5" / "plugins", package / "Qt6" / "plugins", package / "plugins"))
    return candidates


def prepare_environment() -> None:
    """Подготовить плагины и HiDPI до создания QApplication."""
    for plugins in _plugin_candidates():
        platforms = plugins / "platforms"
        if platforms.is_dir():
            QtCore.QCoreApplication.addLibraryPath(str(plugins))
            os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", str(platforms))
            break
    if QT_API == "PyQt5":
        for name in ("AA_EnableHighDpiScaling", "AA_UseHighDpiPixmaps"):
            attribute = getattr(QtCore.Qt.ApplicationAttribute, name, None)
            if attribute is None:
                attribute = getattr(QtCore.Qt, name, None)
            if attribute is not None:
                QtWidgets.QApplication.setAttribute(attribute, True)

