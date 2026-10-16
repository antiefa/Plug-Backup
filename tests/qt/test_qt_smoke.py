"""Проверка сборки интерфейса и переводов Qt-макета."""
from __future__ import annotations

import ast
import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from plugbackup.qt.compat import QtCore, QtGui, QtWidgets, prepare_environment
except ImportError:
    QtCore = None
    QtGui = None
    QtWidgets = None
    prepare_environment = None


ROOT = Path(__file__).resolve().parents[2]
ALLOWED_UNTRANSLATED = {
    "The drive is encrypted (BitLocker) and locked. Unlock it in File Explorer.",
    "Create profile…",
    "File",
    "Settings…",
    "View",
}


def po_translations(path: Path) -> dict[str, str]:
    """Прочитать msgid/msgstr из PO стандартной библиотекой."""
    translations = {}
    for block in path.read_text(encoding="utf-8").split("\n\n"):
        msgid = None
        msgstr = None
        field = None
        for line in block.splitlines():
            if line.startswith("msgid "):
                field = "msgid"
                msgid = ast.literal_eval(line[6:])
            elif line.startswith("msgstr "):
                field = "msgstr"
                msgstr = ast.literal_eval(line[7:])
            elif line.startswith("msgstr["):
                field = None
            elif line.startswith('"') and field == "msgid":
                msgid += ast.literal_eval(line)
            elif line.startswith('"') and field == "msgstr":
                msgstr += ast.literal_eval(line)
            elif line.startswith(("msgctxt ", "msgid_plural ")):
                field = None
        if msgid is not None and msgstr:
            translations[msgid] = msgstr
    return translations


def qt_msgids() -> set[str]:
    result = set()
    for path in (ROOT / "plugbackup" / "qt").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_"
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                result.add(node.args[0].value)
    return result


class QtSmokeTest(unittest.TestCase):
    @unittest.skipIf(QtWidgets is None, "PyQt5/PyQt6 is not installed")
    def test_main_window_builds_with_primary_actions(self):
        prepare_environment()
        from plugbackup.qt.main_window import MainWindow

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        window = MainWindow()
        for button in (window.backup_button, window.preview_button, window.restore_button,
                       window.log_button, window.settings_button, window.add_job_button):
            self.assertIsInstance(button, QtWidgets.QPushButton)
        self.assertEqual(window.size().width(), 1180)
        self.assertEqual(len(window.menuBar().actions()), 3)
        sidebar = window.findChild(QtWidgets.QWidget, "sidebar")
        self.assertFalse(sidebar.findChildren(QtWidgets.QToolButton))
        window.close()
        app.quit()

    @unittest.skipIf(QtWidgets is None, "PyQt5/PyQt6 is not installed")
    def test_job_order_uses_real_drag_and_drop_events(self):
        prepare_environment()
        from plugbackup.qt.main_window import JOB_MIME_TYPE, MainWindow

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        window = MainWindow()
        window.show()
        app.processEvents()
        container = window.job_container
        self.assertFalse(window.findChildren(QtWidgets.QAbstractItemView))
        self.assertEqual(len(container.rows), len(window.profile.jobs))
        self.assertGreater(container.height(), 0)
        targets = (0, len(window.profile.jobs) // 2, len(window.profile.jobs), 1, 0,
                   len(window.profile.jobs) // 2, len(window.profile.jobs), 1, 0, len(window.profile.jobs))
        for attempt, target_index in enumerate(targets):
            source_index = (attempt * 2) % len(window.profile.jobs)
            source_job = window.profile.jobs[source_index]
            if target_index < len(container.rows):
                y = container.rows[target_index].geometry().top() + 1
            else:
                y = container.rows[-1].geometry().bottom()
            point = QtCore.QPoint(4, y)
            mime = QtCore.QMimeData()
            mime.setData(JOB_MIME_TYPE, str(id(source_job)).encode("ascii"))
            buttons = QtCore.Qt.MouseButton.LeftButton
            modifiers = QtCore.Qt.KeyboardModifier.NoModifier
            action = QtCore.Qt.DropAction.MoveAction
            enter = QtGui.QDragEnterEvent(point, action, mime, buttons, modifiers)
            container.dragEnterEvent(enter)
            self.assertTrue(enter.isAccepted())
            move = QtGui.QDragMoveEvent(point, action, mime, buttons, modifiers)
            container.dragMoveEvent(move)
            self.assertTrue(move.isAccepted())
            actual_target = container._drop_index
            expected = list(window.profile.jobs)
            destination = actual_target - 1 if actual_target > source_index else actual_target
            if destination != source_index:
                expected.insert(destination, expected.pop(source_index))
            drop = QtGui.QDropEvent(QtCore.QPointF(point), action, mime, buttons, modifiers)
            container.dropEvent(drop)
            self.assertTrue(drop.isAccepted())
            app.processEvents()
            self.assertEqual(window.profile.jobs, expected)
            self.assertEqual(len(container.rows), len(window.profile.jobs))
            self.assertEqual(container.rows_layout.count(), len(window.profile.jobs) + 1)
            for index, job in enumerate(window.profile.jobs):
                row = container.rows[index]
                label = row.findChild(QtWidgets.QLabel, "jobTitle")
                self.assertIsNotNone(label)
                self.assertEqual(label.text().split("  →  ")[0], job.source)

        original = list(window.profile.jobs)
        valid_mime = QtCore.QMimeData()
        valid_mime.setData(JOB_MIME_TYPE, str(id(original[0])).encode("ascii"))
        cancel_point = QtCore.QPoint(4, container.rows[0].geometry().top() + 1)
        cancel_enter = QtGui.QDragEnterEvent(cancel_point, QtCore.Qt.DropAction.MoveAction, valid_mime,
                                             QtCore.Qt.MouseButton.LeftButton,
                                             QtCore.Qt.KeyboardModifier.NoModifier)
        container.dragEnterEvent(cancel_enter)
        cancel_move = QtGui.QDragMoveEvent(cancel_point, QtCore.Qt.DropAction.MoveAction, valid_mime,
                                           QtCore.Qt.MouseButton.LeftButton,
                                           QtCore.Qt.KeyboardModifier.NoModifier)
        container.dragMoveEvent(cancel_move)
        self.assertTrue(container.drop_line.isVisible())
        container.dragLeaveEvent(QtGui.QDragLeaveEvent())
        self.assertEqual(window.profile.jobs, original)
        self.assertIsNone(container._drop_index)
        foreign = QtCore.QMimeData()
        foreign.setData("application/x-other-app-item", b"123")
        point = QtCore.QPoint(4, 1)
        action = QtCore.Qt.DropAction.MoveAction
        buttons = QtCore.Qt.MouseButton.LeftButton
        modifiers = QtCore.Qt.KeyboardModifier.NoModifier
        enter = QtGui.QDragEnterEvent(point, action, foreign, buttons, modifiers)
        container.dragEnterEvent(enter)
        self.assertFalse(enter.isAccepted())
        move = QtGui.QDragMoveEvent(point, action, foreign, buttons, modifiers)
        container.dragMoveEvent(move)
        self.assertFalse(move.isAccepted())
        drop = QtGui.QDropEvent(QtCore.QPointF(point), action, foreign, buttons, modifiers)
        container.dropEvent(drop)
        self.assertFalse(drop.isAccepted())
        self.assertEqual(window.profile.jobs, original)
        self.assertEqual(len(container.rows), len(window.profile.jobs))
        window.close()
        app.quit()

    @unittest.skipIf(QtWidgets is None, "PyQt5/PyQt6 is not installed")
    def test_message_row_wraps_and_menu_is_sidebar_action(self):
        prepare_environment()
        from plugbackup.qt.main_window import MainWindow

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        window = MainWindow()
        window.set_demo_state("locked")
        window.show()
        app.processEvents()
        self.assertTrue(window.message_row.hasHeightForWidth())
        two_line_height = window.message_row.heightForWidth(260)
        one_line_height = window.message_row.heightForWidth(820)
        self.assertGreaterEqual(two_line_height, one_line_height)
        self.assertEqual(len(window.menuBar().actions()), 3)
        self.assertEqual(len(window.file_menu.actions()), 6)
        self.assertEqual(len(window.view_menu.actions()), 2)
        self.assertEqual(len(window.help_menu.actions()), 4)
        window.close()
        app.quit()

    @unittest.skipIf(QtWidgets is None, "PyQt5/PyQt6 is not installed")
    def test_job_dialog_fits_without_outer_scroll_area(self):
        prepare_environment()
        from plugbackup.qt.job_dialog import JobDialog

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        dialog = JobDialog()
        self.assertIsNone(dialog.findChild(QtWidgets.QScrollArea))
        self.assertGreater(dialog.max_size.width(), 0)
        self.assertTrue(dialog.mode_hint_row.hasHeightForWidth())
        self.assertGreaterEqual(dialog.mode_hint_row.heightForWidth(320),
                                dialog.mode_hint_row.heightForWidth(820))
        self.assertIsNotNone(dialog.form.labelForField(dialog.symlinks))
        self.assertEqual(dialog.max_size.buttonSymbols(), QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
        for mode in ("mirror_versions", "mirror", "accumulate"):
            dialog.mode.setCurrentIndex(dialog.mode.findData(mode))
            app.processEvents()
            self.assertGreater(dialog.mode_hint_row.height(), 0)
        self.assertLessEqual(dialog.layout().sizeHint().height(), 768)
        dialog.close()
        app.quit()

    def test_qt_msgids_have_russian_translations(self):
        translations = po_translations(ROOT / "po" / "ru.po")
        missing = sorted(qt_msgids() - translations.keys() - ALLOWED_UNTRANSLATED)
        self.assertEqual(missing, [], "Qt msgid is missing a po/ru.po translation: %r" % missing)

    def test_size_format_matches_glib_decimal_units(self):
        from plugbackup.qt.format import format_size

        with patch("plugbackup.qt.format.language", return_value="ru"):
            self.assertEqual(format_size(21_600_000_000), "21,6 ГБ")
        with patch("plugbackup.qt.format.language", return_value="en"):
            self.assertEqual(format_size(21_600_000_000), "21.6 GB")


if __name__ == "__main__":
    unittest.main()
