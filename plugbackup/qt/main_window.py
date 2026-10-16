"""Главное окно Qt, повторяющее структуру согласованного GTK-интерфейса."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from plugbackup.core.profiles import Profile
from plugbackup.i18n import _
from plugbackup.qt.compat import QtCore, QtGui, QtWidgets, exec_dialog
from plugbackup.qt.demo import sample_profile
from plugbackup.qt.format import format_size
from plugbackup.qt.job_dialog import JobDialog
from plugbackup.qt.message_row import MessageRow
from plugbackup.qt.style import load_stylesheet

ICON_DIR = Path(__file__).resolve().parent / "icons"


def _icon(name: str) -> QtGui.QIcon:
    return QtGui.QIcon(str(ICON_DIR / (name + ".svg")))


def _tinted_pixmap(name: str, size: int, color: str) -> QtGui.QPixmap:
    """Нарисовать одноцветный SVG в нужном размере и цвете."""
    pixmap = _icon(name).pixmap(QtCore.QSize(size, size))
    painter = QtGui.QPainter(pixmap)
    mode = getattr(QtGui.QPainter, "CompositionMode_SourceIn", None)
    if mode is None:
        mode = QtGui.QPainter.CompositionMode.CompositionMode_SourceIn
    painter.setCompositionMode(mode)
    painter.fillRect(pixmap.rect(), QtGui.QColor(color))
    painter.end()
    return pixmap


def _tinted_icon(name: str, color: str, size: int = 20) -> QtGui.QIcon:
    icon = QtGui.QIcon()
    icon.addPixmap(_tinted_pixmap(name, size, color))
    return icon


JOB_MIME_TYPE = "application/x-plugbackup-job-id"


class JobDragHandle(QtWidgets.QLabel):
    """Единственная область строки, откуда начинается перетаскивание."""

    def __init__(self, job_id: int, start_drag, parent=None):
        super().__init__(parent)
        self.job_id = job_id
        self._start_drag = start_drag
        self._press_position = None
        self._drag_started = False
        self.setObjectName("dragHandle")
        self.setCursor(QtCore.Qt.CursorShape.SizeAllCursor)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._press_position = event.pos()
            self._drag_started = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (self._press_position is not None and not self._drag_started and
                event.buttons() & QtCore.Qt.MouseButton.LeftButton and
                (event.pos() - self._press_position).manhattanLength() >=
                QtWidgets.QApplication.startDragDistance()):
            self._drag_started = True
            self._start_drag(self.job_id, self)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._press_position = None
        self._drag_started = False
        super().mouseReleaseEvent(event)


class JobRowsContainer(QtWidgets.QWidget):
    """Контейнер строк с перестановкой по mime-данным внутреннего drag-and-drop."""

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        self.setObjectName("jobRowsContainer")
        self.setAcceptDrops(True)
        self.rows_layout = QtWidgets.QVBoxLayout(self)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(0)
        self.rows = []
        self._drop_index = None
        self.drop_line = QtWidgets.QFrame(self)
        self.drop_line.setObjectName("jobDropLine")
        self.drop_line.setFixedHeight(2)
        self.drop_line.hide()

    @staticmethod
    def _event_point(event):
        position = getattr(event, "position", None)
        if position is not None:
            return position().toPoint()
        posf = getattr(event, "posF", None)
        if posf is not None:
            return posf().toPoint()
        return event.pos()

    def _valid_job_id(self, event):
        mime = event.mimeData()
        if not mime.hasFormat(JOB_MIME_TYPE):
            return None
        try:
            job_id = int(bytes(mime.data(JOB_MIME_TYPE)).decode("ascii"))
        except (TypeError, ValueError, UnicodeDecodeError):
            return None
        if not any(id(job) == job_id for job in self.window.profile.jobs):
            return None
        return job_id

    def _index_at(self, y: int) -> int:
        for index, row in enumerate(self.rows):
            if y < row.y() + row.height() // 2:
                return index
        return len(self.rows)

    def _show_drop_line(self, index: int) -> None:
        self._drop_index = index
        if index < len(self.rows):
            y = self.rows[index].y()
        elif self.rows:
            y = self.rows[-1].geometry().bottom() + 1
        else:
            y = self.rows_layout.contentsMargins().top()
        self.drop_line.setGeometry(0, y - 1, max(1, self.width()), 2)
        self.drop_line.show()
        self.drop_line.raise_()

    def _hide_drop_line(self) -> None:
        self._drop_index = None
        self.drop_line.hide()

    def dragEnterEvent(self, event):
        if self._valid_job_id(event) is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._valid_job_id(event) is None:
            event.ignore()
            self._hide_drop_line()
            return
        self._show_drop_line(self._index_at(self._event_point(event).y()))
        event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._hide_drop_line()
        event.accept()

    def dropEvent(self, event):
        job_id = self._valid_job_id(event)
        target = self._drop_index
        self._hide_drop_line()
        if job_id is None or target is None:
            event.ignore()
            return
        jobs = list(self.window.profile.jobs)
        source = next((index for index, job in enumerate(jobs) if id(job) == job_id), None)
        if source is None:
            event.ignore()
            return
        destination = target - 1 if target > source else target
        if destination != source:
            self.window._reorder_job(source, destination)
        event.acceptProposedAction()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, profile: Profile | None = None):
        super().__init__()
        self.profile = profile or sample_profile()
        self._active_drag_row = None
        self.state = "connected"
        self.setWindowTitle("Plug & Backup")
        self.resize(1180, 800)
        self.setObjectName("mainWindow")
        self.setProperty("theme", "light")
        self.setStyleSheet(load_stylesheet())
        self._build_menu_bar()
        self._build()

    def _build_menu_bar(self):
        menu_bar = self.menuBar()
        self.file_menu = menu_bar.addMenu(_("File"))
        create_profile = self.file_menu.addAction(_("Create profile…"))
        create_profile.triggered.connect(lambda: None)
        import_profile = self.file_menu.addAction(_("Import profile…"))
        import_profile.triggered.connect(lambda: None)
        self.file_menu.addSeparator()
        settings = self.file_menu.addAction(_("Settings…"))
        settings.triggered.connect(self._show_general_settings)
        self.file_menu.addSeparator()
        quit_action = self.file_menu.addAction(_("Quit"))
        quit_action.triggered.connect(self.close)

        self.view_menu = menu_bar.addMenu(_("View"))
        theme_menu = self.view_menu.addMenu(_("Theme"))
        self.theme_actions = {}
        for value, label in (("system", _("As in the system")), ("light", _("Light")),
                             ("dark", _("Dark"))):
            action = theme_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(value == "system")
            action.triggered.connect(lambda _checked=False, selected=value: self._select_theme(selected))
            self.theme_actions[value] = action
        show_disks = self.view_menu.addAction(_("Show all disks"))
        show_disks.setCheckable(True)
        show_disks.triggered.connect(lambda: None)

        self.help_menu = menu_bar.addMenu(_("Help"))
        for text in (_("Help"), _("System check")):
            self.help_menu.addAction(text, lambda: None)
        self.help_menu.addSeparator()
        self.help_menu.addAction(_("About"), lambda: None)

    def _select_theme(self, selected: str):
        for value, action in self.theme_actions.items():
            action.setChecked(value == selected)
        self.set_theme("light" if selected == "system" else selected)

    def _show_general_settings(self):
        exec_dialog(self._general_settings_dialog())

    def _general_settings_dialog(self):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(_("Settings…"))
        dialog.resize(540, 440)
        dialog.setProperty("theme", self.property("theme"))
        dialog.setStyleSheet(load_stylesheet())
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(QtWidgets.QLabel(_("Common exclusions…")))
        excludes = QtWidgets.QPlainTextEdit()
        excludes.setPlaceholderText("*.tmp\n.cache/")
        layout.addWidget(excludes, 1)
        layout.addWidget(QtWidgets.QLabel(_("Ignored drives…")))
        ignored = QtWidgets.QLineEdit()
        layout.addWidget(ignored)
        autostart = QtWidgets.QCheckBox(_("Start at login"))
        layout.addWidget(autostart)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Cancel |
                                             QtWidgets.QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        return dialog

    def _build(self):
        root = QtWidgets.QWidget()
        root.setObjectName("appRoot")
        shell = QtWidgets.QVBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        body = QtWidgets.QWidget()
        body_layout = QtWidgets.QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(self._build_sidebar())
        body_layout.addWidget(self._build_profile_page(), 1)
        shell.addWidget(body, 1)
        self.setCentralWidget(root)
        self.set_demo_state("connected")

    def _build_sidebar(self):
        sidebar = QtWidgets.QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(270)
        layout = QtWidgets.QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._section_row(_("Profiles"), "sidebarSection"))
        self.profile_rows = [self._profile_row(self.profile.name, _("drive connected"), "connected", True)]
        layout.addWidget(self.profile_rows[0])
        layout.addWidget(self._section_row(_("New drives"), "sidebarSection"))
        layout.addWidget(self._drive_row("KINGSTON", "exFAT · " + format_size(64_000_000_000)))
        self.create_profile_button = QtWidgets.QPushButton(_("Create profile"))
        self.create_profile_button.setObjectName("createProfile")
        self.create_profile_button.setIcon(_tinted_icon("plus", "#4b535a", 16))
        self.create_profile_button.clicked.connect(lambda: None)
        layout.addWidget(self.create_profile_button)
        layout.addStretch(1)
        return sidebar

    @staticmethod
    def _profile_row(name: str, status: str, state: str, selected: bool):
        row = QtWidgets.QWidget()
        row.setObjectName("profileRow")
        row.setProperty("state", state)
        row.setProperty("selected", selected)
        row.setFixedHeight(82)
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(12, 8, 8, 8)
        layout.setSpacing(10)
        icon = QtWidgets.QLabel()
        icon.setObjectName("statusIcon")
        icon.setFixedSize(24, 24)
        icon.setPixmap(_tinted_pixmap("status-" + state, 22, "#ffffff" if selected else
                                     {"connected": "#16a34a", "disconnected": "#77818a",
                                      "locked": "#d97706", "running": "#3b82f6",
                                      "error": "#ef4444", "conflict": "#d97706"}.get(state, "#16a34a")))
        layout.addWidget(icon, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        texts = QtWidgets.QVBoxLayout()
        texts.setSpacing(1)
        title = QtWidgets.QLabel(name)
        title.setObjectName("profileRowTitle")
        subtitle = QtWidgets.QLabel(status)
        subtitle.setObjectName("profileRowStatus")
        texts.addWidget(title)
        texts.addWidget(subtitle)
        layout.addLayout(texts, 1)
        return row

    @staticmethod
    def _section_row(text: str, name: str):
        label = QtWidgets.QLabel(text)
        label.setObjectName(name)
        if name == "sidebarSection":
            label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred, QtWidgets.QSizePolicy.Policy.Fixed)
        return label

    @staticmethod
    def _drive_row(name: str, detail: str):
        row = QtWidgets.QWidget()
        row.setObjectName("driveRow")
        row.setFixedHeight(74)
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(14, 8, 8, 8)
        layout.setSpacing(10)
        icon = QtWidgets.QLabel()
        icon.setObjectName("driveIcon")
        icon.setPixmap(_tinted_pixmap("drive", 22, "#535b62"))
        icon.setFixedSize(24, 24)
        layout.addWidget(icon, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        text = QtWidgets.QVBoxLayout()
        text.setSpacing(1)
        title = QtWidgets.QLabel(name)
        subtitle = QtWidgets.QLabel(detail)
        subtitle.setObjectName("muted")
        text.addWidget(title)
        text.addWidget(subtitle)
        layout.addLayout(text, 1)
        return row

    def _build_profile_page(self):
        page = QtWidgets.QWidget()
        page.setObjectName("profilePage")
        outer = QtWidgets.QVBoxLayout(page)
        outer.setContentsMargins(18, 16, 18, 18)
        outer.setSpacing(12)

        heading = QtWidgets.QHBoxLayout()
        self.title = QtWidgets.QLabel(self.profile.name)
        self.title.setObjectName("pageTitle")
        heading.addWidget(self.title, 1)
        self.profile_menu_button = QtWidgets.QToolButton()
        self.profile_menu_button.setObjectName("profileMoreButton")
        self.profile_menu_button.setIcon(_tinted_icon("more", "#4b535a", 20))
        self.profile_menu_button.setIconSize(QtCore.QSize(20, 20))
        self.profile_menu_button.setFixedSize(36, 36)
        self.profile_menu_button.setToolTip(_("More actions"))
        profile_menu = QtWidgets.QMenu(self.profile_menu_button)
        for text in (_("Open the copy on the drive"), _("Bind to another drive…"),
                     _("Write the marker to the drive"), _("Clear the version history…"),
                     _("Export…"), _("Delete profile…"), _("Eject")):
            profile_menu.addAction(text, lambda: None)
        self.profile_menu_button.setMenu(profile_menu)
        self.profile_menu_button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        heading.addWidget(self.profile_menu_button)
        outer.addLayout(heading)

        drive_line = QtWidgets.QHBoxLayout()
        self.volume_icon = QtWidgets.QLabel()
        self.volume_icon.setObjectName("driveIcon")
        self.volume_icon.setPixmap(_tinted_pixmap("drive", 20, "#66717a"))
        self.volume_icon.setFixedSize(22, 22)
        drive_line.addWidget(self.volume_icon, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.volume_summary = QtWidgets.QLabel()
        self.volume_summary.setObjectName("driveSummary")
        drive_line.addWidget(self.volume_summary)
        self.info_button = QtWidgets.QToolButton()
        self.info_button.setIcon(_tinted_icon("info", "#3b82f6", 18))
        self.info_button.setIconSize(QtCore.QSize(18, 18))
        self.info_button.setObjectName("infoButton")
        self.info_button.setToolTip(_("Drive details"))
        self.info_button.clicked.connect(self._show_drive_details)
        drive_line.addWidget(self.info_button, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        drive_line.addStretch(1)
        outer.addLayout(drive_line)

        self.banner = QtWidgets.QFrame()
        self.banner.setObjectName("infoBanner")
        banner_layout = QtWidgets.QHBoxLayout(self.banner)
        banner_layout.setContentsMargins(10, 7, 10, 7)
        self.banner_icon = QtWidgets.QLabel()
        self.banner_icon.setObjectName("bannerIcon")
        self.banner_icon.setFixedSize(20, 20)
        self.warning = QtWidgets.QLabel()
        self.warning.setWordWrap(True)
        self.message_row = MessageRow(self.banner_icon, self.warning)
        # Строка сообщения — своей высоты и по центру баннера (не растягивается до высоты кнопки).
        banner_layout.addWidget(self.message_row, 1, QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.banner_action = QtWidgets.QPushButton(_("Unlock…"))
        self.banner_action.clicked.connect(lambda: None)
        banner_layout.addWidget(self.banner_action, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.banner_action.setVisible(False)
        self.banner.setVisible(False)
        outer.addWidget(self.banner)

        backup_card = self._card()
        backup_layout = QtWidgets.QVBoxLayout(backup_card)
        backup_layout.setContentsMargins(12, 10, 12, 10)
        backup_layout.setSpacing(8)
        backup_header = QtWidgets.QHBoxLayout()
        backup_header.addWidget(self._section_row(_("Backup"), "cardTitle"))
        backup_header.addStretch(1)
        backup_layout.addLayout(backup_header)
        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(6)
        self.backup_button = QtWidgets.QPushButton(_("Back up"))
        self.backup_button.setObjectName("primaryButton")
        self.backup_button.setIcon(_tinted_icon("play", "#ffffff", 18))
        self.backup_button.setIcon(_tinted_icon("play", "#ffffff", 18))
        self.preview_button = QtWidgets.QPushButton(_("Preview"))
        self.restore_button = QtWidgets.QPushButton(_("Restore…"))
        for button in (self.backup_button, self.preview_button, self.restore_button):
            button.clicked.connect(lambda: None)
            buttons.addWidget(button)
        buttons.addStretch(1)
        self.log_button = QtWidgets.QPushButton(_("Log…"))
        self.settings_button = QtWidgets.QPushButton(_("Profile settings…"))
        self.log_button.clicked.connect(self._show_log)
        self.settings_button.clicked.connect(self._show_settings)
        buttons.addWidget(self.log_button)
        buttons.addWidget(self.settings_button)
        backup_layout.addLayout(buttons)
        self.status_label = QtWidgets.QLabel()
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        backup_layout.addWidget(self.status_label)

        self.progress_box = QtWidgets.QWidget()
        progress_row = QtWidgets.QHBoxLayout(self.progress_box)
        progress_row.setContentsMargins(0, 0, 0, 0)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(True)
        self.stop_button = QtWidgets.QPushButton(_("Stop"))
        self.stop_button.setObjectName("dangerButton")
        self.stop_button.clicked.connect(lambda: None)
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.stop_button)
        backup_layout.addWidget(self.progress_box)
        self.progress_box.setVisible(False)
        outer.addWidget(backup_card)

        jobs_card = self._card()
        jobs_layout = QtWidgets.QVBoxLayout(jobs_card)
        jobs_layout.setContentsMargins(12, 10, 12, 10)
        jobs_layout.setSpacing(6)
        jobs_header = QtWidgets.QHBoxLayout()
        self.jobs_title = self._section_row(_("Jobs (%d)") % len(self.profile.jobs), "cardTitle")
        jobs_header.addWidget(self.jobs_title)
        jobs_header.addStretch(1)
        add_job_text = _("+ Add job")
        self.add_job_button = QtWidgets.QPushButton(add_job_text[2:] if add_job_text.startswith("+ ") else add_job_text)
        self.add_job_button.setIcon(_tinted_icon("plus", "#4b535a", 16))
        self.add_job_button.clicked.connect(self._new_job)
        jobs_header.addWidget(self.add_job_button)
        jobs_layout.addLayout(jobs_header)
        self.job_container = JobRowsContainer(self)
        self.job_scroll = QtWidgets.QScrollArea()
        self.job_scroll.setObjectName("jobScroll")
        self.job_scroll.setWidgetResizable(True)
        self.job_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.job_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.job_scroll.setWidget(self.job_container)
        jobs_layout.addWidget(self.job_scroll, 1)
        self._populate_job_list()
        outer.addWidget(jobs_card, 1)
        return page

    @staticmethod
    def _card():
        card = QtWidgets.QFrame()
        card.setObjectName("card")
        return card

    def _job_row(self, job, index: int):
        row = QtWidgets.QWidget()
        row.setObjectName("jobRow")
        row.setProperty("theme", self.property("theme"))
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)
        handle = JobDragHandle(id(job), self._start_job_drag, row)
        row_tint = "#b4bbc1" if self.property("theme") == "dark" else "#77818a"
        handle.setPixmap(_tinted_pixmap("drag", 18, row_tint))
        handle.setFixedSize(20, 24)
        layout.addWidget(handle, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        enabled = QtWidgets.QCheckBox()
        enabled.setChecked(job.enabled)
        enabled.setToolTip(_("Job is enabled"))
        enabled.stateChanged.connect(lambda: None)
        layout.addWidget(enabled, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        labels = QtWidgets.QVBoxLayout()
        labels.setSpacing(2)
        title = QtWidgets.QLabel("%s  →  %s" % (job.source, job.dest))
        title.setObjectName("jobTitle")
        subtitle_row = QtWidgets.QHBoxLayout()
        subtitle_row.setSpacing(8)
        mode = {"mirror_versions": _("Mirror with version history"), "mirror": _("Mirror"),
                "accumulate": _("Accumulate")}[job.mode]
        subtitle_parts = [mode]
        if not job.in_auto:
            subtitle_parts.append(_("manual only"))
        if not job.enabled:
            subtitle_parts.append(_("disabled"))
        subtitle = QtWidgets.QLabel(" · ".join(subtitle_parts))
        subtitle.setObjectName("muted")
        labels.addWidget(title)
        subtitle_row.addWidget(subtitle)
        if job.in_auto:
            badge = QtWidgets.QWidget()
            badge.setObjectName("autoBadge")
            badge.setToolTip(_("The job runs in automatic backup when the drive is inserted"))
            badge_layout = QtWidgets.QHBoxLayout(badge)
            badge_layout.setContentsMargins(7, 2, 7, 2)
            badge_layout.setSpacing(4)
            sync_icon = QtWidgets.QLabel()
            sync_icon.setPixmap(_tinted_pixmap("sync", 12, "#ffffff"))
            sync_icon.setFixedSize(12, 12)
            auto_label = QtWidgets.QLabel(_("AUTO"))
            auto_label.setObjectName("autoBadgeText")
            badge_layout.addWidget(sync_icon)
            badge_layout.addWidget(auto_label)
            subtitle_row.addWidget(badge)
        subtitle_row.addStretch(1)
        labels.addLayout(subtitle_row)
        layout.addLayout(labels, 1)
        buttons = (("play", "Back up only this job", "runJob", "#16a34a"),
                   ("edit", "Edit job", "editButton", "#3b82f6"),
                   ("trash", "Remove job", "deleteButton", "#ef4444"))
        for icon_name, tooltip, object_name, color in buttons:
            button = self._row_button(icon_name, tooltip, object_name, color)
            layout.addWidget(button, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        more = QtWidgets.QToolButton()
        more.setObjectName("moreButton")
        more.setIcon(_tinted_icon("more", row_tint, 18))
        more.setIconSize(QtCore.QSize(18, 18))
        more.setFixedSize(32, 32)
        more.setToolTip(_("More actions"))
        menu = QtWidgets.QMenu(more)
        move_up = menu.addAction(_("Move up"))
        move_up.setEnabled(index > 0)
        move_up.triggered.connect(lambda _checked=False, i=index: self.move_job(i, -1))
        move_down = menu.addAction(_("Move down"))
        move_down.setEnabled(index < len(self.profile.jobs) - 1)
        move_down.triggered.connect(lambda _checked=False, i=index: self.move_job(i, 1))
        menu.addSeparator()
        actions = (_("Turn off automatic") if job.in_auto else _("Turn on automatic"),
                   _("Restore this job…"), _("Open the job’s copy"),
                   _("Remove excluded files from the copy…"), _("Clear this job’s copy…"))
        for text in actions:
            menu.addAction(text, lambda: None)
        more.setMenu(menu)
        more.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        layout.addWidget(more, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        row.setProperty("jobEnabled", job.enabled)
        row.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        row.setFixedHeight(row.sizeHint().height())
        return row

    @staticmethod
    def _row_button(icon_name: str, tooltip: str, object_name: str, color: str):
        button = QtWidgets.QToolButton()
        button.setIcon(_tinted_icon(icon_name, color, 20))
        button.setIconSize(QtCore.QSize(20, 20))
        button.setToolTip(_(tooltip))
        button.setObjectName(object_name)
        button.setFixedSize(32, 32)
        button.clicked.connect(lambda: None)
        return button

    def _populate_job_list(self):
        while self.job_container.rows_layout.count():
            item = self.job_container.rows_layout.takeAt(0)
            old_row = item.widget()
            if old_row is not None:
                old_row.setParent(None)
                if old_row is not self._active_drag_row:
                    old_row.deleteLater()
        self.job_container.rows = []
        self.job_rows = []
        for index, job in enumerate(self.profile.jobs):
            row = self._job_row(job, index)
            self.job_container.rows_layout.addWidget(row)
            self.job_container.rows.append(row)
            self.job_rows.append(row)
        self.job_container.rows_layout.addStretch(1)

    def _start_job_drag(self, job_id: int, handle: JobDragHandle):
        row = handle.parentWidget()
        self._active_drag_row = row
        effect = QtWidgets.QGraphicsOpacityEffect(row)
        effect.setOpacity(0.42)
        row.setGraphicsEffect(effect)
        mime = QtCore.QMimeData()
        mime.setData(JOB_MIME_TYPE, str(job_id).encode("ascii"))
        drag = QtGui.QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(row.grab())
        drag.setHotSpot(QtCore.QPoint(handle.width() // 2, handle.height() // 2))
        execute = getattr(drag, "exec", None) or drag.exec_
        try:
            execute(QtCore.Qt.DropAction.MoveAction)
        finally:
            row.setGraphicsEffect(None)
            if row not in self.job_container.rows:
                row.deleteLater()
            self._active_drag_row = None
            self.job_container._hide_drop_line()

    def _reorder_job(self, source: int, destination: int):
        jobs = list(self.profile.jobs)
        job = jobs.pop(source)
        jobs.insert(destination, job)
        self.profile.jobs = jobs
        self._populate_job_list()

    def move_job(self, index: int, offset: int) -> bool:
        """Переместить демо-задание и перестроить строки списка."""
        destination = index + offset
        if index < 0 or index >= len(self.profile.jobs) or destination < 0 or destination >= len(self.profile.jobs):
            return False
        self._reorder_job(index, destination)
        return True

    def _show_drive_details(self):
        menu = self._drive_details_menu()
        menu.exec(self.info_button.mapToGlobal(QtCore.QPoint(0, self.info_button.height())))

    def _drive_details_menu(self):
        menu = QtWidgets.QMenu(self.info_button)
        details = (_("Device") + ": SanDisk Ultra", _("Volume label") + ": SANDISK",
                   _("File system") + ": exFAT", _("Size") + ": " + format_size(128_000_000_000),
                   _("Free") + ": " + format_size(21_600_000_000))
        for detail in details:
            icon = QtWidgets.QLabel()
            icon.setPixmap(_tinted_pixmap("info", 16, "#3b82f6"))
            icon.setFixedSize(18, 18)
            label = QtWidgets.QLabel(detail)
            row = MessageRow(icon, label)
            action = QtWidgets.QWidgetAction(menu)
            action.setDefaultWidget(row)
            menu.addAction(action)
        return menu

    def _show_log(self):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(_("Log — %s") % self.profile.name)
        dialog.resize(850, 520)
        layout = QtWidgets.QVBoxLayout(dialog)
        log = QtWidgets.QPlainTextEdit("16.10.2026 13:15  " + _("completed"))
        log.setReadOnly(True)
        layout.addWidget(log)
        close = QtWidgets.QPushButton(_("Close"))
        close.clicked.connect(dialog.accept)
        layout.addWidget(close, 0, QtCore.Qt.AlignmentFlag.AlignRight)
        exec_dialog(dialog)

    def _show_settings(self):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(_("Profile settings…"))
        dialog.resize(540, 460)
        layout = QtWidgets.QVBoxLayout(dialog)
        form = QtWidgets.QFormLayout()
        form.addRow(_("Name"), QtWidgets.QLineEdit(self.profile.name))
        form.addRow(_("Backup folder on the drive"), QtWidgets.QLineEdit(self.profile.root))
        form.addRow(_("Back up automatically when the drive is inserted"), QtWidgets.QCheckBox())
        layout.addLayout(form)
        layout.addStretch(1)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Cancel |
                                             QtWidgets.QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        exec_dialog(dialog)

    def _new_job(self):
        exec_dialog(JobDialog(self, self.profile))

    def set_demo_state(self, state: str):
        self.state = state
        connected = state in ("connected", "running")
        locked = state == "locked"
        self.profile_rows[0].setProperty("state", state)
        icon = self.profile_rows[0].findChild(QtWidgets.QLabel, "statusIcon")
        subtitle = self.profile_rows[0].findChild(QtWidgets.QLabel, "profileRowStatus")
        icon_name = "status-" + state if state in ("connected", "disconnected", "running", "locked",
                                                    "error", "conflict") else "status-connected"
        icon_color = {"connected": "#ffffff", "disconnected": "#ffffff", "running": "#ffffff",
                      "locked": "#ffffff", "error": "#ffffff", "conflict": "#ffffff"}.get(state, "#ffffff")
        icon.setPixmap(_tinted_pixmap(icon_name, 22, icon_color))
        subtitle.setText({"connected": _("drive connected"), "disconnected": _("drive not connected"),
                          "running": _("Preparing…"), "locked": _("drive connected, locked"),
                          "error": _("Needs attention"), "conflict": _("Needs attention")}.get(
                              state, _("drive connected")))
        self.volume_summary.setText("SANDISK · SanDisk Ultra · exfat · %s" %
                                     (_("%s free") % format_size(21_600_000_000) if connected else
                                      _("Drive not connected · %s") % "SANDISK"))
        if locked:
            self.volume_summary.setText("SANDISK · SanDisk Ultra · " + _("drive connected, locked"))
        self.banner.setVisible(False)
        self.banner_action.setVisible(False)
        self.banner.setObjectName("infoBanner")
        self.banner_icon.setPixmap(_tinted_pixmap("info", 18, "#3b82f6"))
        if state == "disconnected":
            self.message_row.setMessage(_("Insert the profile’s drive to back up or restore. Settings and jobs can be "
                                          "edited now."))
            self.banner.setVisible(True)
        elif locked:
            self.message_row.setMessage(_("The drive is encrypted (BitLocker) and locked. Unlock it in File Explorer."))
            self.banner_icon.setPixmap(_tinted_pixmap("warning", 18, "#d97706"))
            self.banner.setObjectName("warningBanner")
            self.banner_action.setVisible(True)
            self.banner.setVisible(True)
        elif state in ("error", "conflict"):
            self.message_row.setMessage(_("Needs attention"))
            self.banner_icon.setPixmap(_tinted_pixmap("status-error" if state == "error" else "status-conflict",
                                                       18, "#ef4444" if state == "error" else "#d97706"))
            self.banner.setObjectName("errorBanner")
            self.banner.setVisible(True)
        self.style().unpolish(self.banner)
        self.style().polish(self.banner)
        self.style().unpolish(self.profile_rows[0])
        self.style().polish(self.profile_rows[0])
        self.backup_button.setEnabled(connected and state != "running")
        self.preview_button.setEnabled(connected and state != "running")
        self.restore_button.setEnabled(connected)
        self.progress_box.setVisible(state == "running")
        self.progress.setValue(42)
        status = _("Last backup: %s — %s") % (
            datetime(2026, 10, 16, 13, 15).strftime("%d.%m.%Y %H:%M"), _("completed"))
        auto = [_("Automatic backup: on"), _("countdown %d s") % 10,
                _("not more often than every %d h") % 4, _("ejects after success")]
        self.status_label.setText(status + "   ·   " + " · ".join(auto))

    def set_theme(self, theme: str):
        self.setProperty("theme", theme)
        icon_tint = "#c0c5ca" if theme == "dark" else "#60656b"
        drive_tint = "#c0c5ca" if theme == "dark" else "#66717a"
        self.profile_menu_button.setIcon(_tinted_icon("more", icon_tint, 20))
        for icon in self.findChildren(QtWidgets.QLabel, "driveIcon"):
            icon.setPixmap(_tinted_pixmap("drive", 22, drive_tint))
        self.create_profile_button.setIcon(_tinted_icon("plus", icon_tint, 16))
        self.add_job_button.setIcon(_tinted_icon("plus", icon_tint, 16))
        self._populate_job_list()
        self.style().unpolish(self)
        self.style().polish(self)
        self.setStyleSheet((Path(__file__).resolve().parent / "style.qss").read_text(encoding="utf-8"))
