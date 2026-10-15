from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rosbagel.core.analysis import adjacent_timestamp, message_fields
from rosbagel.core.decoder import message_to_plain


def create_inspector(viewer: Any) -> Any:
    QtCore, QtWidgets = viewer.QtCore, viewer.QtWidgets

    class Inspector(QtWidgets.QWidget):
        def __init__(self):
            super().__init__()
            self.record = None
            self.rows = []
            self.request = None
            layout = QtWidgets.QVBoxLayout(self)
            top = QtWidgets.QHBoxLayout()
            self.topic_box = QtWidgets.QComboBox()
            self.topic_box.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            self.topic_box.setAccessibleName("Inspect topic")
            top.addWidget(self.topic_box, 1)
            self.previous = QtWidgets.QToolButton()
            self.previous.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_ArrowBack))
            self.previous.setProperty("standardIcon", QtWidgets.QStyle.StandardPixmap.SP_ArrowBack.value)
            self.previous.setToolTip("Previous message")
            self.previous.clicked.connect(lambda: self.step(-1))
            top.addWidget(self.previous)
            self.next = QtWidgets.QToolButton()
            self.next.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_ArrowForward))
            self.next.setProperty("standardIcon", QtWidgets.QStyle.StandardPixmap.SP_ArrowForward.value)
            self.next.setToolTip("Next message")
            self.next.clicked.connect(lambda: self.step(1))
            top.addWidget(self.next)
            self.save_button = QtWidgets.QPushButton("Save message...")
            self.save_button.clicked.connect(self.save_message)
            top.addWidget(self.save_button)
            layout.addLayout(top)
            self.status = QtWidgets.QLabel("No message")
            self.status.setWordWrap(True)
            layout.addWidget(self.status)
            self.search = QtWidgets.QLineEdit()
            self.search.setPlaceholderText("Search fields")
            self.search.setClearButtonEnabled(True)
            self.search.textChanged.connect(self.filter_fields)
            layout.addWidget(self.search)
            self.tabs = QtWidgets.QTabWidget()
            self.table = QtWidgets.QTableWidget(0, 3)
            self.table.setHorizontalHeaderLabels(("Field", "Type", "Value"))
            self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
            self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
            self.table.horizontalHeader().setStretchLastSection(True)
            self.table.setColumnWidth(0, 220)
            self.table.setColumnWidth(1, 70)
            self.tabs.addTab(self.table, "Fields")
            self.raw = QtWidgets.QPlainTextEdit()
            self.raw.setReadOnly(True)
            self.tabs.addTab(self.raw, "Raw bytes")
            layout.addWidget(self.tabs, 1)
            self.plot_button = QtWidgets.QPushButton("Plot selected fields")
            self.plot_button.clicked.connect(self.plot_fields)
            self.table.itemSelectionChanged.connect(self.update_actions)
            self.table.itemDoubleClicked.connect(lambda _item: self.plot_fields())
            layout.addWidget(self.plot_button)
            self.topic_box.currentTextChanged.connect(lambda _text: self.reload())
            self.update_actions()

        def set_topics(self, topics):
            self.clear()
            blocker = QtCore.QSignalBlocker(self.topic_box)
            self.topic_box.clear()
            self.topic_box.addItems([topic.name for topic in topics])
            del blocker

        def clear(self):
            viewer._io_tasks.cancel("inspector")
            viewer._io_tasks.cancel("message-step")
            self.request = None
            self.record = None
            self.rows = []
            self.table.setRowCount(0)
            self.raw.clear()
            self.status.setText("No message")
            self.update_actions()

        def reload(self, stamp=None):
            stamp = viewer._current_timestamp_ns() if stamp is None else stamp
            topic = self.topic_box.currentText()
            if not topic or stamp is None or viewer.preview is None or viewer._opening:
                return
            key = (topic, stamp)
            if key == self.request:
                return
            self.request = key
            viewer._io_tasks.cancel("message-step")
            self.record = None
            self.update_actions()
            preview = viewer.preview
            self.status.setText(f"Loading {topic}")

            def work(ctx):
                record = preview.nearest_record(topic, stamp, cancellation_token=ctx)
                ctx.throw_if_cancelled()
                return record, message_fields(record.decoded) if record and record.decoded is not None else []

            def finish(result):
                self.record, self.rows = result
                selected = {self.table.item(index.row(), 0).text() for index in self.table.selectionModel().selectedRows()}
                self.table.setRowCount(len(self.rows))
                for row, field in enumerate(self.rows):
                    for column, text in enumerate((field.path, field.type_name, field.text)):
                        item = QtWidgets.QTableWidgetItem(text)
                        item.setToolTip(text)
                        self.table.setItem(row, column, item)
                    if field.path in selected:
                        for column in range(3):
                            self.table.item(row, column).setSelected(True)
                if self.record is None:
                    self.status.setText("No messages in this topic")
                    self.raw.clear()
                else:
                    delta = (self.record.timestamp_ns - stamp) / 1e6
                    self.status.setText(f"{self.record.msgtype}\n{self.record.timestamp_ns} ns | delta {delta:+.3f} ms | {len(self.rows)} fields")
                    raw = self.record.raw or b""
                    self.raw.setPlainText(f"{len(raw):,} bytes | first {min(4096, len(raw)):,} shown\n" + raw[:4096].hex(" "))
                self.filter_fields(self.search.text())
                self.update_actions()

            def failed(message):
                self.request = None
                self.record = None
                self.status.setText(f"Inspect failed: {message}")
                self.update_actions()

            viewer._io_tasks.submit("inspector", work, finish, failed)

        def filter_fields(self, text):
            query = text.casefold()
            for index, row in enumerate(self.rows):
                self.table.setRowHidden(index, query not in f"{row.path} {row.text}".casefold())

        def selected_numeric(self):
            return [self.rows[index.row()].path for index in self.table.selectionModel().selectedRows()
                    if index.row() < len(self.rows) and self.rows[index.row()].numeric]

        def update_actions(self):
            self.table.setEnabled(self.record is not None)
            self.plot_button.setEnabled(self.record is not None and bool(self.selected_numeric()))
            for control in (self.save_button, self.previous, self.next):
                control.setEnabled(self.record is not None)

        def plot_fields(self):
            for field in self.selected_numeric():
                viewer.workflows.add_chart_field(self.topic_box.currentText(), field)

        def step(self, direction):
            if self.record is None or viewer.session.reader is None:
                return
            reader, topic, stamp = viewer.session.reader, self.record.topic, self.record.timestamp_ns

            def finish(target):
                if target is None:
                    self.status.setText("No earlier message" if direction < 0 else "No later message")
                    return
                viewer._pause_playback()
                viewer._set_timeline_seconds((target - viewer._bag_start_ns) / 1e9, immediate=True)
                self.reload(target)

            viewer._io_tasks.submit("message-step", lambda ctx: adjacent_timestamp(reader, topic, stamp, direction, ctx),
                                    finish, lambda error: self.status.setText(error))

        def save_message(self):
            if self.record is None:
                return
            path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save message", "message.json", "JSON (*.json)")
            if not path:
                return
            record = self.record

            def work(ctx):
                ctx.throw_if_cancelled()
                payload = {"topic": record.topic, "msgtype": record.msgtype, "timestamp_ns": record.timestamp_ns,
                           "message": message_to_plain(record.decoded), "raw": message_to_plain(record.raw)}
                Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

            viewer._io_tasks.submit("save-message", work, lambda _: viewer._log(f"Saved message to {path}"), viewer._show_warning)

    return Inspector()
