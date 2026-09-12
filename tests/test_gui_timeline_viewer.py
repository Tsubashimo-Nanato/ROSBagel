from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
import time
from unittest.mock import patch

import numpy as np

from rosbagel.core.lane_lines import LaneFrame, LaneOverlayData, LanePoint, LaneSeries
from rosbagel.core.models import MessageRecord
from rosbagel.core.models import TopicInfo
from rosbagel.core.preview import TopicDisplaySettings
from rosbagel.gui.timeline_viewer import (
    MAX_RENDERED_PLAYBACK_FRAMES,
    TOPICS_MIME,
    TimelineViewer,
)


if importlib.util.find_spec("PySide6") is None:
    raise unittest.SkipTest("PySide6 is not installed")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6 import QtCore, QtGui, QtWidgets, QtTest  # noqa: E402


class DummyReader:
    def close(self) -> None:
        return None


class ImageReader:
    def __init__(self, count: int, *, first_timestamp_ns: int = 0) -> None:
        self.count = count
        self.first_timestamp_ns = first_timestamp_ns
        self.iteration_count = 0

    def iter_messages(self, topics: list[str] | None = None) -> object:
        self.iteration_count += 1
        topic = (topics or ["/camera"])[0]
        for index in range(self.count):
            yield MessageRecord(
                topic=topic,
                timestamp_ns=self.first_timestamp_ns + index,
                msgtype="sensor_msgs/msg/Image",
                raw=b"raw",
                decoded=object(),
            )

    def close(self) -> None:
        return None


class GuiTimelineViewerTests(unittest.TestCase):
    def setUp(self) -> None:
        settings = patch.object(TimelineViewer, "_maybe_offer_startup_update_check")
        settings.start()
        self.addCleanup(settings.stop)
        values = {"updates/mode": "off", "ui/theme": "dark"}
        preferences = patch.object(QtCore, "QSettings", return_value=SimpleNamespace(
            value=lambda key, default=None: values.get(key, default),
            setValue=lambda key, value: values.__setitem__(key, value),
        ))
        preferences.start()
        self.addCleanup(preferences.stop)

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_window_menu_exposes_dock_toggles(self) -> None:
        viewer = TimelineViewer()
        try:
            self.assertEqual(
                [action.text() for action in viewer.window.menuBar().actions()],
                ["File", "Theme", "Windows", "Analysis"],
            )
            self.assertIn("Version...", [action.text() for action in viewer._file_menu.actions()])
            self.assertEqual(
                [action.text() for action in viewer._theme_menu.actions()],
                ["Light mode", "Dark mode"],
            )
            self.assertEqual(viewer.theme_toggle.text(), "Dark mode")
            self.assertEqual(viewer.theme_toggle.objectName(), "themeToggle")
            self.assertEqual(
                list(viewer._dock_widgets),
                ["Topic list", "Main view", "Lane line overlay", "Properties", "Output", "Message inspector", "Bag analysis", "Numeric chart"],
            )
            for dock in viewer._dock_widgets.values():
                self.assertTrue(dock.toggleViewAction().isCheckable())
            self.assertEqual(viewer.theme_toggle.text(), "Dark mode")
            self.assertEqual(viewer.theme_toggle.objectName(), "themeToggle")
        finally:
            viewer.window.close()

    def test_main_view_titlebar_labels_view_area(self) -> None:
        viewer = TimelineViewer()
        try:
            self.assertEqual(viewer.main_view_title.text(), "Views")
            self.assertTrue(viewer._panes[0].property("active"))
        finally:
            viewer.window.close()

    def test_view_split_button_splits_active_view(self) -> None:
        viewer = TimelineViewer()
        try:
            pane = viewer._panes[0]
            self.assertEqual(pane.split_button.text(), "+")
            self.assertEqual(len(viewer._panes), 1)

            pane.split_button.click()

            self.assertEqual(len(viewer._panes), 2)
            self.assertEqual(viewer._panes[1].title_label.text(), "View 2")
            self.assertFalse(viewer._panes[0].property("active"))
            self.assertTrue(viewer._panes[1].property("active"))
        finally:
            viewer.window.close()

    def test_version_action_ignores_qt_checked_argument(self) -> None:
        viewer = TimelineViewer()
        calls = []
        try:
            viewer._show_version_dialog = lambda: calls.append("opened")  # type: ignore[method-assign]

            viewer._on_version_action_triggered(False)

            self.assertEqual(calls, ["opened"])
        finally:
            viewer.window.close()

    def test_topic_tree_is_folded_after_population(self) -> None:
        viewer = TimelineViewer()
        try:
            viewer.session.reader = DummyReader()  # type: ignore[assignment]
            viewer.session.topics = [
                TopicInfo(
                    name="/aiformula/camera/image_raw",
                    msgtype="sensor_msgs/msg/Image",
                    category="image",
                )
            ]
            viewer._populate_topics()
            top = viewer.topic_tree.topLevelItem(0)
            self.assertIsNotNone(top)
            self.assertFalse(top.isExpanded())
            self.assertEqual(viewer._panes[0].title_label.text(), "View 1")
            self.assertGreaterEqual(viewer.topic_tree.columnWidth(0), 150)
        finally:
            viewer.window.close()

    def test_topic_panel_exposes_search_and_fold_controls(self) -> None:
        viewer = TimelineViewer()
        try:
            self.assertEqual(viewer.topic_search.placeholderText(), "Search topics")
            self.assertEqual(viewer.topic_expand_button.accessibleName(), "Expand all")
            self.assertEqual(viewer.topic_collapse_button.accessibleName(), "Collapse all")
            self.assertEqual(viewer.topic_uncheck_button.accessibleName(), "Uncheck all")
            self.assertTrue(viewer.topic_expand_button.toolTip())
            self.assertTrue(viewer.topic_collapse_button.toolTip())
            self.assertTrue(viewer.topic_uncheck_button.toolTip())
            self.assertIs(viewer.topic_dock.widget(), viewer.topic_panel)
        finally:
            viewer.window.close()

    def test_topic_tree_distinguishes_groups_from_topic_leaves(self) -> None:
        viewer = TimelineViewer()
        try:
            viewer.session.reader = DummyReader()  # type: ignore[assignment]
            viewer.session.topics = [
                TopicInfo(
                    name="/aiformula/camera/image_raw",
                    msgtype="sensor_msgs/msg/Image",
                    category="image",
                )
            ]
            viewer._populate_topics()

            root = viewer.topic_tree.topLevelItem(0)
            camera = root.child(0)
            leaf = camera.child(0)

            self.assertTrue(root.font(0).bold())
            self.assertTrue(root.flags() & QtCore.Qt.ItemFlag.ItemIsSelectable)
            self.assertTrue(root.flags() & QtCore.Qt.ItemFlag.ItemIsDragEnabled)
            self.assertIsNone(root.data(0, QtCore.Qt.ItemDataRole.UserRole))
            self.assertTrue(leaf.flags() & QtCore.Qt.ItemFlag.ItemIsSelectable)
            self.assertTrue(leaf.flags() & QtCore.Qt.ItemFlag.ItemIsDragEnabled)
            self.assertEqual(
                leaf.data(0, QtCore.Qt.ItemDataRole.UserRole),
                "/aiformula/camera/image_raw",
            )
        finally:
            viewer.window.close()

    def test_topic_single_click_selects_without_assigning_view(self) -> None:
        viewer = TimelineViewer()
        try:
            topic = TopicInfo(
                name="/aiformula/camera/image_raw",
                msgtype="sensor_msgs/msg/Image",
                category="image",
            )
            viewer.session.reader = DummyReader()  # type: ignore[assignment]
            viewer.session.topics = [topic]
            viewer.settings.topics = {
                topic.name: TopicDisplaySettings(topic=topic.name),
            }
            viewer._populate_topics()
            leaf = _tree_item(viewer.topic_tree, "image_raw")

            viewer.topic_tree.setCurrentItem(leaf)

            self.assertIsNone(viewer._panes[0].topic)
        finally:
            viewer.window.close()

    def test_topic_double_click_assigns_selected_topic_to_active_view(self) -> None:
        viewer = TimelineViewer()
        try:
            topic = TopicInfo(
                name="/aiformula/camera/image_raw",
                msgtype="sensor_msgs/msg/Image",
                category="image",
            )
            viewer.session.reader = DummyReader()  # type: ignore[assignment]
            viewer.session.topics = [topic]
            viewer._topic_info_by_name = {topic.name: topic}
            viewer.settings.topics = {
                topic.name: TopicDisplaySettings(topic=topic.name),
            }
            viewer._populate_topics()
            leaf = _tree_item(viewer.topic_tree, "image_raw")

            viewer._on_topic_double_clicked(leaf, 0)

            self.assertEqual(viewer._panes[0].topic, topic.name)
        finally:
            viewer.window.close()

    def test_topic_search_filters_to_matching_topic_path(self) -> None:
        viewer = TimelineViewer()
        try:
            viewer.session.reader = DummyReader()  # type: ignore[assignment]
            viewer.session.topics = [
                TopicInfo(
                    name="/aiformula/camera/image_raw",
                    msgtype="sensor_msgs/msg/Image",
                    category="image",
                ),
                TopicInfo(
                    name="/aiformula/imu",
                    msgtype="sensor_msgs/msg/Imu",
                    category="unknown_raw",
                ),
                TopicInfo(
                    name="/vehicle/points",
                    msgtype="sensor_msgs/msg/PointCloud2",
                    category="point_cloud",
                ),
            ]
            viewer._populate_topics()

            viewer.topic_search.setText("camera")

            aiformula = _tree_item(viewer.topic_tree, "aiformula")
            camera = _tree_item(viewer.topic_tree, "camera")
            imu = _tree_item(viewer.topic_tree, "imu")
            vehicle = _tree_item(viewer.topic_tree, "vehicle")
            self.assertFalse(aiformula.isHidden())
            self.assertFalse(camera.isHidden())
            self.assertTrue(imu.isHidden())
            self.assertTrue(vehicle.isHidden())
            self.assertTrue(aiformula.isExpanded())
        finally:
            viewer.window.close()

    def test_topic_expand_and_collapse_buttons_control_tree(self) -> None:
        viewer = TimelineViewer()
        try:
            viewer.session.reader = DummyReader()  # type: ignore[assignment]
            viewer.session.topics = [
                TopicInfo(
                    name="/aiformula/camera/image_raw",
                    msgtype="sensor_msgs/msg/Image",
                    category="image",
                )
            ]
            viewer._populate_topics()
            root = viewer.topic_tree.topLevelItem(0)

            viewer.topic_expand_button.click()
            self.assertTrue(root.isExpanded())

            viewer.topic_search.setText("camera")
            viewer.topic_collapse_button.click()
            self.assertEqual(viewer.topic_search.text(), "")
            self.assertFalse(root.isExpanded())
            self.assertFalse(root.isHidden())
        finally:
            viewer.window.close()

    def test_topic_uncheck_all_clears_checked_topics(self) -> None:
        viewer = TimelineViewer()
        try:
            viewer.session.reader = DummyReader()  # type: ignore[assignment]
            viewer.session.topics = [_lane_topic("center"), _lane_topic("right")]
            viewer._populate_topics()
            center = _tree_item(viewer.topic_tree, "center")
            right = _tree_item(viewer.topic_tree, "right")
            center.setCheckState(0, QtCore.Qt.CheckState.Checked)
            right.setCheckState(0, QtCore.Qt.CheckState.Checked)

            viewer.topic_uncheck_button.click()

            self.assertEqual(center.checkState(0), QtCore.Qt.CheckState.Unchecked)
            self.assertEqual(right.checkState(0), QtCore.Qt.CheckState.Unchecked)
            self.assertEqual(viewer._checked_lane_roles(), [])
        finally:
            viewer.window.close()

    def test_topic_tree_drag_checked_topics_uses_multi_mime(self) -> None:
        viewer = TimelineViewer()
        try:
            viewer.session.reader = DummyReader()  # type: ignore[assignment]
            viewer.session.topics = [
                TopicInfo(
                    name="/aiformula/camera/image_raw",
                    msgtype="sensor_msgs/msg/Image",
                    category="image",
                ),
                TopicInfo(
                    name="/aiformula/points",
                    msgtype="sensor_msgs/msg/PointCloud2",
                    category="point_cloud",
                ),
            ]
            viewer._populate_topics()
            image = _tree_item(viewer.topic_tree, "image_raw")
            points = _tree_item(viewer.topic_tree, "points")
            image.setCheckState(0, QtCore.Qt.CheckState.Checked)
            points.setCheckState(0, QtCore.Qt.CheckState.Checked)

            mime = viewer.topic_tree.mimeData([image])

            self.assertTrue(mime.hasFormat(TOPICS_MIME))
            self.assertEqual(
                bytes(mime.data(TOPICS_MIME)).decode("utf-8").splitlines(),
                ["/aiformula/camera/image_raw", "/aiformula/points"],
            )
        finally:
            viewer.window.close()

    def test_topic_tree_drag_folder_includes_descendant_topics(self) -> None:
        viewer = TimelineViewer()
        try:
            viewer.session.reader = DummyReader()  # type: ignore[assignment]
            viewer.session.topics = [
                TopicInfo(
                    name="/aiformula/camera/image_raw",
                    msgtype="sensor_msgs/msg/Image",
                    category="image",
                ),
                TopicInfo(
                    name="/aiformula/imu",
                    msgtype="sensor_msgs/msg/Imu",
                    category="unknown_raw",
                ),
            ]
            viewer._populate_topics()
            folder = _tree_item(viewer.topic_tree, "aiformula")

            mime = viewer.topic_tree.mimeData([folder])

            self.assertEqual(
                bytes(mime.data(TOPICS_MIME)).decode("utf-8").splitlines(),
                ["/aiformula/camera/image_raw", "/aiformula/imu"],
            )
        finally:
            viewer.window.close()

    def test_central_spacer_is_collapsed_for_dock_layout(self) -> None:
        viewer = TimelineViewer()
        try:
            self.assertEqual(viewer.central_spacer.maximumWidth(), 0)
            self.assertEqual(viewer.central_spacer.minimumWidth(), 0)
        finally:
            viewer.window.close()

    def test_view_pane_uses_new_window_control(self) -> None:
        viewer = TimelineViewer()
        try:
            pane = viewer._panes[0]
            self.assertEqual(pane.new_window_button.accessibleName(), "New window")
            self.assertTrue(pane.new_window_button.toolTip())
            self.assertTrue(callable(viewer.duplicate_pane_window))
        finally:
            viewer.window.close()

    def test_multi_topic_drop_splits_conflicting_topics(self) -> None:
        viewer = TimelineViewer()
        try:
            topics = [
                TopicInfo(
                    name="/camera",
                    msgtype="sensor_msgs/msg/Image",
                    category="image",
                ),
                TopicInfo(
                    name="/imu",
                    msgtype="sensor_msgs/msg/Imu",
                    category="unknown_raw",
                ),
            ]
            viewer._topic_info_by_name = {topic.name: topic for topic in topics}

            viewer.assign_topics_to_pane(viewer._panes[0], ["/camera", "/imu"])

            self.assertEqual(len(viewer._panes), 2)
            self.assertEqual(viewer._panes[0].topic, "/camera")
            self.assertEqual(viewer._panes[1].topic, "/imu")
        finally:
            viewer.window.close()

    def test_multi_point_cloud_drop_uses_one_view(self) -> None:
        viewer = TimelineViewer()
        try:
            topics = [
                TopicInfo(
                    name="/points/a",
                    msgtype="sensor_msgs/msg/PointCloud2",
                    category="point_cloud",
                ),
                TopicInfo(
                    name="/points/b",
                    msgtype="sensor_msgs/msg/PointCloud2",
                    category="point_cloud",
                ),
            ]
            viewer._topic_info_by_name = {topic.name: topic for topic in topics}

            viewer.assign_topics_to_pane(viewer._panes[0], ["/points/a", "/points/b"])

            self.assertEqual(len(viewer._panes), 1)
            self.assertEqual(viewer._panes[0].topic, "/points/a")
        finally:
            viewer.window.close()

    def test_show_without_open_bag_does_not_crash_autosize(self) -> None:
        viewer = TimelineViewer()
        try:
            viewer._update_settings.setValue("updates/mode", "off")
            viewer.show()
            for _index in range(10):
                self.app.processEvents()
            self.assertIsNone(viewer.session.reader)
        finally:
            viewer.window.close()

    def test_topic_tree_width_uses_topic_data(self) -> None:
        viewer = TimelineViewer()
        try:
            viewer.session.reader = DummyReader()  # type: ignore[assignment]
            viewer.session.topics = [
                TopicInfo(
                    name="/aiformula_perception/road_detector/annotated_mask_image",
                    msgtype="sensor_msgs/msg/Image",
                    category="mask_candidate",
                    message_count=1809,
                ),
                TopicInfo(
                    name="/aiformula_visualization/processed_point_cloud",
                    msgtype="sensor_msgs/msg/PointCloud2",
                    category="point_cloud",
                    message_count=1821,
                ),
            ]
            viewer._populate_topics()
            self.assertGreaterEqual(viewer._preferred_topic_width(), 360)
            self.assertGreaterEqual(viewer._topic_width_target(), 360)
            self.assertEqual(viewer.topic_tree.minimumWidth(), 240)
        finally:
            viewer.window.close()

    def test_split_view_uses_compact_topic_navigation_width(self) -> None:
        viewer = TimelineViewer()
        try:
            viewer.session.reader = DummyReader()  # type: ignore[assignment]
            viewer.session.topics = [
                TopicInfo(
                    name="/aiformula_perception/road_detector/annotated_mask_image",
                    msgtype="sensor_msgs/msg/Image",
                    category="mask_candidate",
                    message_count=1809,
                ),
            ]
            viewer._populate_topics()
            self.assertGreater(viewer._preferred_topic_width(), 320)

            viewer.split_pane(viewer._panes[0], "horizontal")

            self.assertEqual(viewer._topic_width_target(), 240)
            self.assertEqual(viewer.topic_tree.minimumWidth(), 240)
            self.assertEqual(viewer.topic_dock.minimumWidth(), 240)
            self.assertLessEqual(viewer.window.minimumSizeHint().width(), 1280)
            self.assertLessEqual(viewer.window.minimumSizeHint().height(), 760)
        finally:
            viewer.window.close()

    def test_playback_rate_selector_updates_rate(self) -> None:
        viewer = TimelineViewer()
        try:
            viewer.rate_box.setCurrentText("4x")
            self.assertEqual(viewer._playback_rate, 4.0)
        finally:
            viewer.window.close()

    def test_theme_switch_updates_stylesheet(self) -> None:
        viewer = TimelineViewer()
        try:
            viewer._set_theme("dark")
            self.assertEqual(viewer._theme, "dark")
            self.assertTrue(viewer.theme_toggle.isChecked())
            self.assertIn("#101010", viewer.window.styleSheet())
            self.assertIn("QHeaderView::section", viewer.window.styleSheet())
            self.assertIn("QWidget#propertiesPanel", viewer.window.styleSheet())

            viewer._set_theme("light")
            self.assertEqual(viewer._theme, "light")
            self.assertFalse(viewer.theme_toggle.isChecked())
            self.assertIn("#edf0f3", viewer.window.styleSheet())
            self.assertIn("QTreeWidget::indicator", viewer.window.styleSheet())
        finally:
            viewer.window.close()

    def test_theme_toggle_switches_gui_theme(self) -> None:
        viewer = TimelineViewer()
        try:
            viewer._set_theme("light")

            viewer.theme_toggle.click()
            self.assertEqual(viewer._theme, "dark")

            viewer.theme_toggle.click()
            self.assertEqual(viewer._theme, "light")
        finally:
            viewer.window.close()

    def test_theme_menu_switches_modes(self) -> None:
        viewer = TimelineViewer()
        try:
            viewer._theme_actions["light"].trigger()
            self.assertEqual(viewer._theme, "light")
            self.assertTrue(viewer._theme_actions["light"].isChecked())
            self.assertFalse(viewer._theme_actions["dark"].isChecked())

            viewer._theme_actions["dark"].trigger()
            self.assertEqual(viewer._theme, "dark")
            self.assertFalse(viewer._theme_actions["light"].isChecked())
            self.assertTrue(viewer._theme_actions["dark"].isChecked())
        finally:
            viewer.window.close()

    def test_default_theme_is_dark_without_saved_preference(self) -> None:
        with patch.object(QtCore, "QSettings") as settings_class:
            settings_class.return_value.value.side_effect = (
                lambda key, default="": "off" if key == "updates/mode" else default
            )
            viewer = TimelineViewer()
            try:
                self.assertEqual(viewer._theme, "dark")
                self.assertTrue(viewer._theme_actions["dark"].isChecked())
                self.assertIn("#101010", viewer.window.styleSheet())
            finally:
                viewer.window.close()

        settings_class.assert_called_once_with("TsubashimoNanato", "rosbagel")
        settings_class.return_value.value.assert_any_call("ui/theme", "")

    def test_update_check_starts_background_job(self) -> None:
        viewer = TimelineViewer()
        calls = []
        try:
            def fake_background(**kwargs: object) -> None:
                calls.append(kwargs)

            viewer._run_background = fake_background  # type: ignore[method-assign]
            viewer._start_update_check(show_no_update=False)

            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["title"], "Checking for updates")
        finally:
            viewer.window.close()

    def test_lane_overlay_detects_topics_and_loads_frames(self) -> None:
        viewer = TimelineViewer()
        calls = []
        try:
            def fake_background(**kwargs: object) -> None:
                calls.append(kwargs)
                kwargs["on_success"](_lane_overlay_data())  # type: ignore[index, operator]

            viewer._run_background = fake_background  # type: ignore[method-assign]
            viewer.session.reader = DummyReader()  # type: ignore[assignment]
            viewer.session.bag_path = Path("fake")
            viewer._prepare_lane_overlay([
                _lane_topic("center"),
                _lane_topic("left"),
                _lane_topic("right"),
            ])

            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["title"], "Loading lane line overlay")
            self.assertIsNotNone(viewer._lane_overlay_data)
            for role in ("center", "left", "right"):
                self.assertTrue(viewer.lane_overlay.checkboxes[role].isEnabled())
                self.assertTrue(viewer.lane_overlay.checkboxes[role].isChecked())
            self.assertIn("center: 1 frames", viewer.lane_overlay.status_label.text())
        finally:
            viewer.window.close()

    def test_lane_overlay_checkbox_toggle_changes_visible_series(self) -> None:
        viewer = TimelineViewer()
        try:
            viewer.lane_overlay.set_topics({
                "center": _lane_topic("center"),
                "left": _lane_topic("left"),
                "right": _lane_topic("right"),
            })
            viewer.lane_overlay.set_data(_lane_overlay_data())

            viewer.lane_overlay.checkboxes["right"].setChecked(False)

            self.assertEqual(viewer.lane_overlay.visible_roles(), ["center", "left"])
            self.assertEqual(viewer.lane_overlay.plot._visible_roles, {"center", "left"})
        finally:
            viewer.window.close()

    def test_lane_overlay_receives_timeline_timestamp(self) -> None:
        viewer = TimelineViewer()
        try:
            viewer._bag_start_ns = 1_000
            viewer.time_input.setRange(0.0, 1.0)
            viewer.time_input.blockSignals(True)
            viewer.time_input.setValue(0.5)
            viewer.time_input.blockSignals(False)

            viewer._update_preview()

            self.assertEqual(viewer.lane_overlay.current_timestamp_ns, 500_001_000)
        finally:
            viewer.window.close()

    def test_lane_overlay_plot_renders_lane_pixels(self) -> None:
        viewer = TimelineViewer()
        try:
            viewer.lane_overlay.set_topics({
                "center": _lane_topic("center"),
                "left": _lane_topic("left"),
                "right": _lane_topic("right"),
            })
            viewer.lane_overlay.set_data(_lane_overlay_data())
            viewer.lane_overlay.show_at_timestamp(100)
            viewer.lane_overlay.plot.resize(360, 280)
            image = QtGui.QImage(
                viewer.lane_overlay.plot.size(),
                QtGui.QImage.Format.Format_ARGB32,
            )
            image.fill(QtGui.QColor("#000000"))
            painter = QtGui.QPainter(image)
            viewer.lane_overlay.plot.render(painter, viewer.QtCore.QPoint(0, 0))
            painter.end()

            amber_pixels = 0
            for y in range(image.height()):
                for x in range(image.width()):
                    color = image.pixelColor(x, y)
                    if color.red() > 180 and color.green() > 130 and color.blue() < 130:
                        amber_pixels += 1

            self.assertGreater(amber_pixels, 0)
        finally:
            viewer.window.close()

    def test_lane_plot_zoom_pan_and_reset_view_bounds(self) -> None:
        viewer = TimelineViewer()
        try:
            viewer.lane_overlay.set_topics({
                "center": _lane_topic("center"),
                "left": _lane_topic("left"),
                "right": _lane_topic("right"),
            })
            viewer.lane_overlay.set_data(_lane_overlay_data())
            viewer.lane_overlay.show_at_timestamp(100)
            plot = viewer.lane_overlay.plot
            plot.resize(480, 320)
            plot.reset_view()
            original = plot._view_bounds
            data_bounds = plot._data_bounds
            self.assertIsNotNone(original)
            self.assertIsNotNone(data_bounds)
            self.assertLess(original.min_x, data_bounds.min_x)
            self.assertGreater(original.max_x, data_bounds.max_x)
            self.assertLess(original.min_y, data_bounds.min_y)
            self.assertGreater(original.max_y, data_bounds.max_y)

            plot._zoom_at_plot_position(QtCore.QPointF(240.0, 160.0), 120)
            zoomed = plot._view_bounds
            self.assertIsNotNone(zoomed)
            self.assertTrue(plot._view_is_custom)
            self.assertLess(zoomed.max_x - zoomed.min_x, original.max_x - original.min_x)
            self.assertLess(zoomed.max_y - zoomed.min_y, original.max_y - original.min_y)

            plot._pan_start_bounds = zoomed
            plot._pan_view_by_pixels(40.0, -20.0)
            panned = plot._view_bounds
            self.assertIsNotNone(panned)
            self.assertNotEqual(panned.min_x, zoomed.min_x)
            self.assertNotEqual(panned.min_y, zoomed.min_y)

            plot.reset_view()
            self.assertEqual(plot._view_bounds, original)
            self.assertFalse(plot._view_is_custom)
        finally:
            viewer.window.close()

    def test_lane_plot_swap_xy_changes_axes_and_bounds(self) -> None:
        viewer = TimelineViewer()
        try:
            viewer.lane_overlay.set_topics({
                "center": _lane_topic("center"),
                "left": _lane_topic("left"),
                "right": _lane_topic("right"),
            })
            viewer.lane_overlay.set_data(_lane_overlay_data())
            normal = viewer.lane_overlay.plot._data_bounds
            self.assertIsNotNone(normal)

            viewer.lane_overlay.plot.set_swap_xy(True)
            swapped = viewer.lane_overlay.plot._data_bounds

            self.assertEqual(viewer.lane_overlay.plot._axis_labels(), ("y", "x"))
            self.assertIsNotNone(swapped)
            self.assertEqual(swapped.min_x, -normal.max_y)
            self.assertEqual(swapped.max_x, -normal.min_y)
            self.assertEqual(swapped.min_y, normal.min_x)
            self.assertEqual(swapped.max_y, normal.max_x)
        finally:
            viewer.window.close()

    def test_lane_plot_auto_fit_uses_current_frame_bounds(self) -> None:
        viewer = TimelineViewer()
        try:
            data = LaneOverlayData(
                series_by_role={
                    "center": LaneSeries(
                        role="center",
                        topic=_lane_topic("center").name,
                        frames=[
                            LaneFrame(
                                timestamp_ns=100,
                                points=(LanePoint(2.0, 0.0), LanePoint(10.0, 1.0)),
                            ),
                            LaneFrame(
                                timestamp_ns=200,
                                points=(LanePoint(2.0, 500.0), LanePoint(10.0, 600.0)),
                            ),
                        ],
                    )
                }
            )
            viewer.lane_overlay.set_topics({"center": _lane_topic("center")})
            viewer.lane_overlay.set_data(data)
            plot = viewer.lane_overlay.plot
            plot.resize(480, 320)

            viewer.lane_overlay.show_at_timestamp(100)
            first_bounds = plot._view_bounds

            self.assertIsNotNone(first_bounds)
            self.assertLess(first_bounds.max_y, 100.0)

            viewer.lane_overlay.show_at_timestamp(200)
            second_bounds = plot._view_bounds

            self.assertIsNotNone(second_bounds)
            self.assertGreater(second_bounds.min_y, 480.0)
        finally:
            viewer.window.close()

    def test_lane_plot_auto_fit_matches_plot_aspect_ratio(self) -> None:
        viewer = TimelineViewer()
        try:
            viewer.lane_overlay.set_topics({
                "center": _lane_topic("center"),
                "left": _lane_topic("left"),
                "right": _lane_topic("right"),
            })
            viewer.lane_overlay.set_data(_lane_overlay_data())
            viewer.lane_overlay.show_at_timestamp(100)
            plot = viewer.lane_overlay.plot
            plot.resize(480, 320)
            plot.reset_view()
            bounds = plot._active_bounds()
            plot_rect = plot._plot_rect(plot.rect())

            self.assertIsNotNone(bounds)
            bounds_ratio = (bounds.max_x - bounds.min_x) / (bounds.max_y - bounds.min_y)
            self.assertAlmostEqual(bounds_ratio, plot_rect.width() / plot_rect.height(), places=6)
            _scale, x_pad, y_pad = plot._plot_transform(plot_rect, bounds)
            self.assertAlmostEqual(x_pad, 0.0, places=6)
            self.assertAlmostEqual(y_pad, 0.0, places=6)
        finally:
            viewer.window.close()

    def test_lane_plot_swapped_axes_places_left_lane_on_left(self) -> None:
        viewer = TimelineViewer()
        try:
            viewer.lane_overlay.set_topics({
                "center": _lane_topic("center"),
                "left": _lane_topic("left"),
                "right": _lane_topic("right"),
            })
            viewer.lane_overlay.set_data(_lane_position_data())
            viewer.lane_overlay.show_at_timestamp(100)
            plot = viewer.lane_overlay.plot
            plot.resize(480, 320)
            plot.set_swap_xy(True)
            bounds = plot._active_bounds()
            self.assertIsNotNone(bounds)
            mapper = plot._point_mapper(plot._plot_rect(plot.rect()), bounds)

            left_x = mapper(LanePoint(5.0, 2.0)).x()
            center_x = mapper(LanePoint(5.0, 0.0)).x()
            right_x = mapper(LanePoint(5.0, -2.0)).x()

            self.assertLess(left_x, center_x)
            self.assertLess(center_x, right_x)
        finally:
            viewer.window.close()

    def test_lane_overlay_exposes_plot_help_indicator(self) -> None:
        viewer = TimelineViewer()
        try:
            self.assertEqual(viewer.lane_overlay.help_button.objectName(), "lanePlotHelpIndicator")
            self.assertIn("Wheel", viewer.lane_overlay.help_button.toolTip())
            rect = viewer.lane_overlay.plot._help_indicator_rect(viewer.lane_overlay.plot.rect())
            self.assertGreater(rect.width(), 0)
            self.assertGreater(rect.height(), 0)
        finally:
            viewer.window.close()

    def test_lane_xy_controls_sync_overlay_and_view_pane(self) -> None:
        viewer = TimelineViewer()
        try:
            topics = {role: _lane_topic(role) for role in ("center", "left", "right")}
            viewer._bag_start_ns = 0
            viewer._lane_overlay_data = _lane_overlay_data()
            viewer.lane_overlay.set_topics(topics)
            viewer.lane_overlay.set_data(viewer._lane_overlay_data)
            pane = viewer._panes[0]
            pane.set_topic(topics["center"].name, topics["center"])

            viewer.lane_overlay.swap_axes_button.click()

            self.assertTrue(viewer._lane_swap_xy)
            self.assertTrue(viewer.lane_overlay.plot.swap_xy)
            self.assertTrue(pane.xy_button.isChecked())
            self.assertTrue(pane.lane_plot.swap_xy)
            self.assertFalse(pane.view_help_button.isHidden())
            self.assertIn("Middle-drag", pane.view_help_button.toolTip())
        finally:
            viewer.window.close()

    def test_point_cloud_view_exposes_operation_help_indicator(self) -> None:
        viewer = TimelineViewer()
        try:
            pane = viewer._panes[0]
            pane.set_topic(
                "/points",
                TopicInfo(
                    name="/points",
                    msgtype="sensor_msgs/msg/PointCloud2",
                    category="point_cloud",
                ),
            )

            self.assertEqual(pane.view_help_button.objectName(), "viewHelpIndicator")
            self.assertFalse(pane.view_help_button.isHidden())
            self.assertIn("Wheel", pane.view_help_button.toolTip())
        finally:
            viewer.window.close()

    def test_lane_topic_render_uses_lane_plot_without_image_warning(self) -> None:
        viewer = TimelineViewer()
        warnings: list[str] = []
        try:
            topics = {role: _lane_topic(role) for role in ("center", "left", "right")}
            viewer._bag_start_ns = 0
            viewer._lane_overlay_data = _lane_overlay_data()
            viewer.lane_overlay.set_topics(topics)
            viewer.lane_overlay.set_data(viewer._lane_overlay_data)
            viewer._show_warning = lambda message: warnings.append(message)  # type: ignore[method-assign]
            pane = viewer._panes[0]
            pane.set_topic(topics["center"].name, topics["center"])

            self.assertTrue(pane.ensure_rendered_for_playback())

            self.assertEqual(warnings, [])
            self.assertIs(pane.stack.currentWidget(), pane.lane_plot)
            self.assertEqual(pane.lane_plot._visible_roles, {"center", "left", "right"})
        finally:
            viewer.window.close()

    def test_lane_view_uses_checked_topic_tree_roles(self) -> None:
        viewer = TimelineViewer()
        try:
            topics = [_lane_topic(role) for role in ("center", "left", "right")]
            viewer.session.reader = DummyReader()  # type: ignore[assignment]
            viewer.session.topics = topics
            viewer._bag_start_ns = 0
            viewer._lane_overlay_data = _lane_overlay_data()
            viewer._populate_topics()
            _tree_item(viewer.topic_tree, "center").setCheckState(
                0,
                QtCore.Qt.CheckState.Checked,
            )
            _tree_item(viewer.topic_tree, "right").setCheckState(
                0,
                QtCore.Qt.CheckState.Checked,
            )
            pane = viewer._panes[0]
            pane.set_topic(topics[0].name, topics[0])
            pane.show_at_timestamp(100)

            self.assertIs(pane.stack.currentWidget(), pane.lane_plot)
            self.assertEqual(pane.lane_plot._visible_roles, {"center", "right"})
        finally:
            viewer.window.close()

    def test_multi_lane_drop_checks_roles_and_uses_one_view(self) -> None:
        viewer = TimelineViewer()
        try:
            topics = [_lane_topic(role) for role in ("center", "left", "right")]
            viewer.session.reader = DummyReader()  # type: ignore[assignment]
            viewer.session.topics = topics
            viewer._bag_start_ns = 0
            viewer._lane_overlay_data = _lane_overlay_data()
            viewer._populate_topics()

            viewer.assign_topics_to_pane(viewer._panes[0], [topic.name for topic in topics])

            self.assertEqual(len(viewer._panes), 1)
            self.assertTrue(viewer._panes[0].is_lane_topic())
            self.assertEqual(viewer._checked_lane_roles(), ["center", "left", "right"])
        finally:
            viewer.window.close()

    def test_lane_overlay_missing_topics_stays_empty(self) -> None:
        viewer = TimelineViewer()
        calls = []
        try:
            viewer._run_background = lambda **kwargs: calls.append(kwargs)  # type: ignore[method-assign]
            viewer._prepare_lane_overlay([
                TopicInfo(
                    name="/camera",
                    msgtype="sensor_msgs/msg/Image",
                    category="image",
                )
            ])

            self.assertEqual(calls, [])
            self.assertEqual(viewer.lane_overlay.visible_roles(), [])
            self.assertIn("No lane line topics", viewer.lane_overlay.status_label.text())
        finally:
            viewer.window.close()

    def test_image_render_cache_is_bounded(self) -> None:
        viewer = TimelineViewer()
        try:
            topic = "/camera"
            topic_info = TopicInfo(
                name=topic,
                msgtype="sensor_msgs/msg/Image",
                category="image",
                message_count=MAX_RENDERED_PLAYBACK_FRAMES + 25,
            )
            viewer.session.reader = ImageReader(MAX_RENDERED_PLAYBACK_FRAMES + 25)  # type: ignore[assignment]
            viewer.session.topics = [topic_info]
            viewer._bag_start_ns = 0
            pane = viewer._panes[0]
            pane.set_topic(topic, topic_info)
            frame = SimpleNamespace(
                array=np.zeros((4, 4, 3), dtype=np.uint8),
                width=4,
                height=4,
                encoding="bgr8",
                warnings=[],
            )

            with patch("rosbagel.gui.timeline_viewer._decode_record_frame", return_value=frame):
                self.assertTrue(pane.ensure_rendered_for_playback())
                _wait_for(lambda: not pane._rendering_playback)

            self.assertGreater(len(pane.rendered_frames), 0)
            self.assertLessEqual(len(pane.rendered_frames), MAX_RENDERED_PLAYBACK_FRAMES)
            from rosbagel.gui.playback import MAX_RENDERED_PLAYBACK_BYTES
            byte_count = sum(frame.image.sizeInBytes() for frame in pane.rendered_frames)
            self.assertLessEqual(byte_count, MAX_RENDERED_PLAYBACK_BYTES + pane.rendered_frames[-1].image.sizeInBytes())
        finally:
            viewer.window.close()

    def test_image_render_does_not_restart_before_topic_first_frame(self) -> None:
        viewer = TimelineViewer()
        try:
            topic = "/camera"
            topic_info = TopicInfo(
                name=topic,
                msgtype="sensor_msgs/msg/Image",
                category="image",
                message_count=MAX_RENDERED_PLAYBACK_FRAMES + 25,
            )
            reader = ImageReader(
                MAX_RENDERED_PLAYBACK_FRAMES + 25,
                first_timestamp_ns=100,
            )
            viewer.session.reader = reader  # type: ignore[assignment]
            viewer.session.topics = [topic_info]
            viewer._bag_start_ns = 0
            pane = viewer._panes[0]
            pane.set_topic(topic, topic_info)
            frame = SimpleNamespace(
                array=np.zeros((4, 4, 3), dtype=np.uint8),
                width=4,
                height=4,
                encoding="bgr8",
                warnings=[],
            )

            with patch("rosbagel.gui.timeline_viewer._decode_record_frame", return_value=frame):
                self.assertTrue(pane.ensure_rendered_for_playback())
                _wait_for(lambda: not pane._rendering_playback)
                pane.show_at_timestamp(0)

            self.assertEqual(reader.iteration_count, 1)
            self.assertEqual(pane.rendered_timestamps[0], 100)
            self.assertFalse(pane.image_label.pixmap().isNull())
            self.assertEqual(viewer.progress_bar.text(), "Ready")
        finally:
            viewer.window.close()

def _wait_for(condition, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        QtTest.QTest.qWait(10)
        time.sleep(0.005)
    if not condition():
        raise AssertionError("Qt operation did not finish before timeout")


def _lane_topic(role: str) -> TopicInfo:
    return TopicInfo(
        name=f"/aiformula_perception/lane_line_publisher/lane_lines/{role}",
        msgtype="sensor_msgs/msg/PointCloud2",
        category="point_cloud",
        message_count=1,
    )


def _lane_overlay_data() -> LaneOverlayData:
    return LaneOverlayData(
        series_by_role={
            role: LaneSeries(
                role=role,
                topic=_lane_topic(role).name,
                frames=[
                    LaneFrame(
                        timestamp_ns=100,
                        points=(LanePoint(1.0, 2.0), LanePoint(3.0, 4.0)),
                    )
                ],
            )
            for role in ("center", "left", "right")
        }
    )


def _lane_position_data() -> LaneOverlayData:
    role_y = {"left": 2.0, "center": 0.0, "right": -2.0}
    return LaneOverlayData(
        series_by_role={
            role: LaneSeries(
                role=role,
                topic=_lane_topic(role).name,
                frames=[
                    LaneFrame(
                        timestamp_ns=100,
                        points=(
                            LanePoint(4.0, y),
                            LanePoint(8.0, y),
                        ),
                    )
                ],
            )
            for role, y in role_y.items()
        }
    )


def _tree_item(tree: QtWidgets.QTreeWidget, text: str) -> QtWidgets.QTreeWidgetItem:
    for index in range(tree.topLevelItemCount()):
        found = _tree_item_from(tree.topLevelItem(index), text)
        if found is not None:
            return found
    raise AssertionError(f"Tree item not found: {text}")


def _tree_item_from(
    item: QtWidgets.QTreeWidgetItem,
    text: str,
) -> QtWidgets.QTreeWidgetItem | None:
    if item.text(0) == text:
        return item
    for index in range(item.childCount()):
        found = _tree_item_from(item.child(index), text)
        if found is not None:
            return found
    return None


if __name__ == "__main__":
    unittest.main()
