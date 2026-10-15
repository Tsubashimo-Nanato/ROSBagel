from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
from threading import Event
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from rosbagel.core.analysis import adjacent_timestamp, load_numeric_curves, message_fields, numeric_field
from rosbagel.core.jobs import CancelledError
from rosbagel.core.models import ExportResult, MessageRecord, TopicInfo
from rosbagel.core.preview import PreviewService, TopicDisplaySettings
from rosbagel.gui.tasks import TaskContext


@dataclass
class Vector:
    x: float
    y: float


class Reader:
    path = Path("fake.bag")
    warnings = []

    def __init__(self):
        self.records = [MessageRecord(topic, stamp, "geometry_msgs/msg/Vector3", b"abcd", Vector(value, -value))
                        for topic in ("/front", "/rear") for stamp, value in ((0, 1.), (100_000_000, 3.), (250_000_000, 2.))]

    def get_topics(self):
        return [TopicInfo(name, "geometry_msgs/msg/Vector3", message_count=3, category="vector_struct") for name in ("/front", "/rear")]

    def iter_messages(self, topics=None):
        yield from sorted((record for record in self.records if topics is None or record.topic in topics), key=lambda record: record.timestamp_ns)

    def get_message_count(self, topic):
        return 3

    def get_time_bounds(self):
        return 0, 250_000_000

    def close(self):
        pass


class AnalysisDataTests(unittest.TestCase):
    def test_field_browser_bounds_arrays_and_exposes_numeric_paths(self):
        value = {"pose": Vector(1, 2), "pixels": np.zeros(1_000_000, dtype=np.uint8), "text": "hello"}
        rows = message_fields(value)
        self.assertLess(len(rows), 40)
        self.assertTrue(next(row for row in rows if row.path == "pose.x").numeric)
        self.assertEqual(next(row for row in rows if row.path == "pixels").text, "1,000,000 items")
        self.assertEqual(numeric_field(value, "pixels.2"), 0.)
        self.assertIsNone(numeric_field(value, "pose.missing"))
        self.assertIsNone(numeric_field(float("nan"), "value"))

    def test_curves_keep_extrema_and_late_data_with_bounded_memory(self):
        reader = Reader()
        reader.records = [MessageRecord("/front", index, "test", decoded={"value": 99. if index == 400 else float(index % 3)}) for index in range(1000)]
        result = load_numeric_curves(reader, [("/front", "value")], 0, 999, TaskContext(), max_points=20)[0]
        self.assertLessEqual(len(result.values), 20)
        self.assertIn(99., result.values)
        self.assertGreater(max(result.timestamps_ns), 950)
        self.assertEqual(result.sample_count, 1000)

    def test_multiple_curves_empty_fields_and_cancellation(self):
        reader = Reader()
        curves = load_numeric_curves(reader, [("/front", "x"), ("/rear", "y"), ("/rear", "missing")], 0, 250_000_000, TaskContext())
        self.assertEqual([curve.sample_count for curve in curves], [3, 3, 0])
        token = TaskContext()
        token.cancel()
        with self.assertRaises(CancelledError):
            load_numeric_curves(reader, [("/front", "x")], 0, 250_000_000, token)
        with self.assertRaises(ValueError):
            load_numeric_curves(reader, [], 0, 1, TaskContext())

    def test_previous_and_next_are_strict_and_handle_edges(self):
        reader = Reader()
        self.assertEqual(adjacent_timestamp(reader, "/front", 100_000_000, 1, TaskContext()), 250_000_000)
        self.assertEqual(adjacent_timestamp(reader, "/front", 100_000_000, -1, TaskContext()), 0)
        self.assertIsNone(adjacent_timestamp(reader, "/front", 0, -1, TaskContext()))
        self.assertIsNone(adjacent_timestamp(reader, "/front", 250_000_000, 1, TaskContext()))


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 not installed")
class WorkflowGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6 import QtCore, QtGui, QtWidgets, QtTest
        cls.QtCore, cls.QtGui, cls.Q, cls.QtTest = QtCore, QtGui, QtWidgets, QtTest
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        from rosbagel.gui.timeline_viewer import TimelineViewer
        values = {"updates/mode": "off", "ui/theme": "dark"}
        settings = patch.object(self.QtCore, "QSettings", return_value=SimpleNamespace(
            value=lambda key, default=None: values.get(key, default), setValue=lambda key, value: values.__setitem__(key, value)))
        settings.start()
        self.addCleanup(settings.stop)
        self.viewer = TimelineViewer()
        self.addCleanup(self.viewer.window.close)
        viewer = self.viewer
        viewer.session.reader = Reader()
        viewer.session.bag_path = Path("fake.bag")
        viewer.session.topics = viewer.session.reader.get_topics()
        viewer.settings.bag_path = "fake.bag"
        viewer.settings.topics = {topic.name: TopicDisplaySettings(topic.name) for topic in viewer.session.topics}
        viewer._bag_start_ns, viewer._bag_end_ns = 0, 250_000_000
        viewer.time_input.setRange(0, .25)
        viewer._populate_topics()
        viewer.preview = PreviewService(viewer.session)
        viewer._set_bag_controls_enabled(True)
        viewer.workflows.on_bag()
        self.errors = []
        viewer._show_warning = self.errors.append

    def wait(self, condition, timeout=5):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(condition(), "GUI task timeout")

    def test_report_scan_manifest_and_topic_csv(self):
        flow = self.viewer.workflows
        self.assertFalse(flow.manifest_button.isEnabled())
        flow.scan()
        self.wait(lambda: flow.scan_task is None)
        self.assertTrue(flow.manifest_button.isEnabled())
        self.assertEqual(flow.topic_table.rowCount(), 2)
        self.assertIsNotNone(self.viewer.session.manifest)
        with tempfile.TemporaryDirectory() as directory:
            for filename, callback in (("manifest.json", flow.save_manifest), ("topics.csv", flow.save_topics)):
                path = Path(directory) / filename
                with patch.object(self.Q.QFileDialog, "getSaveFileName", return_value=(str(path), "")):
                    callback()
                    self.wait(lambda: not self.viewer._io_tasks.busy)
                self.assertTrue(path.is_file())
            self.assertEqual(len(json.loads((Path(directory) / "manifest.json").read_text())["topics"]), 2)
        self.assertFalse(self.errors)

    def test_inspector_fields_filter_next_previous_and_save(self):
        flow = self.viewer.workflows
        inspector = flow.inspector
        inspector.reload(0)
        self.wait(lambda: inspector.record is not None)
        self.assertEqual(inspector.table.rowCount(), 2)
        inspector.search.setText("x")
        self.assertTrue(inspector.table.isRowHidden(1))
        inspector.step(1)
        self.wait(lambda: inspector.record is not None and inspector.record.timestamp_ns == 100_000_000)
        inspector.step(-1)
        self.wait(lambda: inspector.record is not None and inspector.record.timestamp_ns == 0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "message.json"
            with patch.object(self.Q.QFileDialog, "getSaveFileName", return_value=(str(path), "")):
                inspector.save_message()
                self.wait(lambda: not self.viewer._io_tasks.busy)
            self.assertEqual(json.loads(path.read_text())["message"]["x"], 1.)

    def test_numeric_field_selection_builds_chart_and_seeks_timeline(self):
        viewer = self.viewer
        viewer.show()
        flow = viewer.workflows
        flow.inspector.reload(0)
        self.wait(lambda: flow.inspector.record is not None)
        flow.inspector.table.selectRow(0)
        self.assertTrue(flow.inspector.plot_button.isEnabled())
        flow.inspector.plot_button.click()
        self.wait(lambda: flow.chart.task is None)
        self.assertEqual(flow.chart.selections, [("/front", "x")])
        self.assertEqual(flow.chart.plot.curves[0].values, [1., 3., 2.])
        self.app.processEvents()
        self.assertTrue(viewer.play_button.isVisible())
        self.assertTrue(viewer.time_slider.isVisible())
        rect = flow.chart.plot.plot_rect()
        self.QtTest.QTest.mouseClick(flow.chart.plot, self.QtCore.Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
        self.assertAlmostEqual(viewer.time_input.value(), .125, places=2)
        with tempfile.TemporaryDirectory() as directory:
            for filename, callback in (("chart.csv", flow.chart.save_csv), ("chart.png", flow.chart.save_image)):
                path = Path(directory) / filename
                with patch.object(self.Q.QFileDialog, "getSaveFileName", return_value=(str(path), "")):
                    callback()
                self.assertGreater(path.stat().st_size, 0)

    def test_changing_topic_disables_stale_message_actions_until_loaded(self):
        inspector = self.viewer.workflows.inspector
        inspector.reload(0)
        self.wait(lambda: inspector.record is not None)
        inspector.table.selectRow(0)
        started, release = Event(), Event()
        original = self.viewer.preview.nearest_record

        def slow_record(*args, **kwargs):
            started.set()
            release.wait(3)
            return original(*args, **kwargs)

        try:
            with patch.object(self.viewer.preview, "nearest_record", side_effect=slow_record):
                inspector.topic_box.setCurrentText("/rear")
                self.wait(started.is_set)
                self.assertFalse(inspector.plot_button.isEnabled())
                self.assertFalse(inspector.save_button.isEnabled())
                self.assertFalse(inspector.next.isEnabled())
                release.set()
                self.wait(lambda: inspector.record is not None)
            self.assertEqual(inspector.record.topic, "/rear")
            self.assertTrue(inspector.plot_button.isEnabled())
        finally:
            release.set()

    def test_time_inspection_and_duration_are_gui_workflows(self):
        flow = self.viewer.workflows
        self.viewer._properties_topic = "/front"
        flow.inspect_time()
        self.wait(lambda: not self.viewer._io_tasks.busy)
        self.assertEqual(flow.sync_table.rowCount(), 2)
        flow.topic_duration()
        self.wait(lambda: not self.viewer._io_tasks.busy)
        self.assertIn("message_count: 3", flow.report_status.text())

    def test_report_filter_survives_new_results_and_sorting(self):
        flow = self.viewer.workflows
        flow.report_search.setText("rear")
        flow.inspect_time()
        self.wait(lambda: not self.viewer._io_tasks.busy)
        table = flow.sync_table
        for row in range(table.rowCount()):
            self.assertEqual(table.isRowHidden(row), table.item(row, 0).text() != "/rear")
        for column in (0, 3, 4):
            flow.topic_table.sortItems(column, self.QtCore.Qt.SortOrder.AscendingOrder)
            flow.refresh_report()
            self.assertEqual({flow.topic_table.item(row, 0).text() for row in range(2)}, {"/front", "/rear"})

    def test_chart_pan_zoom_fit_and_missing_time_bounds(self):
        viewer = self.viewer
        viewer.show()
        chart = viewer.workflows.chart
        viewer.workflows.add_chart_field("/front", "x")
        self.wait(lambda: chart.task is None)
        plot = chart.plot
        self.app.processEvents()
        bounds = plot.bounds
        center = plot.plot_rect().center()
        wheel = self.QtGui.QWheelEvent(center, plot.mapToGlobal(center.toPoint()), self.QtCore.QPoint(),
            self.QtCore.QPoint(0, 120), self.QtCore.Qt.MouseButton.NoButton,
            self.QtCore.Qt.KeyboardModifier.NoModifier, self.QtCore.Qt.ScrollPhase.NoScrollPhase, False)
        self.Q.QApplication.sendEvent(plot, wheel)
        self.assertLess(plot.bounds[1] - plot.bounds[0], bounds[1] - bounds[0])
        zoomed = plot.bounds
        self.QtTest.QTest.mousePress(plot, self.QtCore.Qt.MouseButton.MiddleButton, pos=center.toPoint())
        self.QtTest.QTest.mouseMove(plot, (center + self.QtCore.QPointF(20, 10)).toPoint())
        self.QtTest.QTest.mouseRelease(plot, self.QtCore.Qt.MouseButton.MiddleButton)
        self.assertNotEqual(plot.bounds, zoomed)
        self.QtTest.QTest.mouseDClick(plot, self.QtCore.Qt.MouseButton.LeftButton, pos=center.toPoint())
        self.assertEqual(plot.bounds, bounds)
        viewer._bag_start_ns = None
        chart.load()
        self.assertIn("Scan bag", chart.status.text())

    def test_failed_bag_open_releases_cancelled_chart_controls(self):
        viewer = self.viewer
        chart = viewer.workflows.chart
        viewer.workflows.add_chart_field("/front", "x")
        viewer.open_bag("missing-gui-workflow-test-bag")
        self.wait(lambda: not viewer._opening)
        self.assertIsNotNone(viewer.session.reader)
        self.assertIsNone(chart.task)
        self.assertEqual(chart.load_button.text(), "Load")
        chart.load_button.click()
        self.wait(lambda: chart.task is None)
        self.assertEqual(chart.plot.curves[0].values, [1., 3., 2.])
        self.assertEqual(len(self.errors), 1)

    def test_workspace_restores_nested_views_settings_and_chart_fields(self):
        from rosbagel.gui.workspace import restore_workspace, workspace_snapshot
        viewer = self.viewer
        first = viewer._panes[0]
        second = viewer.split_pane(first, "horizontal")
        third = viewer.split_pane(second, "vertical")
        viewer.assign_topic_to_pane(first, "/front")
        viewer.assign_topic_to_pane(third, "/rear")
        viewer.settings.topics["/rear"].opacity = .3
        viewer._set_checked_topics(["/rear"])
        viewer._set_timeline_seconds(.1, immediate=False)
        viewer.workflows.chart.selections = [("/rear", "y")]
        data = workspace_snapshot(viewer)
        self.assertEqual(data["bag_path"], str(Path("fake.bag").resolve()))
        viewer.delete_pane(third)
        restore_workspace(viewer, data)
        self.wait(lambda: not viewer._io_tasks.busy)
        self.assertEqual(len(viewer._panes), 3)
        self.assertEqual(viewer._panes[2].topic, "/rear")
        self.assertEqual(viewer._panes[1].parentWidget().orientation(), self.QtCore.Qt.Orientation.Vertical)
        self.assertEqual(viewer.settings.topics["/rear"].opacity, .3)
        self.assertEqual(viewer.topic_tree._checked_topics(), ["/rear"])
        self.assertEqual(viewer.workflows.chart.selections, [("/rear", "y")])
        self.assertEqual(viewer.time_input.value(), .1)

    def test_workspace_validation_rejects_corruption_before_changes(self):
        from rosbagel.gui.workspace import validate_workspace, workspace_snapshot
        data = workspace_snapshot(self.viewer)
        for key, value in (("time_sec", float("nan")), ("layout", {"topics": "bad"}), ("dock_state", "not base64"), ("inspector_topic", [])):
            with self.subTest(key=key):
                damaged = deepcopy(data)
                damaged[key] = value
                with self.assertRaises(ValueError):
                    validate_workspace(damaged)

    def test_workspace_restore_cancels_previous_message_step(self):
        from rosbagel.gui.workspace import restore_workspace, workspace_snapshot
        viewer = self.viewer
        inspector = viewer.workflows.inspector
        inspector.reload(0)
        self.wait(lambda: inspector.record is not None)
        saved = workspace_snapshot(viewer)
        saved["time_sec"] = .25
        started, release = Event(), Event()

        def slow_step(reader, topic, stamp, direction, context):
            started.set()
            release.wait(3)
            context.throw_if_cancelled()
            return 100_000_000

        try:
            with patch("rosbagel.gui.inspector.adjacent_timestamp", side_effect=slow_step):
                inspector.step(1)
                self.wait(started.is_set)
                restore_workspace(viewer, saved)
                release.set()
                self.wait(lambda: not viewer._io_tasks.busy)
            self.assertEqual(viewer.time_input.value(), .25)
        finally:
            release.set()

    def test_detaching_final_main_view_respects_workspace_view_limit(self):
        from rosbagel.gui.workspace import validate_workspace, workspace_snapshot
        viewer = self.viewer
        pane = viewer._panes[0]
        for _ in range(15):
            viewer.duplicate_pane_window(pane)
        viewer.detach_pane(pane)
        self.assertEqual(len(viewer._all_panes()), 16)
        self.assertIn(pane, viewer._panes)
        self.assertIn("16", self.errors[-1])
        validate_workspace(workspace_snapshot(viewer))

    def test_workspace_save_and_open_dialog_restore_detached_view(self):
        viewer = self.viewer
        viewer.assign_topic_to_pane(viewer._panes[0], "/front")
        viewer.detach_pane(viewer._panes[0])
        with tempfile.TemporaryDirectory() as directory:
            bag = Path(directory) / "source.bag"
            bag.touch()
            viewer.session.bag_path = bag
            target = Path(directory) / "session.bagel.json"
            with patch.object(self.Q.QFileDialog, "getSaveFileName", return_value=(str(target), "")):
                viewer.workflows.save_workspace()
            self.assertTrue(target.exists())
            viewer._window_panes[0].window().close()
            with patch.object(self.Q.QFileDialog, "getOpenFileName", return_value=(str(target), "")):
                viewer.workflows.load_workspace()
            self.assertEqual(len(viewer._window_panes), 1)
            self.assertEqual(viewer._window_panes[0].topic, "/front")
            self.assertFalse(self.errors)

    def test_scan_cancel_discards_result_and_allows_retry(self):
        flow = self.viewer.workflows
        started, release = Event(), Event()
        original = self.viewer.session.reader.iter_messages

        def slow_messages(topics=None):
            started.set()
            release.wait(3)
            yield from original(topics)

        try:
            with patch.object(self.viewer.session.reader, "iter_messages", side_effect=slow_messages):
                flow.scan()
                self.wait(started.is_set)
                flow.cancel_button.click()
                release.set()
                self.wait(lambda: not self.viewer._io_tasks.busy)
            self.assertIsNone(self.viewer.session.manifest)
            self.assertIn("cancelled", flow.report_status.text())
            flow.scan()
            self.wait(lambda: flow.scan_task is None)
            self.assertIsNotNone(self.viewer.session.manifest)
        finally:
            release.set()

    def test_chart_cancel_and_manual_field_dialog(self):
        viewer = self.viewer
        chart = viewer.workflows.chart
        errors = []
        timer = self.QtCore.QTimer(viewer.window)
        timer.setInterval(10)
        deadline = time.monotonic() + 5

        def interact():
            dialog = next((widget for widget in self.app.topLevelWidgets() if widget.windowTitle() == "Add numeric field" and widget.isVisible()), None)
            if dialog is None:
                return
            try:
                if time.monotonic() > deadline:
                    raise AssertionError("Field discovery timed out")
                field = next(widget for widget in dialog.findChildren(self.Q.QComboBox) if widget.accessibleName() == "Numeric field path")
                if field.count() == 0:
                    return
                field.setEditText("y")
                timer.stop()
                dialog.findChild(self.Q.QDialogButtonBox).button(self.Q.QDialogButtonBox.StandardButton.Ok).click()
            except BaseException as exc:
                errors.append(exc)
                timer.stop()
                dialog.reject()

        timer.timeout.connect(interact)
        timer.start()
        chart.add_field_dialog()
        if errors:
            raise errors[0]
        self.wait(lambda: chart.task is None)
        self.assertEqual(chart.selections, [("/front", "y")])
        self.assertEqual(chart.plot.curves[0].values, [-1., -3., -2.])
        started, release = Event(), Event()

        def loading(*args):
            started.set()
            release.wait(3)
            args[-1].throw_if_cancelled()
            return []

        try:
            with patch("rosbagel.gui.charts.load_numeric_curves", side_effect=loading):
                chart.load()
                self.wait(started.is_set)
                chart.load_button.click()
                release.set()
                self.wait(lambda: chart.task is None)
            self.assertIn("cancelled", chart.status.text())
            self.assertEqual(chart.load_button.text(), "Load")
        finally:
            release.set()

    def test_suggested_exports_use_scan_and_core_policy(self):
        viewer = self.viewer
        viewer._set_checked_topics(["/front"])
        flow = viewer.workflows
        flow.scan()
        self.wait(lambda: flow.scan_task is None)
        exported, errors = [], []

        def export(topic, fmt, out, **kwargs):
            exported.append(fmt)
            return ExportResult(topic, fmt, str(out), 3)

        def interact():
            dialog = next(widget for widget in self.app.topLevelWidgets() if widget.windowTitle() == "Export topics" and widget.isVisible())
            try:
                mode = next(widget for widget in dialog.findChildren(self.Q.QComboBox) if widget.accessibleName() == "Export format mode")
                mode.setCurrentText("Suggested formats")
                output = next(widget for widget in dialog.findChildren(self.Q.QLineEdit) if widget.accessibleName() == "Output directory")
                output.setText("exports")
                dialog.findChild(self.Q.QDialogButtonBox).button(self.Q.QDialogButtonBox.StandardButton.Ok).click()
            except BaseException as exc:
                errors.append(exc)
                dialog.done(0)

        with patch.object(viewer.session, "export_topic", side_effect=export), patch.object(self.Q.QMessageBox, "question", return_value=self.Q.QMessageBox.StandardButton.Yes):
            self.QtCore.QTimer.singleShot(0, interact)
            self.QtCore.QTimer.singleShot(5000, viewer.window, lambda: [widget.done(0) for widget in self.app.topLevelWidgets() if isinstance(widget, self.Q.QDialog) and widget.isVisible()])
            viewer._show_export_dialog()
        if errors:
            raise errors[0]
        from rosbagel.core.export_policy import default_export_formats
        self.assertEqual(exported, default_export_formats(viewer._topic_info_by_name["/front"]))
        self.assertIn(f"Completed {len(exported)}/{len(exported)} exports", viewer.log_text.toPlainText())

    def test_close_bag_clears_workflows_and_recent_list_is_deduplicated(self):
        viewer = self.viewer
        viewer.workflows.on_bag()
        self.assertEqual(viewer.workflows.recent_paths(), [str(Path("fake.bag").resolve())])
        viewer.workflows.close_bag()
        self.wait(lambda: not viewer._io_tasks.busy)
        self.assertIsNone(viewer.session.reader)
        self.assertEqual(viewer.workflows.inspector.topic_box.count(), 0)
        self.assertFalse(viewer.workflows.save_action.isEnabled())

    def test_export_per_topic_formats_and_result_table(self):
        viewer = self.viewer
        viewer._set_checked_topics(["/front", "/rear"])
        exported, errors = [], []

        def export(topic, fmt, out, **kwargs):
            exported.append((topic, fmt))
            return ExportResult(topic, fmt, str(out), 3, warnings=["Short topic coverage"])

        def interact():
            dialog = next(widget for widget in self.app.topLevelWidgets() if widget.windowTitle() == "Export topics" and widget.isVisible())
            try:
                mode = next(widget for widget in dialog.findChildren(self.Q.QComboBox) if widget.accessibleName() == "Export format mode")
                mode.setCurrentText("Per-topic formats")
                table = dialog.findChild(self.Q.QTableWidget)
                table.cellWidget(0, 1).setCurrentText("csv")
                table.cellWidget(1, 1).setCurrentText("jsonl")
                output = next(widget for widget in dialog.findChildren(self.Q.QLineEdit) if widget.accessibleName() == "Output directory")
                output.setText("exports")
                dialog.findChild(self.Q.QDialogButtonBox).button(self.Q.QDialogButtonBox.StandardButton.Ok).click()
            except BaseException as exc:
                errors.append(exc)
                dialog.done(self.Q.QDialog.DialogCode.Rejected)

        with patch.object(viewer.session, "export_topic", side_effect=export), patch.object(self.Q.QMessageBox, "question", return_value=self.Q.QMessageBox.StandardButton.Yes):
            self.QtCore.QTimer.singleShot(0, interact)
            self.QtCore.QTimer.singleShot(5000, viewer.window, lambda: [widget.done(0) for widget in self.app.topLevelWidgets() if isinstance(widget, self.Q.QDialog) and widget.isVisible()])
            viewer._show_export_dialog()
        if errors:
            raise errors[0]
        self.assertEqual(exported, [("/front", "csv"), ("/rear", "jsonl")])
        self.assertEqual(viewer.workflows.export_table.rowCount(), 2)
        self.assertIn("2 exports completed", viewer.workflows.report_status.text())
        self.assertIn("/rear (jsonl): Short topic coverage", viewer.workflows.report_warnings.toPlainText())


if __name__ == "__main__":
    unittest.main()
