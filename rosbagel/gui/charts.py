from __future__ import annotations

from bisect import bisect_left
import csv
from pathlib import Path
from typing import Any

from rosbagel.core.analysis import load_numeric_curves, message_fields
from rosbagel.gui.theme import theme_palette


COLORS = ("#f0aa32", "#3b96e8", "#ed6572", "#30b599", "#bd80dc", "#abbd44")
LIGHT_COLORS = ("#986200", "#0063b5", "#ae2440", "#067861", "#7943aa", "#657515")


def create_chart_panel(viewer: Any) -> Any:
    QtCore, QtGui, QtWidgets = viewer.QtCore, viewer.QtGui, viewer.QtWidgets

    class TimePlot(QtWidgets.QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.setMinimumSize(280, 180)
            self.setMouseTracking(True)
            self.setToolTip("Click: move timeline\nMiddle-drag: pan\nWheel: zoom\nDouble-click: fit")
            self.curves = []
            self.paths = []
            self.origin_ns = 0
            self.cursor_ns = None
            self.bounds = (0.0, 1.0, -1.0, 1.0)
            self.pan = None

        def plot_rect(self):
            return QtCore.QRectF(self.rect()).adjusted(66, 18 + 18 * ((len(self.curves) + 1) // 2), -20, -36)

        def set_curves(self, curves, origin_ns):
            self.curves, self.origin_ns = curves, origin_ns
            self.setMinimumHeight(160 + 18 * ((len(curves) + 1) // 2))
            self.paths = []
            for curve in curves:
                path = QtGui.QPainterPath()
                for index, (stamp, value) in enumerate(zip(curve.timestamps_ns, curve.values)):
                    point = QtCore.QPointF((stamp - origin_ns) / 1e9, value)
                    path.moveTo(point) if index == 0 else path.lineTo(point)
                self.paths.append(path)
            self.fit()

        def fit(self):
            stamps = [stamp for curve in self.curves for stamp in curve.timestamps_ns]
            values = [value for curve in self.curves for value in curve.values]
            if values:
                xmin, xmax = (min(stamps) - self.origin_ns) / 1e9, (max(stamps) - self.origin_ns) / 1e9
                ymin, ymax = min(values), max(values)
                dx, dy = max(xmax - xmin, 0.1) * .05, max(ymax - ymin, abs(ymin) * .01, .1) * .08
                self.bounds = (xmin - dx, xmax + dx, ymin - dy, ymax + dy)
            else:
                self.bounds = (0., 1., -1., 1.)
            self.update()

        def paintEvent(self, event):
            palette = theme_palette(viewer._theme)
            painter = QtGui.QPainter(self)
            painter.fillRect(self.rect(), QtGui.QColor(palette["viewer_bg"]))
            rect = self.plot_rect()
            xmin, xmax, ymin, ymax = self.bounds
            colors = COLORS if viewer._theme == "dark" else LIGHT_COLORS
            for index, curve in enumerate(self.curves):
                column_width = max(80, (self.width() - 30) / 2)
                x, y = 12 + index % 2 * column_width, 4 + index // 2 * 18
                painter.fillRect(QtCore.QRectF(x, y + 5, 8, 8), QtGui.QColor(colors[index % len(colors)]))
                painter.setPen(QtGui.QColor(palette["text"]))
                label = f"{curve.topic.rsplit('/', 1)[-1]} / {curve.field}"
                label = painter.fontMetrics().elidedText(label, QtCore.Qt.TextElideMode.ElideRight, int(column_width - 25))
                painter.drawText(QtCore.QRectF(x + 14, y, column_width - 20, 18), QtCore.Qt.AlignmentFlag.AlignVCenter, label)
            for index in range(6):
                x = rect.left() + rect.width() * index / 5
                y = rect.bottom() - rect.height() * index / 5
                painter.setPen(QtGui.QColor(palette["border"]))
                painter.drawLine(QtCore.QPointF(x, rect.top()), QtCore.QPointF(x, rect.bottom()))
                painter.drawLine(QtCore.QPointF(rect.left(), y), QtCore.QPointF(rect.right(), y))
                painter.setPen(QtGui.QColor(palette["muted"]))
                painter.drawText(QtCore.QRectF(x - 28, rect.bottom() + 5, 56, 18), QtCore.Qt.AlignmentFlag.AlignCenter, f"{xmin + (xmax - xmin) * index / 5:.3g}")
                painter.drawText(QtCore.QRectF(2, y - 9, 59, 18), QtCore.Qt.AlignmentFlag.AlignRight, f"{ymin + (ymax - ymin) * index / 5:.3g}")
            painter.drawText(QtCore.QPointF(rect.center().x() - 20, self.height() - 3), "Time (s)")
            painter.save()
            painter.setClipRect(rect)
            transform = QtGui.QTransform()
            transform.translate(rect.left(), rect.bottom())
            transform.scale(rect.width() / (xmax - xmin), -rect.height() / (ymax - ymin))
            transform.translate(-xmin, -ymin)
            painter.setTransform(transform)
            for index, path in enumerate(self.paths):
                pen = QtGui.QPen(QtGui.QColor(colors[index % len(colors)]), 1.5)
                pen.setCosmetic(True)
                painter.setPen(pen)
                painter.drawPath(path)
                if path.elementCount() == 1:
                    pen.setWidthF(4)
                    painter.setPen(pen)
                    painter.drawPoint(path.currentPosition())
            painter.restore()
            if self.cursor_ns is not None:
                x = rect.left() + ((self.cursor_ns - self.origin_ns) / 1e9 - xmin) / (xmax - xmin) * rect.width()
                if rect.left() <= x <= rect.right():
                    painter.setPen(QtGui.QPen(QtGui.QColor(palette["accent"]), 1, QtCore.Qt.PenStyle.DashLine))
                    painter.drawLine(QtCore.QPointF(x, rect.top()), QtCore.QPointF(x, rect.bottom()))
            if not any(curve.values for curve in self.curves):
                painter.setPen(QtGui.QColor(palette["muted"]))
                painter.drawText(rect, QtCore.Qt.AlignmentFlag.AlignCenter, "No numeric samples")

        def stamp_at(self, position):
            rect = self.plot_rect()
            xmin, xmax, _ymin, _ymax = self.bounds
            return self.origin_ns + round((xmin + (position.x() - rect.left()) / rect.width() * (xmax - xmin)) * 1e9)

        def mousePressEvent(self, event):
            if not self.plot_rect().contains(event.position()):
                return
            if event.button() == QtCore.Qt.MouseButton.MiddleButton:
                self.pan = event.position()
                self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            elif event.button() == QtCore.Qt.MouseButton.LeftButton:
                viewer._pause_playback()
                viewer._set_timeline_seconds((self.stamp_at(event.position()) - (viewer._bag_start_ns or 0)) / 1e9, immediate=True)

        def mouseMoveEvent(self, event):
            if self.pan is not None:
                delta = event.position() - self.pan
                rect = self.plot_rect()
                xmin, xmax, ymin, ymax = self.bounds
                dx, dy = delta.x() * (xmax - xmin) / rect.width(), delta.y() * (ymax - ymin) / rect.height()
                self.bounds = (xmin - dx, xmax - dx, ymin + dy, ymax + dy)
                self.pan = event.position()
                self.update()
                return
            stamp = self.stamp_at(event.position())
            rows = [f"{(stamp - self.origin_ns) / 1e9:.3f} s"]
            for curve in self.curves:
                if not curve.timestamps_ns:
                    continue
                index = min(bisect_left(curve.timestamps_ns, stamp), len(curve.values) - 1)
                if index and abs(curve.timestamps_ns[index - 1] - stamp) < abs(curve.timestamps_ns[index] - stamp):
                    index -= 1
                rows.append(f"{curve.topic} / {curve.field}: {curve.values[index]:.6g}")
            QtWidgets.QToolTip.showText(event.globalPosition().toPoint(), "\n".join(rows), self)

        def mouseReleaseEvent(self, event):
            self.pan = None
            self.unsetCursor()

        def mouseDoubleClickEvent(self, event):
            self.fit()

        def wheelEvent(self, event):
            rect = self.plot_rect()
            if not rect.contains(event.position()) or event.angleDelta().y() == 0:
                return
            factor = 1.2 ** (-event.angleDelta().y() / 120)
            xmin, xmax, ymin, ymax = self.bounds
            if not 1e-8 < (xmax - xmin) * factor < 1e12:
                return
            x = (self.stamp_at(event.position()) - self.origin_ns) / 1e9
            y = ymax - (event.position().y() - rect.top()) / rect.height() * (ymax - ymin)
            self.bounds = (x + (xmin - x) * factor, x + (xmax - x) * factor,
                           y + (ymin - y) * factor, y + (ymax - y) * factor)
            self.update()

    class ChartPanel(QtWidgets.QWidget):
        def __init__(self):
            super().__init__()
            self.selections = []
            self.task = None
            layout = QtWidgets.QVBoxLayout(self)
            controls = QtWidgets.QHBoxLayout()
            self.series_box = QtWidgets.QComboBox()
            self.series_box.setMinimumWidth(140)
            self.series_box.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            self.series_box.setAccessibleName("Chart series")
            controls.addWidget(self.series_box, 1)
            for text, tooltip, callback in (("+", "Add numeric field", self.add_field_dialog),
                                             ("-", "Remove selected series", self.remove_series),
                                             ("Fit", "Fit all samples", lambda: self.plot.fit()),
                                             ("PNG", "Save chart image", self.save_image),
                                             ("CSV", "Save plotted samples", self.save_csv)):
                button = QtWidgets.QToolButton()
                button.setText(text)
                symbols = {"Fit": QtWidgets.QStyle.StandardPixmap.SP_BrowserReload,
                           "PNG": QtWidgets.QStyle.StandardPixmap.SP_DialogSaveButton,
                           "CSV": QtWidgets.QStyle.StandardPixmap.SP_FileDialogDetailedView}
                if text in symbols:
                    button.setText("")
                    button.setIcon(self.style().standardIcon(symbols[text]))
                    if text == "Fit":
                        button.setProperty("standardIcon", symbols[text].value)
                button.setToolTip(tooltip)
                button.setAccessibleName(tooltip)
                button.clicked.connect(callback)
                controls.addWidget(button)
            self.load_button = QtWidgets.QPushButton("Load")
            self.load_button.clicked.connect(self.toggle_load)
            controls.addWidget(self.load_button)
            layout.addLayout(controls)
            self.plot = TimePlot()
            layout.addWidget(self.plot, 1)
            self.status = QtWidgets.QLabel("No fields selected")
            self.status.setWordWrap(True)
            layout.addWidget(self.status)

        def add_field_dialog(self):
            if viewer.preview is None:
                return
            dialog = QtWidgets.QDialog(viewer.window)
            dialog.setWindowTitle("Add numeric field")
            dialog.resize(620, 170)
            layout = QtWidgets.QFormLayout(dialog)
            topic_box = QtWidgets.QComboBox()
            topic_box.addItems([topic.name for topic in viewer.session.topics])
            topic_box.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            topic_box.setCurrentText(viewer.workflows.inspector.topic_box.currentText())
            field_box = QtWidgets.QComboBox()
            field_box.setEditable(True)
            field_box.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
            field_box.setAccessibleName("Numeric field path")
            status = QtWidgets.QLabel()
            layout.addRow("Topic", topic_box)
            layout.addRow("Field path", field_box)
            layout.addRow(status)
            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
            layout.addRow(buttons)
            key = f"chart-field-picker-{id(dialog)}"
            preview, stamp = viewer.preview, viewer._current_timestamp_ns()

            def discover():
                name = topic_box.currentText()
                field_box.clear()
                status.setText("Reading available fields...")

                def work(ctx):
                    record = preview.nearest_record(name, stamp, cancellation_token=ctx)
                    return [row.path for row in message_fields(record.decoded) if row.numeric] if record and record.decoded is not None else []

                def finish(paths):
                    typed = field_box.currentText()
                    field_box.clear()
                    field_box.addItems(paths)
                    if typed:
                        field_box.setEditText(typed)
                    status.setText(f"{len(paths)} numeric fields in the current message")

                viewer._io_tasks.submit(key, work, finish, status.setText)

            buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("Add")
            buttons.accepted.connect(lambda: dialog.accept() if field_box.currentText().strip() else None)
            buttons.rejected.connect(dialog.reject)
            topic_box.currentTextChanged.connect(lambda _: discover())
            dialog.finished.connect(lambda _: viewer._io_tasks.cancel(key))
            discover()
            if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
                self.add_series(topic_box.currentText(), field_box.currentText().strip())
            dialog.deleteLater()

        def add_series(self, topic, field):
            if (topic, field) in self.selections:
                return
            if len(self.selections) >= 16:
                viewer._show_warning("A chart supports at most 16 fields.")
                return
            self.selections.append((topic, field))
            self.refresh_series()
            self.load()

        def refresh_series(self):
            current = self.series_box.currentIndex()
            self.series_box.clear()
            colors = COLORS if viewer._theme == "dark" else LIGHT_COLORS
            for index, (topic, field) in enumerate(self.selections):
                pixmap = QtGui.QPixmap(12, 12)
                pixmap.fill(QtGui.QColor(colors[index % len(colors)]))
                self.series_box.addItem(QtGui.QIcon(pixmap), f"{topic} / {field}")
            if 0 <= current < self.series_box.count():
                self.series_box.setCurrentIndex(current)

        def remove_series(self):
            index = self.series_box.currentIndex()
            if index >= 0:
                self.selections.pop(index)
                self.refresh_series()
                self.load()

        def clear(self):
            viewer._io_tasks.cancel("chart")
            self.task = None
            self.selections = []
            self.refresh_series()
            self.plot.set_curves([], 0)
            self.load_button.setText("Load")
            self.status.setText("No fields selected")

        def toggle_load(self):
            if self.task is not None:
                self.task.context.cancel()
                self.status.setText("Cancelling chart load...")
            else:
                self.load()

        def load(self):
            if self.task is not None:
                self.task.context.cancel()
                viewer._io_tasks.cancel("chart")
                self.task = None
                self.load_button.setText("Load")
            if not self.selections or viewer.session.reader is None:
                self.plot.set_curves([], 0)
                self.status.setText("No fields selected")
                return
            if viewer._bag_start_ns is None or viewer._bag_end_ns is None:
                self.status.setText("Bag time bounds are unavailable; run Analysis > Scan bag.")
                return
            reader = viewer.session.reader
            selections = list(self.selections)
            start, end = viewer._bag_start_ns, viewer._bag_end_ns
            self.load_button.setText("Cancel")
            self.status.setText("Loading numeric fields...")

            def finish(curves):
                self.task = None
                self.load_button.setText("Load")
                self.plot.set_curves(curves, start)
                self.status.setText("; ".join(f"{curve.field}: {len(curve.values):,}/{curve.sample_count:,} samples" if curve.sample_count else
                                             f"{curve.field}: no numeric samples" for curve in curves))

            def failed(message):
                self.task = None
                self.load_button.setText("Load")
                self.status.setText(message)

            self.task = viewer._io_tasks.submit("chart", lambda ctx: load_numeric_curves(reader, selections, start, end, ctx),
                finish, failed, on_progress=lambda label, value, total: self.status.setText(f"{label}: {value:,}"),
                on_cancel=lambda: failed("Chart load cancelled"))

        def show_at_timestamp(self, stamp):
            self.plot.cursor_ns = stamp
            self.plot.update()

        def save_image(self):
            path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save chart image", "chart.png", "PNG image (*.png)")
            if path and not self.plot.grab().save(path, "PNG"):
                viewer._show_warning(f"Could not save {path}")

        def save_csv(self):
            path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save plotted samples", "chart.csv", "CSV (*.csv)")
            if not path:
                return
            try:
                with Path(path).open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(("topic", "field", "timestamp_ns", "relative_time_sec", "value"))
                    for curve in self.plot.curves:
                        writer.writerows((curve.topic, curve.field, stamp, (stamp - self.plot.origin_ns) / 1e9, value)
                                         for stamp, value in zip(curve.timestamps_ns, curve.values))
                viewer._log(f"Saved chart samples to {path}")
            except OSError as exc:
                viewer._show_warning(f"Could not save chart: {exc}")

    return ChartPanel()
