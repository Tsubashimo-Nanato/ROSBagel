from __future__ import annotations

from typing import Any

import numpy as np


def create_point_plot(QtWidgets: Any) -> Any:
    from PySide6 import QtCore, QtGui

    class PointPlot(QtWidgets.QWidget):
        """Software XY projection for platforms without an OpenGL surface."""

        def __init__(self) -> None:
            super().__init__()
            self._clouds: list[Any] = []
            self._bounds = (-1.0, 1.0, -1.0, 1.0)
            self._auto_fit = True
            self._pan_position: Any = None
            self._palette = {"viewer_bg": "#101010", "text": "#eeeeee", "border": "#4a4a4a"}
            self.setMinimumSize(160, 120)
            self.setToolTip("XY projection\nMiddle-drag: pan\nWheel: zoom\nDouble-click: fit points")

        def widget(self) -> Any:
            return self

        def apply_theme(self, palette: dict[str, str]) -> None:
            self._palette = palette
            self.update()

        def set_clouds(self, clouds: list[Any]) -> None:
            self._clouds = clouds
            if self._auto_fit:
                self._fit()
            self.update()

        def reset_view(self) -> None:
            self._auto_fit = True
            self._fit()
            self.update()

        def _plot_rect(self) -> Any:
            return QtCore.QRectF(self.rect()).adjusted(48, 28, -18, -32)

        def _fit(self) -> None:
            arrays = [np.asarray(cloud.points).reshape(-1, 3)[:, :2] for cloud, _ in self._clouds if cloud.preview_point_count]
            if not arrays:
                self._bounds = (-1.0, 1.0, -1.0, 1.0)
                return
            points = np.concatenate(arrays)
            points = points[np.isfinite(points).all(axis=1)]
            if not len(points):
                return
            mins, maxs = points.min(axis=0), points.max(axis=0)
            center = (mins + maxs) / 2
            spans = np.maximum(maxs - mins, max(float((maxs - mins).max()), 1.0) * 0.1) * 1.1
            rect = self._plot_rect()
            scale = min(max(1.0, rect.width()) / spans[0], max(1.0, rect.height()) / spans[1])
            width, height = max(1, rect.width()) / scale, max(1, rect.height()) / scale
            self._bounds = (center[0] - width / 2, center[0] + width / 2,
                            center[1] - height / 2, center[1] + height / 2)

        def paintEvent(self, _event: Any) -> None:
            painter = QtGui.QPainter(self)
            painter.fillRect(self.rect(), QtGui.QColor(self._palette["viewer_bg"]))
            rect = self._plot_rect()
            if rect.width() <= 0 or rect.height() <= 0:
                return
            xmin, xmax, ymin, ymax = self._bounds
            painter.setPen(QtGui.QColor(self._palette["border"]))
            painter.drawRect(rect)
            for index in range(1, 5):
                x = rect.left() + rect.width() * index / 5
                y = rect.top() + rect.height() * index / 5
                painter.drawLine(QtCore.QPointF(x, rect.top()), QtCore.QPointF(x, rect.bottom()))
                painter.drawLine(QtCore.QPointF(rect.left(), y), QtCore.QPointF(rect.right(), y))
            painter.setPen(QtGui.QColor(self._palette["text"]))
            painter.drawText(QtCore.QPointF(rect.left(), rect.bottom() + 19), f"{xmin:.3g}")
            painter.drawText(QtCore.QPointF(rect.right() - 45, rect.bottom() + 19), f"{xmax:.3g}")
            painter.drawText(QtCore.QPointF(rect.center().x(), rect.bottom() + 19), "x")
            painter.drawText(QtCore.QPointF(4, rect.top() + 10), f"{ymax:.3g}")
            painter.drawText(QtCore.QPointF(4, rect.bottom()), f"{ymin:.3g}")
            painter.drawText(QtCore.QPointF(8, rect.center().y()), "y")
            painter.drawText(QtCore.QPointF(rect.left(), 18), "XY projection")
            painter.save()
            painter.setClipRect(rect)
            for cloud, settings in self._clouds:
                points = np.asarray(cloud.points).reshape(-1, 3)
                points = points[np.isfinite(points).all(axis=1)]
                color = QtGui.QColor(settings.color)
                color.setAlphaF(settings.opacity)
                pen = QtGui.QPen(color, settings.point_size)
                pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                polygon = QtGui.QPolygonF([
                    QtCore.QPointF(rect.left() + (float(x) - xmin) / (xmax - xmin) * rect.width(),
                                  rect.bottom() - (float(y) - ymin) / (ymax - ymin) * rect.height())
                    for x, y, _z in points
                ])
                painter.drawPoints(polygon)
            painter.restore()

        def wheelEvent(self, event: Any) -> None:
            rect = self._plot_rect()
            if not rect.contains(event.position()) or event.angleDelta().y() == 0:
                event.ignore()
                return
            factor = 1.2 ** (-event.angleDelta().y() / 120)
            xmin, xmax, ymin, ymax = self._bounds
            width, height = xmax - xmin, ymax - ymin
            if not 1e-8 < width * factor < 1e12:
                return
            x = xmin + (event.position().x() - rect.left()) / rect.width() * width
            y = ymax - (event.position().y() - rect.top()) / rect.height() * height
            self._bounds = (x + (xmin - x) * factor, x + (xmax - x) * factor,
                            y + (ymin - y) * factor, y + (ymax - y) * factor)
            self._auto_fit = False
            self.update()
            event.accept()

        def mousePressEvent(self, event: Any) -> None:
            if event.button() == QtCore.Qt.MouseButton.MiddleButton:
                self._pan_position = event.position()
                self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
                event.accept()
            else:
                super().mousePressEvent(event)

        def mouseMoveEvent(self, event: Any) -> None:
            if self._pan_position is None:
                return
            delta = event.position() - self._pan_position
            rect = self._plot_rect()
            xmin, xmax, ymin, ymax = self._bounds
            dx = delta.x() / max(1, rect.width()) * (xmax - xmin)
            dy = delta.y() / max(1, rect.height()) * (ymax - ymin)
            self._bounds = (xmin - dx, xmax - dx, ymin + dy, ymax + dy)
            self._pan_position = event.position()
            self._auto_fit = False
            self.update()

        def mouseReleaseEvent(self, event: Any) -> None:
            if event.button() == QtCore.Qt.MouseButton.MiddleButton:
                self._pan_position = None
                self.unsetCursor()
                event.accept()
            else:
                super().mouseReleaseEvent(event)

        def mouseDoubleClickEvent(self, event: Any) -> None:
            self.reset_view()
            event.accept()

        def resizeEvent(self, event: Any) -> None:
            if self._auto_fit:
                self._fit()
            elif event.oldSize().isValid():
                xmin, xmax, ymin, ymax = self._bounds
                old_rect = QtCore.QRectF(QtCore.QPointF(), QtCore.QSizeF(event.oldSize())).adjusted(48, 28, -18, -32)
                units_per_pixel = max((xmax - xmin) / max(1, old_rect.width()),
                                      (ymax - ymin) / max(1, old_rect.height()))
                rect = self._plot_rect()
                half_width = max(1, rect.width()) * units_per_pixel / 2
                half_height = max(1, rect.height()) * units_per_pixel / 2
                x, y = (xmin + xmax) / 2, (ymin + ymax) / 2
                self._bounds = (x - half_width, x + half_width, y - half_height, y + half_height)
            super().resizeEvent(event)

    return PointPlot()
