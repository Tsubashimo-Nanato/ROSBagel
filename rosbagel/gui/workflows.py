from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from rosbagel.core.export_policy import compatible_export_formats
from rosbagel.core.manifest import write_manifest, write_topics_csv
from rosbagel.gui.charts import create_chart_panel
from rosbagel.gui.inspector import create_inspector
from rosbagel.gui.theme import theme_palette, tinted_standard_icon


class GuiWorkflows:
    def __init__(self, viewer: Any) -> None:
        self.viewer = viewer
        self.QtCore, self.QtWidgets = viewer.QtCore, viewer.QtWidgets
        self.pending_workspace = None
        self.scan_task = None
        self.inspector = create_inspector(viewer)
        self.chart = create_chart_panel(viewer)
        self.report = self._build_report()
        self.inspector_dock = viewer._make_dock("Message inspector", self.inspector, self.QtCore.Qt.DockWidgetArea.RightDockWidgetArea)
        self.report_dock = viewer._make_dock("Bag analysis", self.report, self.QtCore.Qt.DockWidgetArea.RightDockWidgetArea)
        self.chart_dock = viewer._make_dock("Numeric chart", self.chart, self.QtCore.Qt.DockWidgetArea.RightDockWidgetArea)
        self.docks = [self.inspector_dock, self.report_dock, self.chart_dock]
        for dock in self.docks:
            viewer.window.tabifyDockWidget(viewer.main_view_dock, dock)
            dock.hide()
        self.inspector_dock.visibilityChanged.connect(lambda visible: self.inspector.reload() if visible else None)
        menu = viewer.window.menuBar().addMenu("Analysis")
        self.actions = []
        for text, callback in (("Bag report", self.show_report), ("Scan bag", self.scan),
                               ("Message inspector", self.show_inspector), ("Inspect all at current time", self.inspect_time),
                               ("Numeric chart", lambda: self.show_dock(self.chart_dock)),
                               ("Topic duration", self.topic_duration)):
            action = menu.addAction(text)
            action.triggered.connect(callback)
            self.actions.append(action)
        file_menu = viewer._file_menu
        file_menu.addSeparator()
        self.recent_menu = file_menu.addMenu("Recent bags")
        self.refresh_recent()
        self.save_action = file_menu.addAction("Save workspace...")
        self.save_action.triggered.connect(self.save_workspace)
        self.load_action = file_menu.addAction("Open workspace...")
        self.load_action.triggered.connect(self.load_workspace)
        self.close_action = file_menu.addAction("Close bag")
        self.close_action.triggered.connect(self.close_bag)
        self.set_enabled(False)
        self.apply_theme()

    def _table(self, headers):
        table = self.QtWidgets.QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(self.QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(self.QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        table.setWordWrap(False)
        table.setTextElideMode(self.QtCore.Qt.TextElideMode.ElideMiddle)
        table.horizontalHeader().setStretchLastSection(True)
        table.setSortingEnabled(True)
        return table

    def _build_report(self):
        Q = self.QtWidgets
        panel = Q.QWidget()
        layout = Q.QVBoxLayout(panel)
        self.bag_summary = Q.QLabel("No bag")
        self.bag_summary.setWordWrap(True)
        layout.addWidget(self.bag_summary)
        toolbar = Q.QHBoxLayout()
        self.scan_button = Q.QPushButton("Scan bag")
        self.scan_button.clicked.connect(self.scan)
        toolbar.addWidget(self.scan_button)
        self.cancel_button = Q.QPushButton("Cancel task")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_report)
        toolbar.addWidget(self.cancel_button)
        self.manifest_button = Q.QPushButton("Save manifest...")
        self.manifest_button.clicked.connect(self.save_manifest)
        toolbar.addWidget(self.manifest_button)
        csv_button = Q.QPushButton("Save topics CSV...")
        csv_button.clicked.connect(self.save_topics)
        toolbar.addWidget(csv_button)
        self.report_search = Q.QLineEdit()
        self.report_search.setPlaceholderText("Filter report topics")
        self.report_search.setClearButtonEnabled(True)
        self.report_search.textChanged.connect(self.filter_report)
        toolbar.addWidget(self.report_search, 1)
        layout.addLayout(toolbar)
        self.report_tabs = Q.QTabWidget()
        self.topic_table = self._table(("Topic", "Type", "Category", "Messages", "Duration (s)", "Mean Hz", "Start offset (s)", "End gap (s)", "Formats"))
        self.topic_table.setColumnWidth(0, 260)
        self.topic_table.itemDoubleClicked.connect(self.inspect_report_topic)
        self.report_tabs.addTab(self.topic_table, "Topics")
        self.sync_table = self._table(("Topic", "Nearest (ns)", "Delta (ms)", "Before (ns)", "After (ns)", "Summary"))
        self.sync_table.itemDoubleClicked.connect(self.inspect_report_topic)
        self.report_tabs.addTab(self.sync_table, "Time inspection")
        self.export_table = self._table(("Topic", "Format", "Messages", "Output", "Warnings"))
        for column, width in enumerate((230, 65, 85, 280, 220)):
            self.export_table.setColumnWidth(column, width)
        self.report_tabs.addTab(self.export_table, "Export results")
        self.report_warnings = Q.QPlainTextEdit()
        self.report_warnings.setReadOnly(True)
        self.report_tabs.addTab(self.report_warnings, "Warnings")
        layout.addWidget(self.report_tabs, 1)
        self.report_status = Q.QLabel("Metadata only; scan not run")
        self.report_status.setWordWrap(True)
        layout.addWidget(self.report_status)
        return panel

    def set_enabled(self, enabled):
        for action in self.actions + [self.save_action, self.close_action]:
            action.setEnabled(enabled)
        for dock in self.docks:
            dock.widget().setEnabled(enabled)

    def show_dock(self, dock):
        dock.show()
        dock.raise_()

    def show_report(self):
        self.show_dock(self.report_dock)

    def show_inspector(self):
        topic = self.viewer._selected_topic() or self.viewer._properties_topic
        if topic:
            self.inspector.topic_box.setCurrentText(topic)
        self.show_dock(self.inspector_dock)
        self.inspector.reload()

    def inspect_report_topic(self, item):
        table = item.tableWidget()
        self.inspector.topic_box.setCurrentText(table.item(item.row(), 0).text())
        self.show_dock(self.inspector_dock)
        self.inspector.reload()

    def add_chart_field(self, topic, field):
        self.chart.add_series(topic, field)
        self.show_dock(self.chart_dock)

    def on_time(self, stamp):
        self.chart.show_at_timestamp(stamp)
        if self.inspector_dock.isVisible():
            self.inspector.reload(stamp)

    def apply_theme(self):
        palette = theme_palette(self.viewer._theme)
        for panel in (self.inspector, self.chart):
            for button in panel.findChildren(self.QtWidgets.QToolButton):
                symbol = button.property("standardIcon")
                if symbol is not None:
                    button.setIcon(tinted_standard_icon(self.viewer.QtGui, button, self.QtWidgets.QStyle.StandardPixmap(symbol), palette))
        self.chart.refresh_series()
        self.chart.plot.update()

    def on_bag(self):
        viewer = self.viewer
        self.clear_tasks()
        self.inspector.set_topics(viewer.session.topics)
        self.chart.clear()
        self.sync_table.setRowCount(0)
        self.export_table.setRowCount(0)
        self.refresh_report()
        self.set_enabled(True)
        recent = self.recent_paths()
        path = str(viewer.session.bag_path.resolve())
        recent = [path] + [item for item in recent if item != path]
        viewer._update_settings.setValue("files/recent", json.dumps(recent[:10]))
        self.refresh_recent()
        if self.pending_workspace is not None:
            from rosbagel.gui.workspace import restore_workspace
            data, self.pending_workspace = self.pending_workspace, None
            restore_workspace(viewer, data)

    def clear_tasks(self):
        for key in ("scan", "inspect-time", "duration", "inspector", "message-step", "chart"):
            self.viewer._io_tasks.cancel(key)
        self.inspector.clear()
        if self.chart.task is not None:
            self.chart.task = None
            self.chart.load_button.setText("Load")
            self.chart.status.setText("Chart load cancelled")
        self.scan_task = None
        self.scan_button.setText("Scan bag")
        self.scan_button.setEnabled(True)
        self.cancel_button.setEnabled(False)

    def cancel_report(self):
        for key in ("scan", "inspect-time", "duration"):
            self.viewer._io_tasks.cancel(key)
        self.scan_task = None
        self.scan_button.setText("Scan bag")
        self.scan_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.report_status.setText("Task cancelled")

    def refresh_report(self):
        viewer, table = self.viewer, self.topic_table
        topics = viewer.session.topics
        start, end = viewer._bag_start_ns, viewer._bag_end_ns
        duration = (end - start) / 1e9 if start is not None and end is not None else None
        self.bag_summary.setText(f"{viewer.session.bag_path}\n{len(topics)} topics | {sum(topic.message_count for topic in topics):,} messages | {duration:.3f} s | backend: {viewer.session.backend}" if duration is not None else f"{viewer.session.bag_path}\n{len(topics)} topics")
        rows = []
        for topic in topics:
            span = topic.duration_sec
            if topic.first_timestamp_ns is not None and topic.last_timestamp_ns is not None:
                span = (topic.last_timestamp_ns - topic.first_timestamp_ns) / 1e9
            rows.append((topic.name, topic.msgtype, topic.category, topic.message_count, span,
                         (topic.message_count - 1) / span if span and topic.message_count > 1 else None,
                         (topic.first_timestamp_ns - start) / 1e9 if start is not None and topic.first_timestamp_ns is not None else None,
                         (end - topic.last_timestamp_ns) / 1e9 if end is not None and topic.last_timestamp_ns is not None else None,
                         ", ".join(compatible_export_formats(topic))))
        self.fill_table(table, rows)
        self.manifest_button.setEnabled(viewer.session.manifest is not None)
        self.report_status.setText("Scan complete" if viewer.session.manifest is not None else "Metadata only; scan not run")
        self.report_warnings.setPlainText("\n".join(getattr(viewer.session.reader, "warnings", [])))

    def fill_table(self, table, rows):
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                item = self.QtWidgets.QTableWidgetItem()
                item.setData(self.QtCore.Qt.ItemDataRole.DisplayRole, "-" if value is None else round(value, 6) if isinstance(value, float) else value)
                item.setToolTip(str(value))
                table.setItem(row, column, item)
        table.setSortingEnabled(True)
        self.filter_report(self.report_search.text())

    def filter_report(self, query):
        for table in (self.topic_table, self.sync_table, self.export_table):
            for row in range(table.rowCount()):
                table.setRowHidden(row, query.casefold() not in table.item(row, 0).text().casefold())

    def scan(self):
        self.show_report()
        if self.scan_task is not None:
            self.scan_task.context.cancel()
            self.scan_button.setEnabled(False)
            return
        session = self.viewer.session
        if session.reader is None:
            return
        self.cancel_report()
        self.viewer._pause_playback()
        self.scan_button.setText("Cancel scan")
        self.cancel_button.setEnabled(True)
        self.report_status.setText("Scanning all messages...")

        def finish(manifest):
            self.scan_task = None
            self.scan_button.setText("Scan bag")
            self.scan_button.setEnabled(True)
            self.cancel_button.setEnabled(False)
            start, end = manifest.bag_start_timestamp_ns, manifest.bag_end_timestamp_ns
            self.viewer._bag_start_ns, self.viewer._bag_end_ns = start, end
            duration = (end - start) / 1e9 if start is not None and end is not None else 0.
            self.viewer.time_input.setRange(0., max(0., duration))
            self.viewer._set_bag_controls_enabled(True)
            checked = self.viewer.topic_tree._checked_topics()
            self.viewer._populate_topics()
            self.viewer._set_checked_topics(checked)
            self.refresh_report()
            self.report_warnings.setPlainText("\n".join(manifest.warnings))

        def failed(message):
            self.scan_task = None
            self.scan_button.setText("Scan bag")
            self.scan_button.setEnabled(True)
            self.cancel_button.setEnabled(False)
            self.report_status.setText(message)

        self.scan_task = self.viewer._io_tasks.submit("scan", lambda ctx: session.scan(progress_factory=ctx.progress_factory),
            finish, failed, on_progress=lambda label, value, total: self.report_status.setText(f"{label}: {value:,}/{total or '?'}"),
            on_cancel=lambda: failed("Scan cancelled"))

    def inspect_time(self):
        self.show_report()
        self.report_tabs.setCurrentWidget(self.sync_table)
        viewer = self.viewer
        stamp = viewer._current_timestamp_ns()
        if stamp is None:
            return
        session = viewer.session
        viewer._pause_playback()
        self.cancel_report()
        self.cancel_button.setEnabled(True)
        self.report_status.setText("Inspecting nearest messages...")

        def finish(result):
            self.cancel_button.setEnabled(False)
            target, rows, warnings = result
            self.fill_table(self.sync_table, [(row.topic, str(row.nearest_timestamp_ns) if row.nearest_timestamp_ns is not None else None,
                row.delta_ms, str(row.before_timestamp_ns) if row.before_timestamp_ns is not None else None,
                str(row.after_timestamp_ns) if row.after_timestamp_ns is not None else None, str(row.summary.get("summary", ""))) for row in rows])
            self.report_status.setText(f"Inspected {len(rows)} topics at {target} ns")
            self.report_warnings.setPlainText("\n".join(warnings))

        viewer._io_tasks.submit("inspect-time", lambda ctx: session.inspect_time(stamp, absolute_ns=True, progress_factory=ctx.progress_factory),
            finish, self.report_failed, on_progress=lambda label, value, total: self.report_status.setText(f"{label}: {value:,}"))

    def report_failed(self, message):
        self.cancel_button.setEnabled(False)
        self.report_status.setText(message)

    def topic_duration(self):
        topic = self.viewer._selected_topic() or self.viewer._properties_topic
        if not topic:
            self.viewer._show_warning("Select a topic first.")
            return
        self.show_report()
        session = self.viewer.session
        self.cancel_report()
        self.cancel_button.setEnabled(True)
        self.report_status.setText(f"Reading duration: {topic}")

        def finish(result):
            self.cancel_button.setEnabled(False)
            self.report_status.setText(" | ".join(f"{key}: {value}" for key, value in asdict(result).items() if key not in {"topic", "msgtype"}))
            self.report_status.setToolTip(result.topic)

        self.viewer._io_tasks.submit("duration", lambda ctx: session.topic_duration(topic, progress_factory=ctx.progress_factory),
                                    finish, self.report_failed)

    def save_manifest(self):
        manifest = self.viewer.session.manifest
        if manifest is None:
            return
        path, _ = self.QtWidgets.QFileDialog.getSaveFileName(self.report, "Save manifest", "manifest.json", "JSON (*.json)")
        if path:
            self._save_report(lambda: write_manifest(manifest, path), path)

    def save_topics(self):
        path, _ = self.QtWidgets.QFileDialog.getSaveFileName(self.report, "Save topic table", "topics.csv", "CSV (*.csv)")
        if path:
            topics = list(self.viewer.session.topics)
            self._save_report(lambda: write_topics_csv(topics, path), path)

    def _save_report(self, work, path):
        self.viewer._io_tasks.submit("save-report", lambda ctx: work(), lambda _: self.viewer._log(f"Saved {path}"), self.viewer._show_warning)

    def record_exports(self, results, failures):
        self.fill_table(self.export_table, [(item.topic, item.format, item.message_count, item.output_path, "; ".join(item.warnings)) for item in results])
        warnings = [f"{item.topic} ({item.format}): {warning}" for item in results for warning in item.warnings]
        self.report_warnings.setPlainText("\n".join([*failures, *warnings]))
        self.report_status.setText(f"{len(results)} exports completed | {len(failures)} failed | {len(warnings)} warnings")
        self.report_tabs.setCurrentWidget(self.export_table)
        self.show_report()

    def recent_paths(self):
        try:
            values = json.loads(str(self.viewer._update_settings.value("files/recent", "[]")))
            return [value for value in values if isinstance(value, str)][:10] if isinstance(values, list) else []
        except (ValueError, TypeError):
            return []

    def refresh_recent(self):
        self.recent_menu.clear()
        for path in self.recent_paths():
            action = self.recent_menu.addAction(path)
            action.triggered.connect(lambda checked=False, value=path: self.viewer.open_bag(value))
        self.recent_menu.setEnabled(bool(self.recent_paths()))

    def save_workspace(self):
        from rosbagel.gui.workspace import save_workspace_dialog
        save_workspace_dialog(self.viewer)

    def load_workspace(self):
        from rosbagel.gui.workspace import load_workspace_dialog
        load_workspace_dialog(self.viewer)

    def close_bag(self):
        viewer = self.viewer
        if viewer._opening or viewer._exporting:
            return
        from rosbagel.core.session import Session
        viewer._pause_playback()
        self.clear_tasks()
        for pane in viewer._all_panes():
            pane.clear_topic()
        viewer._clear_lane_overlay()
        previous, viewer.session = viewer.session, Session(backend=viewer.session.backend)
        viewer.preview = None
        viewer.settings.topics.clear()
        viewer.settings.bag_path = None
        viewer._bag_start_ns = viewer._bag_end_ns = None
        viewer._topic_info_by_name.clear()
        viewer._topic_by_item.clear()
        viewer.topic_tree.clear()
        viewer.time_input.setRange(0, 0)
        viewer.time_slider.setValue(0)
        viewer._apply_topic_settings(None)
        viewer._set_bag_controls_enabled(False)
        viewer.window.setWindowTitle("ROSBagel Timeline Viewer")
        self.inspector.set_topics([])
        self.chart.clear()
        self.topic_table.setRowCount(0)
        self.sync_table.setRowCount(0)
        self.export_table.setRowCount(0)
        self.bag_summary.setText("No bag")
        self.report_status.setText("No bag")
        self.report_warnings.clear()
        self.viewer._update_topic_status()
        viewer._io_tasks.submit("close-bag", lambda ctx: previous.close(), lambda _: None, viewer._log)
