from __future__ import annotations

from typing import Any, Callable

from rosbagel.gui.theme import tinted_standard_icon

from rosbagel.core.lane_lines import (
    LANE_ROLES,
    LaneBounds,
    LaneFrame,
    LaneOverlayData,
    LanePoint,
    LaneSeries,
    lane_bounds,
)


LANE_COLORS = {
    "center": "#f2c94c",
    "left": "#2f80ed",
    "right": "#eb5757",
}
LANE_LIGHT_COLORS = {"center": "#946000", "left": "#175eac", "right": "#b72d40"}
LANE_PLOT_FIT_MARGIN_PX = 10.0
LANE_PLOT_HELP_TEXT = (
    "Wheel: zoom\n"
    "Middle-drag: pan\n"
    "Middle double-click: reset view\n"
    "XY: swap axes"
)


def create_lane_overlay_panel_class(
    QtWidgets: Any,
    QtCore: Any,
    QtGui: Any,
) -> type:
    class LanePlotWidget(QtWidgets.QWidget):
        def __init__(self, parent: Any | None = None) -> None:
            super().__init__(parent)
            self._palette = {
                "viewer_bg": "#edf0f3",
                "panel": "#ffffff",
                "text": "#202124",
                "muted": "#636a73",
                "border": "#c7ccd4",
                "accent": "#0f6bff",
            }
            self._data: LaneOverlayData | None = None
            self._visible_roles: set[str] = set()
            self._display_settings: dict[str, tuple[bool, float, float]] = {}
            self._timestamp_ns: int | None = None
            self._data_bounds: LaneBounds | None = None
            self._view_bounds: LaneBounds | None = None
            self._empty_text = "Open a bag with lane line PointCloud2 topics."
            self._swap_xy = False
            self._view_is_custom = False
            self._pan_start_pos: Any | None = None
            self._pan_start_bounds: LaneBounds | None = None
            self.setFont(QtGui.QFont("Segoe UI", 9))
            self.setMouseTracking(True)
            self.setMinimumSize(260, 150)

        @property
        def current_timestamp_ns(self) -> int | None:
            return self._timestamp_ns

        @property
        def swap_xy(self) -> bool:
            return self._swap_xy

        def apply_theme(self, palette: dict[str, str]) -> None:
            self._palette = dict(palette)
            self.update()

        def set_empty_text(self, text: str) -> None:
            self._empty_text = text
            self.update()

        def set_data(self, data: LaneOverlayData | None) -> None:
            self._data = data
            self._refresh_bounds()
            self.update()

        def set_visible_roles(self, roles: list[str] | tuple[str, ...] | set[str]) -> None:
            self._visible_roles = set(roles)
            self._refresh_bounds()
            self.update()

        def set_swap_xy(self, swapped: bool) -> None:
            if self._swap_xy == swapped:
                return
            self._swap_xy = swapped
            self._refresh_bounds()
            self.update()

        def set_display_settings(self, settings: dict[str, tuple[bool, float, float]]) -> None:
            if settings == self._display_settings:
                return
            self._display_settings = settings
            self._refresh_bounds()
            self.update()

        def reset_view(self) -> None:
            self._view_is_custom = False
            self._fit_auto_view()
            self.update()

        def show_at_timestamp(self, timestamp_ns: int | None) -> None:
            self._timestamp_ns = timestamp_ns
            if not self._view_is_custom:
                self._fit_auto_view()
            self.update()

        def paintEvent(self, _event: Any) -> None:
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            rect = self.rect()
            palette = self._palette
            painter.fillRect(rect, QtGui.QColor(palette["viewer_bg"]))

            plot_rect = self._plot_rect(rect)
            self._draw_grid(painter, plot_rect)
            self._draw_help_indicator(painter, rect)

            if self._data is None:
                self._draw_center_text(painter, plot_rect, self._empty_text)
                painter.end()
                return
            if not self._visible_roles:
                self._draw_center_text(painter, plot_rect, "Select a lane line topic.")
                painter.end()
                return
            bounds = self._active_bounds()
            if bounds is None:
                self._draw_center_text(painter, plot_rect, "Selected lane topics have no x/y points.")
                painter.end()
                return

            frame_items = self._current_frames()
            if not frame_items:
                self._draw_center_text(painter, plot_rect, "No frame is available at this time.")
                painter.end()
                return

            mapper = self._point_mapper(plot_rect, bounds)
            painter.save()
            painter.setClipRect(plot_rect)
            for series, frame in frame_items:
                self._draw_frame(painter, mapper, series, frame)
            painter.restore()
            self._draw_legend(painter, plot_rect, frame_items)
            self._draw_axes(painter, plot_rect, bounds)
            painter.end()

        def _refresh_bounds(self) -> None:
            if self._data is None:
                self._data_bounds = None
                self._view_bounds = None
                self._view_is_custom = False
                return
            base_bounds = self._data.bounds_for_roles(self._visible_roles)
            self._data_bounds = self._oriented_bounds(base_bounds)
            self._view_is_custom = False
            self._fit_auto_view()

        def _oriented_bounds(self, bounds: LaneBounds | None) -> LaneBounds | None:
            if bounds is None or not self._swap_xy:
                return bounds
            return LaneBounds(
                min_x=-bounds.max_y,
                max_x=-bounds.min_y,
                min_y=bounds.min_x,
                max_y=bounds.max_x,
            )

        def _active_bounds(self) -> LaneBounds | None:
            return self._view_bounds or self._data_bounds

        def _fit_auto_view(self) -> None:
            self._view_bounds = self._fit_bounds_to_plot(self._auto_fit_bounds())

        def _auto_fit_bounds(self) -> LaneBounds | None:
            frame_bounds = self._current_frame_bounds()
            return frame_bounds or self._data_bounds

        def _current_frame_bounds(self) -> LaneBounds | None:
            points: list[LanePoint] = []
            for _series, frame in self._current_frames():
                points.extend(self._oriented_point(point) for point in frame.points)
            return lane_bounds(points)

        def _fit_bounds_to_plot(self, bounds: LaneBounds | None) -> LaneBounds | None:
            if bounds is None:
                return None
            plot_rect = self._plot_rect(self.rect())
            data_width = max(1e-9, bounds.max_x - bounds.min_x)
            data_height = max(1e-9, bounds.max_y - bounds.min_y)
            available_width = max(
                1.0,
                plot_rect.width() - (2.0 * LANE_PLOT_FIT_MARGIN_PX),
            )
            available_height = max(
                1.0,
                plot_rect.height() - (2.0 * LANE_PLOT_FIT_MARGIN_PX),
            )
            scale = min(available_width / data_width, available_height / data_height)
            if scale <= 0.0:
                return bounds
            center_x = (bounds.min_x + bounds.max_x) / 2.0
            center_y = (bounds.min_y + bounds.max_y) / 2.0
            fitted_width = plot_rect.width() / scale
            fitted_height = plot_rect.height() / scale
            return LaneBounds(
                min_x=center_x - (fitted_width / 2.0),
                max_x=center_x + (fitted_width / 2.0),
                min_y=center_y - (fitted_height / 2.0),
                max_y=center_y + (fitted_height / 2.0),
            )

        def _current_frames(self) -> list[tuple[LaneSeries, LaneFrame]]:
            if self._data is None or self._timestamp_ns is None:
                return []
            frames: list[tuple[LaneSeries, LaneFrame]] = []
            for series in self._data.ordered_series(LANE_ROLES):
                if series.role not in self._visible_roles:
                    continue
                visible, _opacity, offset = self._display_settings.get(series.role, (True, 1.0, 0.0))
                if not visible:
                    continue
                frame = series.nearest_frame(self._timestamp_ns + round(offset * 1e9))
                if frame is not None:
                    frames.append((series, frame))
            return frames

        def _plot_rect(self, rect: Any) -> Any:
            left = 58.0
            top = 22.0
            right = 18.0
            bottom = 48.0
            width = max(1.0, float(rect.width()) - left - right)
            height = max(1.0, float(rect.height()) - top - bottom)
            return QtCore.QRectF(left, top, width, height)

        def _draw_grid(self, painter: Any, plot_rect: Any) -> None:
            border = QtGui.QColor(self._palette["border"])
            muted = QtGui.QColor(self._palette["muted"])
            muted.setAlpha(55)
            painter.setPen(QtGui.QPen(muted, 1))
            for index in range(1, 5):
                x = plot_rect.left() + (plot_rect.width() * index / 5.0)
                y = plot_rect.top() + (plot_rect.height() * index / 5.0)
                painter.drawLine(
                    QtCore.QPointF(x, plot_rect.top()),
                    QtCore.QPointF(x, plot_rect.bottom()),
                )
                painter.drawLine(
                    QtCore.QPointF(plot_rect.left(), y),
                    QtCore.QPointF(plot_rect.right(), y),
                )
            painter.setPen(QtGui.QPen(border, 1))
            painter.drawRect(plot_rect)

        def _draw_axes(self, painter: Any, plot_rect: Any, bounds: LaneBounds) -> None:
            horizontal_label, vertical_label = self._axis_labels()
            muted = QtGui.QColor(self._palette["muted"])
            axis = QtGui.QColor(self._palette["text"])
            axis.setAlpha(170)
            painter.setPen(QtGui.QPen(axis, 1))
            painter.drawLine(plot_rect.bottomLeft(), plot_rect.bottomRight())
            painter.drawLine(plot_rect.bottomLeft(), plot_rect.topLeft())
            self._draw_zero_axes(painter, plot_rect, bounds)

            painter.setPen(muted)
            for ratio, value in (
                (0.0, bounds.min_x),
                (0.5, (bounds.min_x + bounds.max_x) / 2.0),
                (1.0, bounds.max_x),
            ):
                x = plot_rect.left() + (plot_rect.width() * ratio)
                painter.drawText(
                    QtCore.QRectF(x - 36.0, plot_rect.bottom() + 4.0, 72.0, 18.0),
                    QtCore.Qt.AlignmentFlag.AlignCenter,
                    self._axis_value(self._horizontal_axis_value(value)),
                )
            for ratio, value in (
                (0.0, bounds.max_y),
                (0.5, (bounds.min_y + bounds.max_y) / 2.0),
                (1.0, bounds.min_y),
            ):
                y = plot_rect.top() + (plot_rect.height() * ratio)
                painter.drawText(
                    QtCore.QRectF(2.0, y - 9.0, plot_rect.left() - 8.0, 18.0),
                    QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter,
                    self._axis_value(value),
                )
            painter.drawText(
                QtCore.QRectF(plot_rect.left(), plot_rect.bottom() + 24.0, plot_rect.width(), 18.0),
                QtCore.Qt.AlignmentFlag.AlignCenter,
                horizontal_label,
            )
            painter.drawText(
                QtCore.QRectF(6.0, plot_rect.top(), 24.0, plot_rect.height()),
                QtCore.Qt.AlignmentFlag.AlignCenter,
                vertical_label,
            )

        def _axis_labels(self) -> tuple[str, str]:
            return ("y", "x") if self._swap_xy else ("x", "y")

        def _horizontal_axis_value(self, value: float) -> float:
            return -value if self._swap_xy else value

        def _draw_zero_axes(self, painter: Any, plot_rect: Any, bounds: LaneBounds) -> None:
            zero_pen = QtGui.QPen(QtGui.QColor(self._palette["accent"]), 1)
            zero_pen.setStyle(QtCore.Qt.PenStyle.DashLine)
            painter.setPen(zero_pen)
            if bounds.min_x <= 0.0 <= bounds.max_x:
                ratio = (0.0 - bounds.min_x) / max(1e-9, bounds.max_x - bounds.min_x)
                x = plot_rect.left() + (plot_rect.width() * ratio)
                painter.drawLine(
                    QtCore.QPointF(x, plot_rect.top()),
                    QtCore.QPointF(x, plot_rect.bottom()),
                )
            if bounds.min_y <= 0.0 <= bounds.max_y:
                ratio = (bounds.max_y - 0.0) / max(1e-9, bounds.max_y - bounds.min_y)
                y = plot_rect.top() + (plot_rect.height() * ratio)
                painter.drawLine(
                    QtCore.QPointF(plot_rect.left(), y),
                    QtCore.QPointF(plot_rect.right(), y),
                )

        def _axis_value(self, value: float) -> str:
            if abs(value) >= 1000.0 or (0.0 < abs(value) < 0.01):
                return f"{value:.2e}"
            return f"{value:.2f}".rstrip("0").rstrip(".")

        def _draw_center_text(self, painter: Any, plot_rect: Any, text: str) -> None:
            painter.setPen(QtGui.QColor(self._palette["muted"]))
            painter.drawText(
                plot_rect,
                QtCore.Qt.AlignmentFlag.AlignCenter | QtCore.Qt.TextFlag.TextWordWrap,
                text,
            )

        def _draw_help_indicator(self, painter: Any, rect: Any) -> None:
            indicator_rect = self._help_indicator_rect(rect)
            fill = QtGui.QColor(self._palette["panel"])
            fill.setAlpha(215)
            border = QtGui.QColor(self._palette["border"])
            text = QtGui.QColor(self._palette["muted"])
            painter.setPen(QtGui.QPen(border, 1))
            painter.setBrush(fill)
            painter.drawEllipse(indicator_rect)
            painter.setPen(text)
            painter.drawText(
                indicator_rect,
                QtCore.Qt.AlignmentFlag.AlignCenter,
                "?",
            )

        def _help_indicator_rect(self, rect: Any) -> Any:
            size = 18.0
            return QtCore.QRectF(
                float(rect.right()) - size - 8.0,
                float(rect.top()) + 8.0,
                size,
                size,
            )

        def _point_mapper(
            self,
            plot_rect: Any,
            bounds: LaneBounds,
        ) -> Callable[[LanePoint], Any]:
            scale, x_pad, y_pad = self._plot_transform(plot_rect, bounds)

            def map_point(point: LanePoint) -> Any:
                horizontal, vertical = self._point_values(point)
                x = plot_rect.left() + x_pad + ((horizontal - bounds.min_x) * scale)
                y = plot_rect.top() + y_pad + ((bounds.max_y - vertical) * scale)
                return QtCore.QPointF(x, y)

            return map_point

        def _plot_transform(self, plot_rect: Any, bounds: LaneBounds) -> tuple[float, float, float]:
            data_width = max(1e-9, bounds.max_x - bounds.min_x)
            data_height = max(1e-9, bounds.max_y - bounds.min_y)
            scale = min(plot_rect.width() / data_width, plot_rect.height() / data_height)
            drawn_width = data_width * scale
            drawn_height = data_height * scale
            x_pad = (plot_rect.width() - drawn_width) / 2.0
            y_pad = (plot_rect.height() - drawn_height) / 2.0
            return scale, x_pad, y_pad

        def _point_values(self, point: LanePoint) -> tuple[float, float]:
            oriented = self._oriented_point(point)
            return oriented.x, oriented.y

        def _oriented_point(self, point: LanePoint) -> LanePoint:
            if self._swap_xy:
                return LanePoint(x=-point.y, y=point.x)
            return point

        def wheelEvent(self, event: Any) -> None:
            delta = event.angleDelta().y()
            if delta == 0 or self._active_bounds() is None:
                event.ignore()
                return
            self._zoom_at_plot_position(event.position(), delta)
            event.accept()

        def mousePressEvent(self, event: Any) -> None:
            if event.button() != QtCore.Qt.MouseButton.MiddleButton:
                super().mousePressEvent(event)
                return
            bounds = self._active_bounds()
            if bounds is None:
                event.ignore()
                return
            self._pan_start_pos = event.position()
            self._pan_start_bounds = bounds
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            event.accept()

        def mouseMoveEvent(self, event: Any) -> None:
            if self._pan_start_pos is None or self._pan_start_bounds is None:
                super().mouseMoveEvent(event)
                return
            delta = event.position() - self._pan_start_pos
            self._pan_view_by_pixels(delta.x(), delta.y())
            event.accept()

        def mouseReleaseEvent(self, event: Any) -> None:
            if event.button() == QtCore.Qt.MouseButton.MiddleButton and self._pan_start_pos is not None:
                self._pan_start_pos = None
                self._pan_start_bounds = None
                self.unsetCursor()
                event.accept()
                return
            super().mouseReleaseEvent(event)

        def mouseDoubleClickEvent(self, event: Any) -> None:
            if event.button() == QtCore.Qt.MouseButton.MiddleButton:
                self.reset_view()
                event.accept()
                return
            super().mouseDoubleClickEvent(event)

        def _zoom_at_plot_position(self, position: Any, delta_y: int) -> None:
            bounds = self._active_bounds()
            if bounds is None:
                return
            plot_rect = self._plot_rect(self.rect())
            anchor_x, anchor_y = self._data_at_position(plot_rect, bounds, position)
            factor = 0.88 ** (delta_y / 120.0)
            current_width = bounds.max_x - bounds.min_x
            current_height = bounds.max_y - bounds.min_y
            next_width = current_width * factor
            next_height = current_height * factor
            min_width, min_height = self._minimum_view_span()
            if next_width < min_width or next_height < min_height:
                return
            self._view_bounds = LaneBounds(
                min_x=anchor_x - ((anchor_x - bounds.min_x) * factor),
                max_x=anchor_x + ((bounds.max_x - anchor_x) * factor),
                min_y=anchor_y - ((anchor_y - bounds.min_y) * factor),
                max_y=anchor_y + ((bounds.max_y - anchor_y) * factor),
            )
            self._view_is_custom = True
            self.update()

        def _pan_view_by_pixels(self, dx: float, dy: float) -> None:
            if self._pan_start_bounds is None:
                return
            plot_rect = self._plot_rect(self.rect())
            scale, _x_pad, _y_pad = self._plot_transform(plot_rect, self._pan_start_bounds)
            delta_x = -dx / scale
            delta_y = dy / scale
            bounds = self._pan_start_bounds
            self._view_bounds = LaneBounds(
                min_x=bounds.min_x + delta_x,
                max_x=bounds.max_x + delta_x,
                min_y=bounds.min_y + delta_y,
                max_y=bounds.max_y + delta_y,
            )
            self._view_is_custom = True
            self.update()

        def _data_at_position(
            self,
            plot_rect: Any,
            bounds: LaneBounds,
            position: Any,
        ) -> tuple[float, float]:
            scale, x_pad, y_pad = self._plot_transform(plot_rect, bounds)
            x = bounds.min_x + ((position.x() - plot_rect.left() - x_pad) / scale)
            y = bounds.max_y - ((position.y() - plot_rect.top() - y_pad) / scale)
            return x, y

        def _minimum_view_span(self) -> tuple[float, float]:
            bounds = self._data_bounds
            if bounds is None:
                return 1e-9, 1e-9
            return (
                max(1e-9, (bounds.max_x - bounds.min_x) * 0.002),
                max(1e-9, (bounds.max_y - bounds.min_y) * 0.002),
            )

        def _draw_frame(
            self,
            painter: Any,
            map_point: Callable[[LanePoint], Any],
            series: LaneSeries,
            frame: LaneFrame,
        ) -> None:
            if not frame.points:
                return
            color = self._series_color(series.role)
            painter.setPen(QtGui.QPen(color, 2))
            if len(frame.points) > 1:
                path = QtGui.QPainterPath(map_point(frame.points[0]))
                for point in frame.points[1:]:
                    path.lineTo(map_point(point))
                painter.drawPath(path)
            painter.setBrush(color)
            painter.setPen(QtGui.QPen(color, 1))
            for point in frame.points:
                painter.drawEllipse(map_point(point), 2.8, 2.8)

        def _draw_legend(
            self,
            painter: Any,
            plot_rect: Any,
            frame_items: list[tuple[LaneSeries, LaneFrame]],
        ) -> None:
            top = plot_rect.top() + 8.0
            left = plot_rect.left() + 8.0
            painter.setPen(QtGui.QColor(self._palette["text"]))
            if self._timestamp_ns is not None:
                painter.drawText(
                    QtCore.QRectF(left, 0, plot_rect.width() - 20, 18),
                    QtCore.Qt.AlignmentFlag.AlignRight,
                    f"{self._timestamp_ns} ns",
                )
            for index, (series, frame) in enumerate(frame_items):
                y = top + 2.0 + (index * 20.0)
                color = self._series_color(series.role)
                painter.setBrush(color)
                painter.setPen(QtGui.QPen(color, 1))
                painter.drawEllipse(QtCore.QPointF(left + 5.0, y + 7.0), 4.0, 4.0)
                painter.setPen(QtGui.QColor(self._palette["text"]))
                painter.drawText(
                    QtCore.QRectF(left + 16.0, y, 180.0, 18.0),
                    QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter,
                    f"{series.role}: {len(frame.points)} pts",
                )

        def _series_color(self, role: str) -> Any:
            colors = LANE_LIGHT_COLORS if QtGui.QColor(self._palette["viewer_bg"]).lightness() > 128 else LANE_COLORS
            color = QtGui.QColor(colors[role])
            color.setAlphaF(self._display_settings.get(role, (True, 1.0, 0.0))[1])
            return color

        def resizeEvent(self, event: Any) -> None:
            super().resizeEvent(event)
            if not self._view_is_custom:
                self._fit_auto_view()

        def event(self, event: Any) -> bool:
            if event.type() == QtCore.QEvent.Type.ToolTip:
                if self._help_indicator_rect(self.rect()).contains(QtCore.QPointF(event.pos())):
                    QtWidgets.QToolTip.showText(event.globalPos(), LANE_PLOT_HELP_TEXT, self)
                    return True
                QtWidgets.QToolTip.hideText()
                event.ignore()
                return True
            return super().event(event)

    class LaneOverlayPanel(QtWidgets.QWidget):
        def __init__(
            self,
            parent: Any | None = None,
            on_selection_changed: Callable[[], None] | None = None,
            on_axes_changed: Callable[[bool], None] | None = None,
        ) -> None:
            super().__init__(parent)
            self._on_selection_changed = on_selection_changed
            self._on_axes_changed = on_axes_changed
            self._topics_by_role: dict[str, Any] = {}
            self._data: LaneOverlayData | None = None

            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(8, 8, 8, 8)
            layout.setSpacing(8)

            check_row = QtWidgets.QHBoxLayout()
            check_row.setContentsMargins(0, 0, 0, 0)
            self.checkboxes: dict[str, Any] = {}
            for role in LANE_ROLES:
                checkbox = QtWidgets.QCheckBox(role)
                checkbox.setObjectName(f"lane_{role}_check")
                checkbox.setEnabled(False)
                checkbox.setToolTip(f"Show {role} lane line points")
                checkbox.toggled.connect(self._selection_changed)
                self.checkboxes[role] = checkbox
                check_row.addWidget(checkbox)
            self.swap_axes_button = QtWidgets.QToolButton()
            self.swap_axes_button.setText("XY")
            self.swap_axes_button.setCheckable(True)
            self.swap_axes_button.setToolTip("Swap x/y axes")
            self.swap_axes_button.toggled.connect(self._axes_changed)
            check_row.addWidget(self.swap_axes_button)
            self.fit_button = QtWidgets.QToolButton()
            self.fit_button.setAccessibleName("Fit lane lines")
            self.fit_button.setToolTip("Fit current lane points to the view")
            self.fit_button.setFixedSize(28, 26)
            self.fit_button.clicked.connect(lambda: self.plot.reset_view())
            check_row.addWidget(self.fit_button)
            self.help_button = QtWidgets.QToolButton()
            self.help_button.setObjectName("lanePlotHelpIndicator")
            self.help_button.setText("?")
            self.help_button.setAutoRaise(True)
            self.help_button.setToolTip(LANE_PLOT_HELP_TEXT)
            check_row.addWidget(self.help_button)
            check_row.addStretch(1)
            layout.addLayout(check_row)

            self.status_label = QtWidgets.QLabel("Open a bag with lane line PointCloud2 topics.")
            self.status_label.setWordWrap(True)
            layout.addWidget(self.status_label)

            self.plot = LanePlotWidget(self)
            layout.addWidget(self.plot, 1)

        @property
        def current_timestamp_ns(self) -> int | None:
            return self.plot.current_timestamp_ns

        def apply_theme(self, palette: dict[str, str]) -> None:
            self.status_label.setStyleSheet(f"color: {palette['muted']};")
            self.plot.apply_theme(palette)
            self.fit_button.setIcon(tinted_standard_icon(QtGui, self, QtWidgets.QStyle.StandardPixmap.SP_BrowserReload, palette))

        def set_swap_xy(self, swapped: bool) -> None:
            self.swap_axes_button.blockSignals(True)
            self.swap_axes_button.setChecked(swapped)
            self.swap_axes_button.blockSignals(False)
            self.plot.set_swap_xy(swapped)

        def set_topics(self, topics_by_role: dict[str, Any]) -> None:
            self._topics_by_role = dict(topics_by_role)
            self._data = None
            for role, checkbox in self.checkboxes.items():
                checkbox.blockSignals(True)
                checkbox.setEnabled(role in self._topics_by_role)
                checkbox.setChecked(role in self._topics_by_role)
                checkbox.blockSignals(False)
            self.plot.set_data(None)
            self.plot.set_visible_roles(self.visible_roles())
            if self._topics_by_role:
                roles = ", ".join(self._topics_by_role)
                self.status_label.setText(f"Detected lane topics: {roles}.")
            else:
                self.status_label.setText("No lane line topics found in this bag.")

        def set_loading(self) -> None:
            self.status_label.setText("Loading lane line frames...")

        def set_error(self, message: str) -> None:
            self.status_label.setText(message)

        def set_data(self, data: LaneOverlayData) -> None:
            self._data = data
            self.plot.set_data(data)
            self.plot.set_visible_roles(self.visible_roles())
            if not data.series_by_role:
                self.status_label.setText("No lane line frames were loaded.")
                return
            pieces = [
                f"{series.role}: {len(series.frames)} frames"
                for series in data.ordered_series(LANE_ROLES)
            ]
            self.status_label.setText("; ".join(pieces))

        def show_at_timestamp(self, timestamp_ns: int | None) -> None:
            self.plot.show_at_timestamp(timestamp_ns)

        def visible_roles(self) -> list[str]:
            return [
                role
                for role in LANE_ROLES
                if self.checkboxes[role].isEnabled() and self.checkboxes[role].isChecked()
            ]

        def _selection_changed(self, _checked: bool) -> None:
            self.plot.set_visible_roles(self.visible_roles())
            if self._on_selection_changed is not None:
                self._on_selection_changed()

        def _axes_changed(self, checked: bool) -> None:
            self.plot.set_swap_xy(checked)
            if self._on_axes_changed is not None:
                self._on_axes_changed(checked)

    LaneOverlayPanel.PlotWidget = LanePlotWidget
    return LaneOverlayPanel
