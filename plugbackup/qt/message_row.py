"""Общая строка сообщения со значком, выровненным по первой строке."""
from __future__ import annotations

from plugbackup.qt.compat import QtCore, QtWidgets


class MessageRow(QtWidgets.QWidget):
    """Показывает текст с переносом и значок по центру первой строки."""

    def __init__(self, icon: QtWidgets.QWidget, label: QtWidgets.QLabel, parent=None):
        super().__init__(parent)
        self.icon = icon
        self.label = label
        self.row_layout = QtWidgets.QHBoxLayout(self)
        self.row_layout.setContentsMargins(0, 0, 0, 0)
        self.row_layout.setSpacing(6)
        # Значок — в своей колонке с верхним отступом: так его можно опустить к центру первой строки.
        self.icon_box = QtWidgets.QVBoxLayout()
        self.icon_box.setContentsMargins(0, 0, 0, 0)
        self.icon_box.addWidget(icon)
        self.icon_box.addStretch(1)
        self.row_layout.addLayout(self.icon_box)
        self.row_layout.addWidget(label, 1, QtCore.Qt.AlignmentFlag.AlignTop)
        self.label.setWordWrap(True)
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self._align_icon_to_first_line()

    def setMessage(self, text: str) -> None:
        self.label.setText(text)
        self._align_icon_to_first_line()
        self.updateGeometry()

    def _align_icon_to_first_line(self) -> None:
        """Совместить центр значка с центром первой строки текста.

        Если строка выше значка — опускаем значок; если значок выше строки (обычно: значок 20 px,
        строка 16–17 px) — опускаем текст, иначе текст оказывается выше середины значка.
        """
        line = self.label.fontMetrics().lineSpacing()
        icon_height = self.icon.sizeHint().height()
        self.icon_box.setContentsMargins(0, max(0, (line - icon_height) // 2), 0, 0)
        self.label.setContentsMargins(0, max(0, (icon_height - line + 1) // 2), 0, 0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        layout = self.row_layout
        icon_width = self.icon.sizeHint().width() if not self.icon.isHidden() else 0
        gap = layout.spacing() if icon_width else 0
        text_width = max(1, width - icon_width - gap)
        text_height = self.label.heightForWidth(text_width)   # с учётом отступа текста
        icon_height = 0
        if not self.icon.isHidden():
            icon_height = self.icon.sizeHint().height() + self.icon_box.contentsMargins().top()
        return max(text_height, icon_height)

    def sizeHint(self):
        width = max(500, self.label.sizeHint().width())
        return QtCore.QSize(width, self.heightForWidth(width))
