from __future__ import annotations

from pathlib import Path
from typing import Any


def normalize_theme(theme: str) -> str:
    return "dark" if theme.lower() == "dark" else "light"


def system_theme(QtWidgets: Any) -> str:
    app = QtWidgets.QApplication.instance()
    if app is None:
        return "light"
    color = app.palette().color(app.palette().ColorRole.Window)
    luminance = (0.2126 * color.red()) + (0.7152 * color.green()) + (0.0722 * color.blue())
    return "dark" if luminance < 128 else "light"


def theme_palette(theme: str) -> dict[str, str]:
    if normalize_theme(theme) == "dark":
        return {
            "window": "#1b1b1b",
            "panel": "#242424",
            "panel_alt": "#2d2d2d",
            "viewer_bg": "#101010",
            "input": "#2a2a2a",
            "text": "#f2f2f2",
            "muted": "#b8b8b8",
            "border": "#4a4a4a",
            "button": "#303030",
            "button_hover": "#3a3a3a",
            "accent": "#ff9f1c",
            "highlight_text": "#111111",
            "progress": "#ff9f1c",
        }
    return {
        "window": "#f3f4f6",
        "panel": "#ffffff",
        "panel_alt": "#f8fafc",
        "viewer_bg": "#edf0f3",
        "input": "#ffffff",
        "text": "#202124",
        "muted": "#636a73",
        "border": "#c7ccd4",
        "button": "#f5f7fa",
        "button_hover": "#e9edf3",
        "accent": "#995000",
        "highlight_text": "#ffffff",
        "progress": "#d97706",
    }


def theme_stylesheet(palette: dict[str, str]) -> str:
    return f"""
        QMainWindow, QDialog {{
            background: {palette['window']};
            color: {palette['text']};
        }}
        QWidget {{
            color: {palette['text']};
        }}
        QMenuBar, QMenu {{
            background: {palette['panel']};
            color: {palette['text']};
            border: 1px solid {palette['border']};
        }}
        QMenuBar::item:selected, QMenu::item:selected {{
            background: {palette['button_hover']};
        }}
        QMenu::separator {{
            background: {palette['border']};
            height: 1px;
            margin: 4px 8px;
        }}
        QDockWidget {{
            color: {palette['text']};
        }}
        QDockWidget::title {{
            background: {palette['panel']};
            color: {palette['text']};
            padding: 4px 6px;
            font-weight: 600;
        }}
        QWidget#centralSpacer {{
            background: {palette['window']};
        }}
        QWidget#mainPanel, QWidget#viewGrid {{
            background: {palette['viewer_bg']};
        }}
        QWidget#mainViewTitleBar {{
            background: {palette['panel']};
            border-bottom: 1px solid {palette['border']};
        }}
        QLabel#mainViewTitle {{
            color: {palette['text']};
            font-weight: 600;
        }}
        QToolButton#splitViewButton {{
            min-width: 22px;
            max-width: 22px;
            padding: 2px;
            font-weight: 700;
        }}
        QWidget#topicPanel,
        QWidget#propertiesPanel, QWidget#propertiesViewport, QScrollArea#propertiesScroll,
        QWidget#outputPanel {{
            background: {palette['panel']};
            color: {palette['text']};
        }}
        QFrame#topicViewPane {{
            border: 1px solid {palette['border']};
            border-radius: 4px;
            background: {palette['viewer_bg']};
            color: {palette['text']};
        }}
        QFrame#topicViewPane[active="true"] {{
            border: 2px solid {palette['accent']};
        }}
        QLabel {{
            background: transparent;
            color: {palette['text']};
        }}
        QLabel#viewTitle {{
            color: {palette['text']};
            font-weight: 600;
        }}
        QFrame#topicViewPane[active="true"] QLabel#viewTitle {{
            color: {palette['accent']};
        }}
        QTreeWidget, QTextEdit, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QScrollArea {{
            background: {palette['input']};
            color: {palette['text']};
            border: 1px solid {palette['border']};
            selection-background-color: {palette['accent']};
            selection-color: {palette['highlight_text']};
        }}
        QTreeWidget {{
            alternate-background-color: {palette['panel_alt']};
            show-decoration-selected: 1;
            outline: 0;
        }}
        QHeaderView::section {{
            background: {palette['panel_alt']};
            color: {palette['text']};
            border: 0;
            border-right: 1px solid {palette['border']};
            border-bottom: 1px solid {palette['border']};
            padding: 4px 6px;
        }}
        QTreeWidget::item:selected {{
            background: {palette['accent']};
            color: {palette['highlight_text']};
        }}
        QTreeWidget::item:hover:!selected {{
            background: {palette['button_hover']};
        }}
        QTreeWidget::indicator {{
            width: 14px;
            height: 14px;
        }}
        QCheckBox {{
            background: transparent;
            color: {palette['text']};
            spacing: 6px;
        }}
        QCheckBox::indicator {{
            width: 14px;
            height: 14px;
        }}
        QPushButton, QToolButton {{
            background: {palette['button']};
            color: {palette['text']};
            border: 1px solid {palette['border']};
            border-radius: 4px;
            padding: 3px 8px;
        }}
        QToolButton {{
            padding: 2px;
        }}
        QPushButton:hover, QToolButton:hover {{
            background: {palette['button_hover']};
        }}
        QToolButton:checked {{
            background: {palette['accent']};
            color: {palette['highlight_text']};
        }}
        QPushButton:focus, QToolButton:focus, QLineEdit:focus,
        QDoubleSpinBox:focus, QComboBox:focus {{
            border: 1px solid {palette['accent']};
        }}
        QPushButton:disabled, QToolButton:disabled {{
            color: {palette['muted']};
        }}
        QComboBox::drop-down, QSpinBox::up-button, QSpinBox::down-button,
        QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
            background: {palette['button']};
            border-left: 1px solid {palette['border']};
            width: 18px;
        }}
        QComboBox QAbstractItemView {{
            background: {palette['input']};
            color: {palette['text']};
            selection-background-color: {palette['accent']};
            selection-color: {palette['highlight_text']};
        }}
        QGroupBox {{
            color: {palette['text']};
            border: 1px solid {palette['border']};
            border-radius: 4px;
            font-weight: 600;
            margin-top: 8px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 8px;
            padding: 0 4px;
        }}
        QScrollBar:vertical, QScrollBar:horizontal {{
            background: {palette['panel']};
            border: 0;
            margin: 0;
        }}
        QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
            background: {palette['border']};
            border-radius: 4px;
            min-height: 24px;
            min-width: 24px;
        }}
        QScrollBar::add-line, QScrollBar::sub-line {{
            width: 0;
            height: 0;
        }}
        QProgressBar {{
            background: {palette['input']};
            color: {palette['text']};
            border: 1px solid {palette['border']};
            border-radius: 3px;
            text-align: center;
        }}
        QProgressBar::chunk {{
            background: {palette['progress']};
        }}
        QSlider::groove:horizontal {{
            background: {palette['border']};
            height: 4px;
            border-radius: 2px;
        }}
        QSlider::handle:horizontal {{
            background: {palette['accent']};
            width: 14px;
            margin: -5px 0;
            border-radius: 7px;
        }}
    """


def tinted_standard_icon(QtGui: Any, widget: Any, symbol: Any, palette: dict[str, str]) -> Any:
    source = widget.style().standardIcon(symbol).pixmap(16, 16)
    icon = QtGui.QIcon()
    for mode, color in ((QtGui.QIcon.Mode.Normal, palette["text"]),
                        (QtGui.QIcon.Mode.Disabled, palette["muted"])):
        pixmap = source.copy()
        painter = QtGui.QPainter(pixmap)
        painter.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), QtGui.QColor(color))
        painter.end()
        icon.addPixmap(pixmap, mode)
    return icon


def create_gui_style(QtWidgets: Any, QtCore: Any, QtGui: Any) -> Any:
    class GuiStyle(QtWidgets.QProxyStyle):
        def drawPrimitive(self, element: Any, option: Any, painter: Any, widget: Any = None) -> None:
            if element == QtWidgets.QStyle.PrimitiveElement.PE_IndicatorBranch:
                if not option.state & QtWidgets.QStyle.StateFlag.State_Children:
                    return
                center = QtCore.QPointF(option.rect.center())
                offsets = ((-4, -2), (4, -2), (0, 3)) if option.state & QtWidgets.QStyle.StateFlag.State_Open else ((-2, -4), (-2, 4), (3, 0))
                painter.save()
                painter.setPen(QtCore.Qt.PenStyle.NoPen)
                painter.setBrush(option.palette.text())
                painter.drawPolygon(QtGui.QPolygonF([center + QtCore.QPointF(x, y) for x, y in offsets]))
                painter.restore()
                return
            if element not in (QtWidgets.QStyle.PrimitiveElement.PE_IndicatorCheckBox,
                               QtWidgets.QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck):
                super().drawPrimitive(element, option, painter, widget)
                return
            checked = bool(option.state & QtWidgets.QStyle.StateFlag.State_On)
            mixed = bool(option.state & QtWidgets.QStyle.StateFlag.State_NoChange)
            palette = option.palette
            painter.save()
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            rect = QtCore.QRectF(option.rect).adjusted(1, 1, -1, -1)
            painter.setPen(QtGui.QPen(palette.color(QtGui.QPalette.ColorRole.Mid), 1))
            painter.setBrush(palette.highlight() if checked or mixed else palette.base())
            painter.drawRoundedRect(rect, 2, 2)
            if checked or mixed:
                painter.setPen(QtGui.QPen(palette.highlightedText(), 1.8))
                if mixed:
                    painter.drawLine(rect.topLeft() + QtCore.QPointF(3, rect.height() / 2),
                                     rect.topRight() + QtCore.QPointF(-3, rect.height() / 2))
                else:
                    painter.drawPolyline(QtGui.QPolygonF([
                        QtCore.QPointF(rect.left() + rect.width() * .2, rect.top() + rect.height() * .5),
                        QtCore.QPointF(rect.left() + rect.width() * .43, rect.top() + rect.height() * .75),
                        QtCore.QPointF(rect.left() + rect.width() * .82, rect.top() + rect.height() * .25),
                    ]))
            painter.restore()

    style = GuiStyle(QtWidgets.QStyleFactory.create("Fusion"))
    style.setObjectName("rosbagel")
    return style


def local_changelog_text() -> str:
    changelog_path = Path(__file__).resolve().parents[2] / "CHANGELOG.md"
    try:
        text = changelog_path.read_text(encoding="utf-8")
    except OSError:
        return "Local changelog was not found in this installation."
    return text[:12_000]
