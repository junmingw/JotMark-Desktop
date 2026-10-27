"""Compatibility adapters for MonkeyQt releases with smaller public APIs.

The training UI was written against a MonkeyQt build that exposed a few helper
widgets from its top-level package.  The pinned, installable release omits
those widgets.  Keep the application usable without pinning an unpublished UI
package: message dialogs use Qt directly, loading placeholders are lightweight
Qt painting, and charts reuse this project's ``ThesisChartCard``.
"""

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from monkeyqt import ThemeEngine
from TrainingPlatform.ui.components.thesis_chart import ThesisChartCard


class MkMessageBox:
    @staticmethod
    def info(parent, title, text, **_kwargs):
        QMessageBox.information(parent, title, text)

    @staticmethod
    def success(parent, title, text, **_kwargs):
        QMessageBox.information(parent, title, text)

    @staticmethod
    def warning(parent, title, text, **_kwargs):
        QMessageBox.warning(parent, title, text)

    @staticmethod
    def error(parent, title, text, **_kwargs):
        QMessageBox.critical(parent, title, text)

    @staticmethod
    def confirm(parent, title, text, danger=False, ok_text=None, cancel_text=None, **_kwargs):
        buttons = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        result = QMessageBox.question(parent, title, text, buttons, QMessageBox.StandardButton.No)
        return result == QMessageBox.StandardButton.Yes


@dataclass(frozen=True)
class MkSkeletonBlock:
    """A proportional or pixel-based rectangle used by :class:`MkSkeleton`."""

    x: float
    y: float
    width: float
    height: float
    radius: float = 4
    shape: str = "rect"


class MkSkeleton(QWidget):
    """Small, dependency-free replacement for MonkeyQt's shimmering skeleton."""

    def __init__(self, blocks=None, parent=None, **_kwargs):
        super().__init__(parent)
        self.blocks = list(blocks or [])
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    @staticmethod
    def _value(value, total):
        return int(value * total) if 0 < value <= 1 else int(value)

    def paintEvent(self, _event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#e2e8f0" if not ThemeEngine.is_dark() else "#303038"))
            for block in self.blocks:
                x = self._value(block.x, self.width())
                y = self._value(block.y, self.height())
                width = max(1, self._value(block.width, self.width()))
                height = max(1, self._value(block.height, self.height()))
                if block.shape == "circle":
                    painter.drawEllipse(x, y, width, height)
                else:
                    painter.drawRoundedRect(x, y, width, height, block.radius, block.radius)
        finally:
            painter.end()


class MkChartGroup(QWidget):
    """Compatible chart group backed by the project's native chart cards."""

    visibilityChanged = Signal(str, bool)
    seriesToggled = Signal(bool, bool)

    def __init__(
        self,
        title="",
        columns=3,
        is_expanded=True,
        enable_series_toggle=False,
        series_options=None,
        parent=None,
        **_kwargs,
    ):
        super().__init__(parent)
        self.chart_cards = {}
        self.visible_charts = {}
        self.is_expanded = bool(is_expanded)
        self.show_train_series = True
        self.show_val_series = True
        self._columns = max(1, int(columns))

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        header = QHBoxLayout()
        label = QLabel(title, self)
        label.setStyleSheet("font-size: 15px; font-weight: 700;")
        header.addWidget(label)
        header.addStretch()
        if enable_series_toggle:
            labels = list(series_options or ("Train", "Val"))
            self._train_button = QPushButton(labels[0], self)
            self._val_button = QPushButton(labels[1] if len(labels) > 1 else "Val", self)
            for button in (self._train_button, self._val_button):
                button.setCheckable(True)
                button.setChecked(True)
                header.addWidget(button)
            self._train_button.toggled.connect(self._on_series_toggled)
            self._val_button.toggled.connect(self._on_series_toggled)
        root.addLayout(header)

        self._grid_host = QWidget(self)
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(10)
        self._grid.setVerticalSpacing(10)
        root.addWidget(self._grid_host)

    def _on_series_toggled(self, _checked):
        self.show_train_series = self._train_button.isChecked()
        self.show_val_series = self._val_button.isChecked()
        self.seriesToggled.emit(self.show_train_series, self.show_val_series)

    def add_chart(self, chart_id, title=None, default_visible=True, y_label="", chart_min_height=210, **_kwargs):
        card = ThesisChartCard(
            metric_key=chart_id,
            display_title=title or chart_id,
            is_dark=ThemeEngine.is_dark(),
            parent=self._grid_host,
        )
        card.setMinimumHeight(int(chart_min_height))
        index = len(self.chart_cards)
        self._grid.addWidget(card, index // self._columns, index % self._columns)
        self.chart_cards[chart_id] = card
        self.visible_charts[chart_id] = bool(default_visible)
        card.setVisible(bool(default_visible))
        return card

    def charts(self):
        return list(self.chart_cards.keys())

    def get_chart(self, chart_id):
        return self.chart_cards.get(chart_id)

    def set_chart_visible(self, chart_id, visible):
        if chart_id not in self.chart_cards:
            return
        visible = bool(visible)
        changed = self.visible_charts.get(chart_id) != visible
        self.visible_charts[chart_id] = visible
        self.chart_cards[chart_id].setVisible(visible)
        if changed:
            self.visibilityChanged.emit(chart_id, visible)

    def set_theme(self, is_dark):
        for card in self.chart_cards.values():
            card.set_theme(is_dark)

    def set_loading(self, loading=True):
        for card in self.chart_cards.values():
            card.set_loading(loading)


class MkChartDashboard(QWidget):
    """Vertical container matching the subset of MkChartDashboard used here."""

    def __init__(self, parent=None, scrollable=False, spacing=14, **_kwargs):
        super().__init__(parent)
        self.groups = []
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(int(spacing))

    def add_group(self, **kwargs):
        group = MkChartGroup(parent=self, **kwargs)
        self.groups.append(group)
        self._layout.addWidget(group)
        return group

    def set_loading(self, loading=True):
        for group in self.groups:
            group.set_loading(loading)

    def set_theme(self, is_dark):
        for group in self.groups:
            group.set_theme(is_dark)


class MkSeries(dict):
    """Data holder retained for source compatibility; current views use dict series."""


MkLineChart = ThesisChartCard
