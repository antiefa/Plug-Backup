"""Макет диалога задания с теми же полями и пояснениями, что в GTK."""
from __future__ import annotations

import copy
from pathlib import Path

from plugbackup.core import profiles as P
from plugbackup.i18n import _
from plugbackup.qt.compat import QtCore, QtGui, QtWidgets
from plugbackup.qt.message_row import MessageRow
from plugbackup.qt.style import load_stylesheet

ICON_DIR = Path(__file__).resolve().parent / "icons"


def _warning_pixmap(size: int) -> QtGui.QPixmap:
    pixmap = QtGui.QIcon(str(ICON_DIR / "warning.svg")).pixmap(QtCore.QSize(size, size))
    painter = QtGui.QPainter(pixmap)
    mode = getattr(QtGui.QPainter, "CompositionMode_SourceIn", None)
    if mode is None:
        mode = QtGui.QPainter.CompositionMode.CompositionMode_SourceIn
    painter.setCompositionMode(mode)
    painter.fillRect(pixmap.rect(), QtGui.QColor("#d97706"))
    painter.end()
    return pixmap


class _ExclusionsEdit(QtWidgets.QPlainTextEdit):
    """Поле исключений с компактной начальной высотой, которое можно растянуть."""

    def sizeHint(self):
        hint = super().sizeHint()
        return QtCore.QSize(hint.width(), 82)


class JobDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, profile: P.Profile | None = None, job: P.Job | None = None,
                 theme: str | None = None):
        super().__init__(parent)
        parent_theme = getattr(parent, "property", lambda _key: "light")("theme")
        self.setProperty("theme", theme or parent_theme or "light")
        self.setStyleSheet(load_stylesheet())
        self.profile = profile or P.Profile(name="Plug & Backup")
        self.job = copy.deepcopy(job) if job else P.Job(
            source=r"C:\Users\user\Documents", dest="Documents", mode="mirror_versions")
        self.setWindowTitle(_("Edit job") if job else _("New job"))
        self.resize(880, 680)
        self.setMinimumSize(760, 620)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(6)
        body = QtWidgets.QWidget()
        body_layout = QtWidgets.QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(9)
        self.form = QtWidgets.QFormLayout()
        self.form.setHorizontalSpacing(12)
        self.form.setVerticalSpacing(7)
        form = self.form
        form.setRowWrapPolicy(QtWidgets.QFormLayout.RowWrapPolicy.WrapLongRows)

        source_row = QtWidgets.QWidget()
        source_layout = QtWidgets.QHBoxLayout(source_row)
        source_layout.setContentsMargins(0, 0, 0, 0)
        self.source = QtWidgets.QLineEdit(self.job.source)
        self.source.setPlaceholderText(_("Folder to back up"))
        browse = QtWidgets.QPushButton("…")
        browse.setFixedWidth(38)
        browse.clicked.connect(self._browse)
        source_layout.addWidget(self.source, 1)
        source_layout.addWidget(browse)
        form.addRow(_("Folder on this computer"), source_row)
        self.dest = QtWidgets.QLineEdit(self.job.dest)
        self.dest.setPlaceholderText(_("Folder name inside the backup folder"))
        form.addRow(_("Folder on the drive"), self.dest)
        destination_hint = QtWidgets.QLabel(_("Inside “%s” on the drive.") % self.profile.root)
        destination_hint.setObjectName("muted")
        destination_hint.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter)
        form.addRow("", destination_hint)

        self.mode = QtWidgets.QComboBox()
        self.mode.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.mode.addItem(_("Mirror with version history"), "mirror_versions")
        self.mode.addItem(_("Mirror"), "mirror")
        self.mode.addItem(_("Accumulate"), "accumulate")
        self.mode.setCurrentIndex(max(0, self.mode.findData(self.job.mode)))
        self.mode.currentIndexChanged.connect(self._update_hints)
        form.addRow(_("Mode"), self.mode)
        self.warning_icon = QtWidgets.QLabel()
        self.warning_icon.setObjectName("warningIcon")
        self.warning_icon.setPixmap(_warning_pixmap(18))
        self.warning_icon.setFixedSize(20, 20)
        self.warning_icon.setVisible(False)
        self.mode_hint = QtWidgets.QLabel()
        self.mode_hint.setWordWrap(True)
        self.mode_hint.setObjectName("muted")
        hint_policy = self.mode_hint.sizePolicy()
        hint_policy.setHeightForWidth(True)
        self.mode_hint.setSizePolicy(hint_policy)
        self.mode_hint_row = MessageRow(self.warning_icon, self.mode_hint)
        form.addRow(self.mode_hint_row)

        self.compare = QtWidgets.QComboBox()
        self.compare.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.compare.addItem(_("By size and modification time (fast)"), "size_mtime")
        self.compare.addItem(_("By content (slow, reads every file)"), "checksum")
        self.compare.setCurrentIndex(max(0, self.compare.findData(self.job.compare)))
        self.compare.currentIndexChanged.connect(self._update_hints)
        form.addRow(_("Detect changes"), self.compare)

        self.symlinks = QtWidgets.QComboBox()
        self.symlinks.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToContents)
        symlink_auto = _("Automatic: keep as links on ext4, skip on other file systems")
        self.symlinks.addItem(symlink_auto, "auto")
        self.symlinks.setItemData(0, symlink_auto, QtCore.Qt.ItemDataRole.ToolTipRole)
        self.symlinks.setToolTip(symlink_auto)
        self.symlinks.addItem(_("Skip links"), "skip")
        self.symlinks.addItem(_("Copy what the link points to"), "copy_target")
        self.symlinks.setCurrentIndex(max(0, self.symlinks.findData(self.job.symlinks)))
        self.symlinks.currentIndexChanged.connect(self._update_hints)
        form.addRow(_("Symbolic links"), self.symlinks)

        self.max_size = QtWidgets.QSpinBox()
        self.max_size.setRange(0, 10_000_000)
        self.max_size.setSuffix(" MB")
        self.max_size.setValue(self.job.max_size_mb or 0)
        self.max_size.setMinimumWidth(180)
        self.max_size.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.max_size.setSpecialValueText(_("0 — no limit"))
        self.max_size.setToolTip(_("0 — no limit"))
        form.addRow(_("Skip files larger than (MB)"), self.max_size)
        body_layout.addLayout(form)

        checks = QtWidgets.QVBoxLayout()
        checks.setSpacing(1)
        self.enabled = QtWidgets.QCheckBox(_("Job is enabled"))
        self.enabled.setChecked(self.job.enabled)
        self.in_auto = QtWidgets.QCheckBox(_("Include in automatic backup"))
        self.in_auto.setChecked(self.job.in_auto)
        self.skip_hidden = QtWidgets.QCheckBox(_("Skip hidden files and folders"))
        self.skip_hidden.setChecked(self.job.skip_hidden)
        self.one_fs = QtWidgets.QCheckBox(_("Do not enter other disks mounted inside the folder"))
        self.one_fs.setChecked(self.job.one_file_system)
        for control in (self.enabled, self.in_auto, self.skip_hidden, self.one_fs):
            checks.addWidget(control)
        body_layout.addLayout(checks)
        body_layout.addWidget(self._section(_("Exclusions")))
        self.use_global = QtWidgets.QCheckBox(_("Also apply the common exclusion list"))
        self.use_global.setChecked(self.job.use_global_excludes)
        body_layout.addWidget(self.use_global)
        self.excludes = _ExclusionsEdit("\n".join(self.job.excludes))
        self.excludes.setPlaceholderText("*.tmp\nnode_modules/\n/Archive/")
        self.excludes.setMinimumHeight(78)
        self.excludes.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        body_layout.addWidget(self.excludes, 1)
        hint = QtWidgets.QLabel(_("One pattern per line, rsync syntax: *.tmp — by name at any depth; "
                                  "node_modules/ — a folder at any depth; /Archive/ — only at the top of the job."))
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        body_layout.addWidget(hint)
        dev = QtWidgets.QPushButton(_("Add the Development set"))
        dev.clicked.connect(self._add_development)
        body_layout.addWidget(dev, 0, QtCore.Qt.AlignmentFlag.AlignLeft)

        root.addWidget(body, 1)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        cancel = QtWidgets.QPushButton(_("Cancel"))
        save = QtWidgets.QPushButton(_("Save"))
        save.setObjectName("primaryButton")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        root.addLayout(buttons)
        self._update_hints()

    @staticmethod
    def _section(text: str):
        label = QtWidgets.QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    def _browse(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, _("Folder to back up"), self.source.text())
        if path:
            self.source.setText(path)

    def _update_hints(self):
        mode = self.mode.currentData()
        hints = {
            "mirror_versions": _("The copy matches the folder; deleted and replaced files are moved to the version "
                                 "history on the drive and kept for the set number of days."),
            "mirror": _("The copy matches the folder exactly: files deleted on this computer are permanently "
                        "deleted from the copy."),
            "accumulate": _("New and changed files are copied; nothing is ever deleted from the copy."),
        }
        self.mode_hint.setText(hints[mode])
        self.warning_icon.setVisible(mode == "mirror")
        self.mode_hint.setObjectName("warningHint" if mode == "mirror" else "muted")
        self.mode_hint.style().unpolish(self.mode_hint)
        self.mode_hint.style().polish(self.mode_hint)
        self.mode_hint_row.updateGeometry()
        self.form.invalidate()
        self.layout().activate()
        minimum_height = self.layout().sizeHint().height()
        if minimum_height > self.height():
            self.resize(self.width(), minimum_height)

    def _add_development(self):
        lines = [line.strip() for line in self.excludes.toPlainText().splitlines() if line.strip()]
        for pattern in P.DEV_EXCLUDES:
            if pattern not in lines:
                lines.append(pattern)
        self.excludes.setPlainText("\n".join(lines))
