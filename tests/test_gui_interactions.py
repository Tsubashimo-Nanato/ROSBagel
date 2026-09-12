from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from threading import Event, get_ident
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

if importlib.util.find_spec("PySide6") is None:
    raise unittest.SkipTest("PySide6 is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from rosbagel.core.models import MessageRecord, TopicInfo
from rosbagel.core.preview import PointCloudPreview, TopicDisplaySettings
from rosbagel.gui.playback import PlaybackWindow, RenderedFrame
from rosbagel.gui.point_plot import create_point_plot
from rosbagel.gui.timeline_viewer import TimelineViewer, VIEW_MIME


def wait_until(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        QtTest.QTest.qWait(10)
        time.sleep(0.005)
    if not condition():
        raise AssertionError("Qt task timed out")


class GuiInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        values = {"ui/theme": "dark", "updates/mode": "off"}
        preferences = patch.object(QtCore, "QSettings", return_value=SimpleNamespace(
            value=lambda key, default=None: values.get(key, default),
            setValue=lambda key, value: values.__setitem__(key, value),
        ))
        preferences.start()
        self.addCleanup(preferences.stop)
        self.viewer = TimelineViewer()
        self.addCleanup(self.viewer.window.close)

    def install_topics(self, *names, category="point_cloud", duration=0.25):
        viewer = self.viewer
        msgtype = "sensor_msgs/msg/PointCloud2" if category == "point_cloud" else "sensor_msgs/msg/Image"
        topics = [TopicInfo(name=name, msgtype=msgtype, category=category, message_count=2) for name in names]
        viewer.session.reader = SimpleNamespace(close=lambda: None)
        viewer.session.topics = topics
        viewer.settings.topics = {name: TopicDisplaySettings(name) for name in names}
        viewer._bag_start_ns = 0
        viewer._bag_end_ns = round(duration * 1e9)
        viewer.time_input.setRange(0.0, duration)
        viewer._populate_topics()
        viewer._set_bag_controls_enabled(True)
        return topics

    def test_empty_window_disables_bag_actions_and_hides_lane_dock(self):
        viewer = self.viewer
        self.assertFalse(viewer.play_button.isEnabled())
        self.assertFalse(viewer.export_action.isEnabled())
        self.assertFalse(viewer.visible_check.isEnabled())
        self.assertFalse(viewer._panes[0].render_button.isEnabled())
        self.assertTrue(viewer.lane_overlay_dock.isHidden())

    def test_short_timeline_maps_endpoints_and_manual_input(self):
        viewer = self.viewer
        self.install_topics("/points")
        viewer.time_slider.setValue(500)
        self.assertAlmostEqual(viewer.time_input.value(), 0.125)
        viewer.time_slider.setValue(1000)
        self.assertAlmostEqual(viewer.time_input.value(), 0.25)
        viewer._on_time_input_changed(0.05)
        self.assertEqual(viewer.time_slider.value(), 200)

    def test_step_pauses_and_play_at_end_restarts(self):
        viewer = self.viewer
        self.install_topics("/points")
        viewer._toggle_playback()
        self.assertTrue(viewer.play_timer.isActive())
        viewer._step_forward()
        self.assertFalse(viewer.play_timer.isActive())
        viewer._set_timeline_seconds(0.25, immediate=True)
        viewer._toggle_playback()
        self.assertEqual(viewer.time_input.value(), 0)
        self.assertTrue(viewer.play_timer.isActive())

    def test_properties_edit_selected_topic_without_overwriting_another(self):
        viewer = self.viewer
        self.install_topics("/front", "/rear")
        viewer._apply_topic_settings("/front")
        viewer.point_size.setValue(7)
        viewer.opacity.setValue(0.4)
        viewer.sync_offset.setValue(-0.2)
        viewer.visible_check.setChecked(False)
        viewer._apply_topic_settings("/rear")
        self.assertEqual(viewer.point_size.value(), 2)
        self.assertEqual(viewer.settings.topics["/front"].point_size, 7)
        self.assertEqual(viewer.settings.topics["/front"].opacity, 0.4)
        self.assertFalse(viewer.settings.topics["/front"].visible)
        viewer._apply_topic_settings("/front")
        self.assertEqual(viewer.sync_offset.value(), -0.2)

    def test_clicking_content_activates_that_view(self):
        viewer = self.viewer
        topics = self.install_topics("/front", "/rear")
        first = viewer._panes[0]
        second = viewer.split_pane(first, "horizontal")
        first.set_topic(topics[0].name, topics[0])
        second.set_topic(topics[1].name, topics[1])
        viewer.show()
        QtTest.QTest.mouseClick(first.image_label, QtCore.Qt.MouseButton.LeftButton)
        self.assertIs(viewer._active_pane, first)
        self.assertEqual(viewer._properties_topic, "/front")

    def test_split_is_local_and_close_collapses_splitter(self):
        viewer = self.viewer
        first = viewer._panes[0]
        second = viewer.split_pane(first, "horizontal")
        third = viewer.split_pane(second, "vertical")
        self.assertIs(second.parentWidget(), third.parentWidget())
        self.assertEqual(second.parentWidget().orientation(), QtCore.Qt.Orientation.Vertical)
        self.assertEqual(first.parentWidget().orientation(), QtCore.Qt.Orientation.Horizontal)
        viewer.delete_pane(third)
        self.assertIs(second.parentWidget(), first.parentWidget())
        viewer.delete_pane(second)
        self.assertEqual(viewer._panes, [first])
        self.assertIs(viewer.view_layout.layout.itemAt(0).widget(), first)

    def test_maximized_split_restores_all_views(self):
        viewer = self.viewer
        first = viewer._panes[0]
        second = viewer.split_pane(first, "horizontal")
        viewer.toggle_maximize_pane(first)
        self.assertTrue(second.isHidden())
        viewer.split_pane(first, "vertical")
        self.assertTrue(all(not pane.isHidden() for pane in viewer._panes))

    def test_new_window_moves_and_redocks_same_view(self):
        viewer = self.viewer
        first = viewer._panes[0]
        viewer.detach_pane(first)
        self.assertIn(first, viewer._window_panes)
        target = viewer._panes[0]
        viewer.move_pane(first, target, "left")
        self.assertFalse(viewer._window_panes)
        self.assertIs(first.parentWidget(), target.parentWidget())
        self.assertIs(first.parentWidget().widget(0), first)

    def test_close_floating_active_view_restores_valid_active_pane(self):
        viewer = self.viewer
        first = viewer._panes[0]
        viewer.duplicate_pane_window(first)
        floating = viewer._window_panes[0]
        viewer._set_active_pane(floating)
        viewer.delete_pane(floating)
        self.assertIs(viewer._active_pane, first)
        self.assertFalse(viewer._window_panes)

    def test_clear_last_view_resets_visible_content(self):
        viewer = self.viewer
        topic = self.install_topics("/points")[0]
        pane = viewer._panes[0]
        pane.set_topic(topic.name, topic)
        pane.stack.setCurrentWidget(pane.raw_text)
        viewer.delete_pane(pane)
        self.assertIs(pane.stack.currentWidget(), pane.image_label)
        self.assertIsNone(pane.topic)
        self.assertFalse(pane.render_button.isEnabled())

    def test_drag_unchecked_topic_does_not_steal_checked_selection(self):
        viewer = self.viewer
        self.install_topics("/front", "/rear")
        front, rear = viewer.topic_tree.topLevelItem(0), viewer.topic_tree.topLevelItem(1)
        front.setCheckState(0, QtCore.Qt.CheckState.Checked)
        self.assertEqual(viewer.topic_tree._drag_topics([rear]), ["/rear"])

    def test_clear_search_restores_original_expansion(self):
        viewer = self.viewer
        self.install_topics("/a/points", "/b/points")
        root = viewer.topic_tree.topLevelItem(0)
        self.assertFalse(root.isExpanded())
        viewer.topic_search.setText("a/points")
        self.assertTrue(root.isExpanded())
        viewer.topic_search.clear()
        self.assertFalse(root.isExpanded())

    def test_topic_that_is_also_a_namespace_keeps_its_metadata(self):
        viewer = self.viewer
        self.install_topics("/camera/image", "/camera", category="image")
        root = viewer.topic_tree.topLevelItem(0)
        self.assertEqual(root.text(1), "image")
        self.assertEqual(root.data(0, QtCore.Qt.ItemDataRole.UserRole), "/camera")
        self.assertEqual(root.childCount(), 1)

    def test_short_final_image_window_can_seek_backward(self):
        pane = self.viewer._panes[0]
        image = QtGui.QImage(4, 4, QtGui.QImage.Format.Format_RGB32)
        image.fill(QtGui.QColor("red"))
        pane.rendered_frames = [RenderedFrame(200, image, 4, 4, "rgb8")]
        pane.rendered_timestamps = [200]
        pane.rendered_window_start_ns = 150
        pane.rendered_reaches_topic_end = True
        self.assertTrue(pane._timestamp_requires_render(0))
        self.assertFalse(pane._timestamp_requires_render(175))
        self.assertFalse(pane._timestamp_requires_render(999))

    def test_point_cloud_group_renders_every_topic_and_own_settings(self):
        viewer = self.viewer
        self.install_topics("/front", "/rear")
        viewer.settings.topics["/rear"].point_size = 6
        calls = []

        def cloud(name, stamp, **kwargs):
            calls.append((name, stamp, kwargs["max_points"]))
            return PointCloudPreview(name, stamp, ["x", "y", "z"], np.array([[1, 2, 0]]), None, 1, 1)

        viewer.preview = SimpleNamespace(point_cloud_preview=cloud)
        pane = viewer._panes[0]
        viewer.assign_topics_to_pane(pane, ["/front", "/rear"])
        pane.show_at_timestamp(0)
        wait_until(lambda: not pane._preview_loading)
        self.assertEqual([row[0] for row in calls], ["/front", "/rear"])
        self.assertEqual(len(pane.point_renderer._clouds), 2)
        self.assertEqual(pane.point_renderer._clouds[1][1].point_size, 6)

    def test_hiding_anchor_cloud_keeps_other_layers_visible(self):
        viewer = self.viewer
        self.install_topics("/front", "/rear")
        viewer.settings.topics["/front"].visible = False
        viewer.settings.topics["/rear"].opacity = 0.4
        viewer.preview = SimpleNamespace(point_cloud_preview=lambda name, stamp, **_: PointCloudPreview(
            name, stamp, ["x", "y", "z"], np.array([[1, 2, 0]]), None, 1, 1))
        pane = viewer._panes[0]
        viewer.assign_topics_to_pane(pane, ["/front", "/rear"])
        pane.show_at_timestamp(0)
        wait_until(lambda: not pane._preview_loading)
        self.assertFalse(pane.stack.isHidden())
        self.assertEqual([cloud.topic for cloud, _ in pane.point_renderer._clouds], ["/rear"])
        self.assertEqual(pane.point_renderer._clouds[0][1].opacity, 0.4)
        self.assertIsNone(pane.stack.graphicsEffect())

    def test_software_point_plot_zoom_pan_resize_and_fit_keep_equal_scale(self):
        plot = create_point_plot(QtWidgets)
        self.addCleanup(plot.close)
        plot.resize(600, 400)
        plot.show()
        cloud = PointCloudPreview("/points", 0, ["x", "y", "z"], np.array([[2, 4, 0], [8, 6, 0]]), None, 2, 2)
        plot.set_clouds([(cloud, TopicDisplaySettings("/points"))])
        original = plot._bounds
        position = plot._plot_rect().center()
        wheel = QtGui.QWheelEvent(position, position, QtCore.QPoint(), QtCore.QPoint(0, 120),
                                  QtCore.Qt.MouseButton.NoButton, QtCore.Qt.KeyboardModifier.NoModifier,
                                  QtCore.Qt.ScrollPhase.NoScrollPhase, False)
        QtWidgets.QApplication.sendEvent(plot, wheel)
        self.assertFalse(plot._auto_fit)
        self.assertLess(plot._bounds[1] - plot._bounds[0], original[1] - original[0])
        before_pan = plot._bounds
        QtTest.QTest.mousePress(plot, QtCore.Qt.MouseButton.MiddleButton, pos=position.toPoint())
        QtTest.QTest.mouseMove(plot, position.toPoint() + QtCore.QPoint(20, 10))
        QtTest.QTest.mouseRelease(plot, QtCore.Qt.MouseButton.MiddleButton)
        self.assertNotEqual(plot._bounds, before_pan)
        plot.resize(800, 300)
        self.app.processEvents()
        xmin, xmax, ymin, ymax = plot._bounds
        rect = plot._plot_rect()
        self.assertAlmostEqual((xmax - xmin) / rect.width(), (ymax - ymin) / rect.height())
        QtTest.QTest.mouseDClick(plot, QtCore.Qt.MouseButton.LeftButton)
        self.assertTrue(plot._auto_fit)
        self.assertLess(plot._bounds[0], 2)
        self.assertGreater(plot._bounds[1], 8)

    def test_render_cancellation_never_publishes_old_topic_frames(self):
        viewer = self.viewer
        topics = self.install_topics("/old", "/new", category="image")
        started, release = Event(), Event()

        def messages(topics=None):
            started.set()
            release.wait(3)
            yield MessageRecord("/old", 0, "sensor_msgs/msg/Image", b"", object())

        viewer.session.reader = SimpleNamespace(iter_messages=messages, close=lambda: None)
        pane = viewer._panes[0]
        pane.set_topic("/old", topics[0])
        try:
            pane.ensure_rendered_for_playback()
            wait_until(started.is_set)
            pane.set_topic("/new", topics[1])
            release.set()
            wait_until(lambda: not viewer._io_tasks.busy)
            self.assertEqual(pane.topic, "/new")
            self.assertFalse(pane.rendered_frames)
            self.assertEqual(pane.render_button.text(), "Render")
        finally:
            release.set()

    def test_seeking_during_render_refills_for_latest_timestamp(self):
        viewer = self.viewer
        topic = self.install_topics("/camera", category="image")[0]
        pane = viewer._panes[0]
        pane.set_topic(topic.name, topic)
        started, release = Event(), Event()
        requests = []

        def load(_reader, _topic, stamp, *_args):
            requests.append(stamp)
            if len(requests) == 1:
                started.set()
                release.wait(3)
            image = QtGui.QImage(4, 4, QtGui.QImage.Format.Format_RGB32)
            image.fill(QtGui.QColor("red"))
            return PlaybackWindow([RenderedFrame(stamp, image, 4, 4, "rgb8")], False, [])

        try:
            with patch("rosbagel.gui.timeline_viewer.load_playback_window", side_effect=load):
                pane.show_at_timestamp(0)
                wait_until(started.is_set)
                pane.show_at_timestamp(200_000_000)
                release.set()
                wait_until(lambda: not viewer._io_tasks.busy)
            self.assertEqual(requests, [0, 200_000_000])
            self.assertEqual(pane.rendered_timestamps, [200_000_000])
        finally:
            release.set()

    def test_cancelled_render_waits_for_explicit_retry(self):
        viewer = self.viewer
        topic = self.install_topics("/camera", category="image")[0]
        pane = viewer._panes[0]
        pane.set_topic(topic.name, topic)
        started, release = Event(), Event()
        requests = []

        def load(_reader, _topic, stamp, *_args):
            requests.append(stamp)
            started.set()
            release.wait(3)
            return PlaybackWindow([], True, [])

        try:
            with patch("rosbagel.gui.timeline_viewer.load_playback_window", side_effect=load):
                pane.show_at_timestamp(0)
                wait_until(started.is_set)
                pane._on_render_clicked()
                release.set()
                wait_until(lambda: not viewer._io_tasks.busy)
                pane.show_at_timestamp(10)
                wait_until(lambda: not viewer._io_tasks.busy)
                self.assertEqual(requests, [0])
                self.assertEqual(pane.frame_status.text(), "Render cancelled")
                pane._on_render_clicked()
                wait_until(lambda: not viewer._io_tasks.busy)
                self.assertEqual(requests, [0, 0])
        finally:
            release.set()

    def test_render_button_applies_topic_sync_offset(self):
        viewer = self.viewer
        topic = self.install_topics("/camera", category="image")[0]
        viewer.settings.topics[topic.name].sync_offset_sec = 0.1
        pane = viewer._panes[0]
        pane.set_topic(topic.name, topic)
        with patch.object(pane, "_render_playback_window", return_value=True) as render:
            pane.ensure_rendered_for_playback()
        render.assert_called_once_with(100_000_000)

    def test_color_change_during_cloud_load_discards_old_settings(self):
        viewer = self.viewer
        viewer.show()
        topic = self.install_topics("/points")[0]
        pane = viewer._panes[0]
        pane.set_topic(topic.name, topic)
        viewer._apply_topic_settings(topic.name)
        started, release = Event(), Event()

        def cloud(name, stamp, **_kwargs):
            started.set()
            release.wait(3)
            return PointCloudPreview(name, stamp, ["x", "y", "z"], np.array([[1, 2, 0]]), None, 1, 1)

        viewer.preview = SimpleNamespace(point_cloud_preview=cloud)
        try:
            pane.show_at_timestamp(0)
            wait_until(started.is_set)
            with patch.object(QtWidgets.QColorDialog, "getColor", return_value=QtGui.QColor("#3366aa")):
                viewer._choose_topic_color()
            viewer.preview_timer.stop()
            viewer._update_preview()
            release.set()
            wait_until(lambda: not viewer._io_tasks.busy)
            self.assertEqual(pane.point_renderer._clouds[0][1].color, "#3366aa")
        finally:
            release.set()

    def test_decode_failure_is_reported_once_and_can_be_retried(self):
        viewer = self.viewer
        topic = self.install_topics("/camera", category="image")[0]
        calls = []

        def messages(topics=None):
            calls.append(1)
            yield MessageRecord("/camera", 0, "sensor_msgs/msg/Image", b"", None)

        viewer.session.reader = SimpleNamespace(iter_messages=messages, close=lambda: None)
        pane = viewer._panes[0]
        pane.set_topic(topic.name, topic)
        pane.show_at_timestamp(0)
        wait_until(lambda: not pane._rendering_playback)
        for _ in range(4):
            pane.show_at_timestamp(0)
        self.assertEqual(len(calls), 1)
        self.assertIn("No decodable", pane.frame_status.text())
        pane._on_render_clicked()
        wait_until(lambda: not pane._rendering_playback)
        self.assertEqual(len(calls), 2)

    def test_uncheck_all_hides_lane_series_in_overlay_and_view(self):
        viewer = self.viewer
        names = [f"/lane_line_publisher/lane_lines/{role}" for role in ("center", "left", "right")]
        topics = self.install_topics(*names)
        viewer._lane_topics_by_role = dict(zip(("center", "left", "right"), topics))
        viewer.lane_overlay.set_topics(viewer._lane_topics_by_role)
        viewer._set_lane_selection(["center", "left", "right"])
        viewer._uncheck_topic_tree()
        self.assertEqual(viewer.lane_overlay.visible_roles(), [])
        self.assertEqual(viewer._lane_roles_for_view(topics[0]), [])

    def test_open_file_action_uses_file_picker(self):
        viewer = self.viewer
        with patch.object(QtWidgets.QFileDialog, "getOpenFileName", return_value=("example.bag", "")), patch.object(viewer, "open_bag") as opening:
            viewer.import_file_action.trigger()
        opening.assert_called_once_with("example.bag")

    def test_reset_layout_recovers_closed_docks_without_losing_views(self):
        viewer = self.viewer
        first = viewer._panes[0]
        viewer.split_pane(first, "horizontal")
        viewer.topic_dock.hide()
        viewer.properties_dock.hide()
        viewer._reset_dock_layout()
        self.assertFalse(viewer.topic_dock.isHidden())
        self.assertFalse(viewer.properties_dock.isHidden())
        self.assertEqual(len(viewer._panes), 2)

    def test_export_dialog_prefills_checks_and_runs_export_off_thread(self):
        viewer = self.viewer
        self.install_topics("/points")
        viewer._set_checked_topics(["/points"])
        viewer.session.bag_path = Path("test.bag")
        errors, threads, exported = [], [], []
        viewer.session.prepare_export_selection = lambda name, fmt, out, fps: SimpleNamespace(topic=name, format=fmt, out_dir=out, fps=fps)

        def export(name, fmt, out, **kwargs):
            threads.append(get_ident())
            with kwargs["progress_factory"]("Exporting points", 2) as advance:
                advance(2)
            exported.append((name, fmt, out))
            return object()

        viewer.session.export_topic = export

        def interact():
            dialog = next(widget for widget in self.app.topLevelWidgets() if widget.windowTitle() == "Export topics")
            try:
                items = dialog.findChild(QtWidgets.QListWidget)
                self.assertEqual(items.item(0).checkState(), QtCore.Qt.CheckState.Checked)
                output = next(widget for widget in dialog.findChildren(QtWidgets.QLineEdit) if widget.accessibleName() == "Output directory")
                output.setText("exports")
                button = dialog.findChild(QtWidgets.QDialogButtonBox).button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
                self.assertTrue(button.isEnabled())
                button.click()
            except BaseException as exc:
                errors.append(exc)
                dialog.reject()

        with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.StandardButton.Yes):
            QtCore.QTimer.singleShot(0, interact)
            QtCore.QTimer.singleShot(5000, viewer.window, lambda: [widget.reject() for widget in self.app.topLevelWidgets() if isinstance(widget, QtWidgets.QDialog) and widget.isVisible()])
            viewer._show_export_dialog()
        if errors:
            raise errors[0]
        self.assertEqual(len(exported), 1)
        self.assertNotEqual(threads[0], get_ident())
        self.assertFalse(viewer._exporting)

    def test_failed_open_preserves_previous_bag_and_timeline(self):
        viewer = self.viewer
        self.install_topics("/points")
        previous = viewer.session
        viewer.time_input.setValue(0.1)
        failures = []
        viewer._show_warning = failures.append
        with patch("rosbagel.gui.timeline_viewer.Session") as factory:
            factory.return_value.open_bag.side_effect = OSError("missing bag")
            viewer.open_bag("missing.bag")
            wait_until(lambda: not viewer._opening)
        self.assertIs(viewer.session, previous)
        self.assertEqual(viewer.time_input.value(), 0.1)
        self.assertIn("missing bag", failures[0])
        self.assertTrue(viewer.play_button.isEnabled())

    def test_export_failure_and_cancellation_restore_dialog_state(self):
        viewer = self.viewer
        self.install_topics("/points")
        viewer._set_checked_topics(["/points"])
        viewer.session.prepare_export_selection = lambda name, fmt, out, fps: SimpleNamespace(topic=name, format=fmt, out_dir=out, fps=fps)
        for mode in ("failure", "cancel"):
            with self.subTest(mode=mode):
                started = Event()
                errors = []

                def export(*args, **kwargs):
                    started.set()
                    if mode == "failure":
                        raise OSError("output is read-only")
                    with kwargs["progress_factory"]("Exporting", 10000) as advance:
                        for _ in range(10000):
                            time.sleep(0.001)
                            advance(1)

                viewer.session.export_topic = export
                interaction = QtCore.QTimer(viewer.window)
                interaction.setInterval(10)
                state = {"submitted": False, "cancelled": False}
                deadline = time.monotonic() + 5

                def interact():
                    dialog = next((widget for widget in self.app.topLevelWidgets() if widget.windowTitle() == "Export topics" and widget.isVisible()), None)
                    if dialog is None:
                        return
                    try:
                        if time.monotonic() > deadline:
                            raise AssertionError("Export dialog did not recover")
                        buttons = dialog.findChild(QtWidgets.QDialogButtonBox)
                        if not state["submitted"]:
                            output = next(widget for widget in dialog.findChildren(QtWidgets.QLineEdit) if widget.accessibleName() == "Output directory")
                            output.setText("exports")
                            state["submitted"] = True
                            buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).click()
                        elif mode == "cancel" and started.is_set() and not state["cancelled"]:
                            state["cancelled"] = True
                            buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).click()
                        elif started.is_set() and not viewer._exporting:
                            self.assertTrue(buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).isEnabled())
                            interaction.stop()
                            dialog.reject()
                    except BaseException as exc:
                        errors.append(exc)
                        interaction.stop()
                        dialog.done(QtWidgets.QDialog.DialogCode.Rejected)

                interaction.timeout.connect(interact)
                interaction.start()
                with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.StandardButton.Yes):
                    viewer._show_export_dialog()
                interaction.stop()
                if errors:
                    raise errors[0]
                self.assertFalse(viewer._exporting)
                expected = "read-only" if mode == "failure" else "partial output files may remain"
                self.assertIn(expected, viewer.log_text.toPlainText())

    def test_open_runs_off_thread_and_commits_only_complete_session(self):
        viewer = self.viewer
        started, release = Event(), Event()
        thread_ids = []
        topic = TopicInfo("/camera", "sensor_msgs/msg/Image", category="image")

        def opening(path):
            thread_ids.append(get_ident())
            started.set()
            release.wait(3)
            return [topic]

        session = SimpleNamespace(open_bag=opening, reader=SimpleNamespace(get_time_bounds=lambda: (10, 250_000_010)),
                                  close=lambda: None, topics=[topic], bag_path=Path("test.bag"), backend="auto",
                                  list_topics=lambda: [topic])
        try:
            with patch("rosbagel.gui.timeline_viewer.Session", return_value=session):
                viewer.open_bag("test.bag")
                wait_until(started.is_set)
                self.assertTrue(viewer._opening)
                self.assertIsNot(viewer.session, session)
                release.set()
                wait_until(lambda: not viewer._opening)
            self.assertNotEqual(thread_ids[0], get_ident())
            self.assertIs(viewer.session, session)
            self.assertAlmostEqual(viewer.time_input.maximum(), 0.25)
            self.assertEqual(viewer.time_input.value(), 0)
        finally:
            release.set()

    def test_dropping_view_shows_target_and_moves_existing_pane(self):
        viewer = self.viewer
        first = viewer._panes[0]
        second = viewer.split_pane(first, "horizontal")
        viewer.show()
        self.app.processEvents()
        mime = QtCore.QMimeData()
        mime.setData(VIEW_MIME, str(id(second)).encode("ascii"))
        position = QtCore.QPoint(10, first.height() // 2)
        enter = QtGui.QDragEnterEvent(position, QtCore.Qt.DropAction.MoveAction, mime,
                                     QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.KeyboardModifier.NoModifier)
        QtWidgets.QApplication.sendEvent(first, enter)
        self.assertTrue(enter.isAccepted())
        move = QtGui.QDragMoveEvent(position, QtCore.Qt.DropAction.MoveAction, mime,
                                   QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.KeyboardModifier.NoModifier)
        QtWidgets.QApplication.sendEvent(first, move)
        self.assertFalse(first.drop_indicator.isHidden())
        self.assertEqual(first.drop_indicator.text(), "Split left")
        drop = QtGui.QDropEvent(QtCore.QPointF(position), QtCore.Qt.DropAction.MoveAction, mime,
                               QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.KeyboardModifier.NoModifier)
        QtWidgets.QApplication.sendEvent(first, drop)
        self.assertTrue(drop.isAccepted())
        self.assertIs(second.parentWidget().widget(0), second)
        self.assertTrue(first.drop_indicator.isHidden())


if __name__ == "__main__":
    unittest.main()
