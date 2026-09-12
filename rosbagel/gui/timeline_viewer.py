from __future__ import annotations

from bisect import bisect_left
from pathlib import Path
from dataclasses import replace
import json
from html import escape
from queue import Empty, Queue
from threading import Thread
from typing import Any

from rosbagel.core.decoder import decode_compressed_image, decode_sensor_image
from rosbagel.core.jobs import CancellationToken
from rosbagel.core.lane_lines import (
    LANE_ROLES,
    LaneOverlayData,
    build_lane_overlay_data,
    lane_role_for_topic,
    lane_topics,
)
from rosbagel.core.models import TopicInfo
from rosbagel.core.preview import (
    PreviewService,
    PreviewSessionSettings,
    TopicDisplaySettings,
)
from rosbagel.core.session import Session
from rosbagel.core.update_check import UpdateInfo, check_for_update, current_version
from rosbagel.cli.upgrade import build_upgrade_plan, run_upgrade
from rosbagel.gui.playback import MAX_RENDERED_PLAYBACK_FRAMES, RenderedFrame, load_playback_window
from rosbagel.gui.lane_overlay import LANE_PLOT_HELP_TEXT, create_lane_overlay_panel_class
from rosbagel.gui.renderers import POINT_CLOUD_HELP_TEXT, create_point_cloud_renderer
from rosbagel.gui.tasks import GuiTaskRunner
from rosbagel.gui.view_layout import ViewLayout
from rosbagel.gui.theme import local_changelog_text as _local_changelog_text
from rosbagel.gui.theme import normalize_theme as _normalize_theme
from rosbagel.gui.theme import theme_palette as _theme_palette
from rosbagel.gui.theme import theme_stylesheet as _theme_stylesheet
from rosbagel.gui.theme import create_gui_style, tinted_standard_icon


TOPIC_MIME = "application/x-rosbagel-topic"
TOPICS_MIME = "application/x-rosbagel-topics"
VIEW_MIME = "application/x-rosbagel-view"
IMAGE_CATEGORIES = {"image", "compressed_image", "mask_candidate"}
COMPACT_TOPIC_WIDTH = 240


class TimelineViewer:
    """Offline bag viewer shell for Windows-first ROSBagel workflows."""

    def __init__(self, bag_path: str | Path | None = None) -> None:
        self.QtCore, self.QtGui, self.QtWidgets = _require_pyside6()
        self.TopicTreeWidget = _create_topic_tree_class(self.QtWidgets, self.QtCore)
        self.TopicViewPane = _create_view_pane_class(
            self.QtWidgets, self.QtCore, self.QtGui
        )
        self.LaneOverlayPanel = create_lane_overlay_panel_class(
            self.QtWidgets,
            self.QtCore,
            self.QtGui,
        )
        self.session = Session()
        self.preview: PreviewService | None = None
        self.settings = PreviewSessionSettings()
        self._topic_by_item: dict[int, str] = {}
        self._topic_info_by_name: dict[str, TopicInfo] = {}
        self._bag_start_ns: int | None = None
        self._bag_end_ns: int | None = None
        self._playback_fps = 30.0
        self._panes: list[Any] = []
        self._window_panes: list[Any] = []
        self._active_pane: Any | None = None
        self._maximized_pane: Any | None = None
        self._dock_widgets: dict[str, Any] = {}
        self._windows_menu: Any | None = None
        self._theme_menu: Any | None = None
        self._theme_actions: dict[str, Any] = {}
        self._next_view_id = 1
        self._playback_rate = 1.0
        self._update_settings = self.QtCore.QSettings("TsubashimoNanato", "rosbagel")
        self._latest_update_info: UpdateInfo | None = None
        stored_theme = str(self._update_settings.value("ui/theme", "") or "")
        self._theme = _normalize_theme(stored_theme) if stored_theme else "dark"
        self._dock_resize_generations = {"horizontal": 0, "vertical": 0}
        self._autosize_pending = False
        self._background_jobs: list[tuple[Any, Any, Any, Any]] = []
        self._lane_topics_by_role: dict[str, TopicInfo] = {}
        self._lane_overlay_data: LaneOverlayData | None = None
        self._lane_load_generation = 0
        self._lane_load_token: CancellationToken | None = None
        self._lane_swap_xy = False
        self._selected_lane_roles: list[str] | None = None
        self._closed = False
        self._opening = False
        self._exporting = False
        self._properties_topic: str | None = None
        self._expanded_before_search: set[int] | None = None
        self._owned_sessions: list[Session] = []
        self.workflows: Any = None

        self.window = _create_drop_window(self.QtWidgets, self.QtCore, self.open_bag, self._close)
        self._io_tasks = GuiTaskRunner(self.QtCore, self.window)
        self.window.setWindowTitle("ROSBagel Timeline Viewer")
        self.window.setAttribute(self.QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        self.window.resize(1280, 760)
        self._build_ui()
        from rosbagel.gui.workflows import GuiWorkflows
        self.workflows = GuiWorkflows(self)
        if bag_path is not None:
            self.open_bag(bag_path)
        self.startup_timer = self.QtCore.QTimer(self.window)
        self.startup_timer.setSingleShot(True)
        self.startup_timer.timeout.connect(self._maybe_offer_startup_update_check)
        self.startup_timer.start(500)

    def show(self) -> None:
        self.window.show()
        self._queue_autosize_docks()

    def open_bag(self, bag_path: str | Path, *, backend: str | None = None) -> None:
        if self._opening or self._exporting or self._closed:
            return
        path = Path(bag_path)
        self._log(f"Opening {path}")
        self._pause_playback()
        self.preview_timer.stop()
        for pane in self._all_panes():
            pane.cancel_pending()
        self.workflows.clear_tasks()
        self._opening = True
        self._set_bag_controls_enabled(False)
        self._start_progress(f"Opening {path.name}", None)
        backend = backend or self.session.backend

        def work(context: Any) -> tuple[Session, tuple[int | None, int | None]]:
            session = Session(backend=backend)
            try:
                session.open_bag(path)
                bounds = session.reader.get_time_bounds()
                context.throw_if_cancelled()
            except BaseException:
                session.close()
                raise
            self._owned_sessions.append(session)
            return session, bounds

        def finish(result: tuple[Session, tuple[int | None, int | None]]) -> None:
            session, bounds = result
            previous = self.session
            self.session = session
            self.preview = PreviewService(session)
            self._clear_lane_overlay()
            topics = session.topics
            self._topic_info_by_name = {topic.name: topic for topic in topics}
            self.settings.bag_path = str(path)
            self.settings.topics = {
                topic.name: TopicDisplaySettings(topic=topic.name)
                for topic in topics
            }
            cloud_colors = ("#ff9f1c", "#398be5", "#e75b68", "#28aa92", "#ba83da")
            clouds = [topic for topic in topics if topic.category == "point_cloud" and lane_role_for_topic(topic) is None]
            for index, topic in enumerate(clouds):
                self.settings.topics[topic.name].color = cloud_colors[index % len(cloud_colors)]
            for pane in self._all_panes():
                pane.clear_topic()
            self._populate_topics()
            self._bag_start_ns, self._bag_end_ns = bounds
            duration = max(0.0, (bounds[1] - bounds[0]) / 1e9) if None not in bounds else 0.0
            self.time_input.setRange(0.0, duration)
            self._set_timeline_seconds(0.0, immediate=False)
            self._opening = False
            self._set_bag_controls_enabled(True)
            self._apply_topic_settings(None)
            self._prepare_lane_overlay(topics)
            self._autosize_topic_columns()
            self._queue_autosize_docks()
            self._log(f"Opened {path} ({len(topics)} topics)")
            self._finish_progress()
            self.window.setWindowTitle(f"{path.name} - ROSBagel Timeline Viewer")
            self.workflows.on_bag()
            self._io_tasks.submit("close-previous", lambda _ctx: previous.close(), lambda _: None, self._log)

        def failed(message: str) -> None:
            self._opening = False
            self.workflows.pending_workspace = None
            self._set_bag_controls_enabled(self.session.reader is not None)
            self._finish_progress("Open failed")
            self.workflows.on_time(self._current_timestamp_ns())
            self._show_warning(f"Could not open {path}: {message}")

        self._io_tasks.submit("open", work, finish, failed)

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.startup_timer.stop()
        self.preview_timer.stop()
        self.play_timer.stop()
        self._lane_load_generation += 1
        if self._lane_load_token is not None:
            self._lane_load_token.cancel()
        for pane in self._all_panes():
            pane.cancel_pending()
        for pane in list(self._window_panes):
            pane.window().close()
        for _thread, timer, progress, _results in self._background_jobs:
            timer.stop()
            progress.close()
        self._background_jobs.clear()

        def cleanup() -> None:
            for session in self._owned_sessions:
                session.close()
            self._owned_sessions.clear()

        self._io_tasks.close(cleanup)

    def _set_bag_controls_enabled(self, enabled: bool) -> None:
        if self.workflows is not None:
            self.workflows.set_enabled(enabled)
        for widget in (self.play_button, self.step_button, self.time_slider, self.time_input, self.rate_box):
            widget.setEnabled(enabled and self.time_input.maximum() > 0)
        self.topic_panel.setEnabled(enabled)
        self.save_settings_button.setEnabled(enabled)
        self.export_action.setEnabled(enabled)
        self.import_action.setEnabled(not self._opening)
        self.import_file_action.setEnabled(not self._opening)
        self.main_panel.setEnabled(not self._opening)
        self.properties_panel.setEnabled(enabled)

    def assign_topic_to_pane(self, pane: Any, topic: str) -> None:
        topic_info = self._topic_info_by_name.get(topic)
        if topic_info is None:
            self._log(f"Topic not found: {topic}")
            return
        pane.set_topic(topic, topic_info)
        self._set_active_pane(pane)
        self._apply_topic_settings(topic)
        self._request_preview_update()

    def assign_topics_to_pane(self, pane: Any, topics: list[str]) -> None:
        valid_topics = self._valid_drop_topics(topics)
        if not valid_topics:
            return
        groups = self._drop_topic_groups(valid_topics)
        target_pane = pane
        for index, group in enumerate(groups):
            if index > 0:
                next_pane = self.split_pane(target_pane, "horizontal")
                if next_pane is None:
                    return
                target_pane = next_pane
            self._assign_topic_group_to_pane(target_pane, group)

    def _valid_drop_topics(self, topics: list[str]) -> list[str]:
        valid_topics: list[str] = []
        seen: set[str] = set()
        for topic in topics:
            if topic in seen:
                continue
            seen.add(topic)
            if topic not in self._topic_info_by_name:
                self._log(f"Topic not found: {topic}")
                continue
            valid_topics.append(topic)
        return valid_topics

    def _drop_topic_groups(self, topics: list[str]) -> list[list[str]]:
        groups: list[list[str]] = []
        lane_group: list[str] | None = None
        point_cloud_group: list[str] | None = None
        for topic in topics:
            info = self._topic_info_by_name[topic]
            if lane_role_for_topic(info) is not None:
                if lane_group is None:
                    lane_group = []
                    groups.append(lane_group)
                lane_group.append(topic)
                continue
            if info.category == "point_cloud":
                if point_cloud_group is None:
                    point_cloud_group = []
                    groups.append(point_cloud_group)
                point_cloud_group.append(topic)
                continue
            groups.append([topic])
        return groups

    def _assign_topic_group_to_pane(self, pane: Any, topics: list[str]) -> None:
        if not topics:
            return
        if self._all_lane_topics(topics):
            self._set_checked_topics(topics)
            topic = self._preferred_lane_anchor(topics)
            self.assign_topic_to_pane(pane, topic)
            self._refresh_lane_view_panes()
            return
        self.assign_topic_to_pane(pane, topics[0])
        if len(topics) > 1 and self._topic_info_by_name[topics[0]].category == "point_cloud":
            pane.point_topics = list(topics)
            pane._refresh_title()
            self._request_preview_update()

    def _all_lane_topics(self, topics: list[str]) -> bool:
        return all(
            lane_role_for_topic(self._topic_info_by_name[topic]) is not None
            for topic in topics
        )

    def _preferred_lane_anchor(self, topics: list[str]) -> str:
        for role in LANE_ROLES:
            for topic in topics:
                info = self._topic_info_by_name[topic]
                if lane_role_for_topic(info) == role:
                    return topic
        return topics[0]

    def split_pane(self, pane: Any, direction: str) -> Any | None:
        if len(self._all_panes()) >= 16:
            self._show_warning("A maximum of 16 views can be open at once.")
            return None
        if pane not in self._panes:
            pane = self._panes[-1]
        self._maximized_pane = None
        new_pane = self._new_pane()
        insert_at = self._panes.index(pane) + 1
        self._panes.insert(insert_at, new_pane)
        self.view_layout.split(pane, new_pane, direction)
        self._layout_panes()
        self._set_active_pane(new_pane)
        return new_pane

    def toggle_maximize_pane(self, pane: Any) -> None:
        self._set_active_pane(pane)
        if pane in self._window_panes:
            window = pane.window()
            if window.isMaximized():
                window.showNormal()
            else:
                window.showMaximized()
            return
        if self._maximized_pane is pane:
            self._maximized_pane = None
            for item in self._panes:
                item.setVisible(True)
            self._update_topic_minimum_width()
            self._queue_autosize_docks()
            return
        self._maximized_pane = pane
        for item in self._panes:
            item.setVisible(item is pane)
        self._update_topic_minimum_width()
        self._queue_autosize_docks()

    def delete_pane(self, pane: Any) -> None:
        if pane in self._window_panes:
            pane.window().close()
            return
        if len(self._panes) <= 1:
            pane.clear_topic()
            self._apply_topic_settings(None)
            return
        if pane in self._panes:
            pane.cancel_pending()
            self._panes.remove(pane)
            self.view_layout.remove(pane)
            pane.deleteLater()
            if self._active_pane is pane:
                self._set_active_pane(self._panes[0] if self._panes else None)
            if self._maximized_pane is pane:
                self._maximized_pane = None
            self._layout_panes()

    def duplicate_pane_window(self, pane: Any) -> None:
        if len(self._all_panes()) >= 16:
            self._show_warning("A maximum of 16 views can be open at once.")
            return
        duplicate = self._new_pane()
        if pane.topic is not None and pane.topic_info is not None:
            duplicate.set_topic(pane.topic, pane.topic_info)
            duplicate.point_topics = list(pane.point_topics)
            duplicate._refresh_title()
        self._show_pane_window(duplicate)

    def detach_pane(self, pane: Any) -> None:
        if pane in self._window_panes:
            pane.window().raise_()
            return
        if pane not in self._panes:
            return
        self._maximized_pane = None
        self._panes.remove(pane)
        self.view_layout.remove(pane)
        if not self._panes:
            replacement = self._new_pane()
            self._panes.append(replacement)
            self.view_layout.add(replacement)
        self._layout_panes()
        self._show_pane_window(pane)

    def _show_pane_window(self, pane: Any) -> None:
        dialog = self.QtWidgets.QDialog(self.window)
        dialog.setWindowTitle(pane.topic or pane.view_title)
        dialog.resize(760, 520)
        layout = self.QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(pane)
        self._window_panes.append(pane)

        def cleanup(_result: int = 0) -> None:
            if pane in self._window_panes:
                pane.cancel_pending()
                self._window_panes.remove(pane)
            if self._active_pane is pane and pane not in self._panes:
                self._set_active_pane(self._panes[0] if self._panes else None)
            dialog.deleteLater()

        dialog.finished.connect(cleanup)
        dialog.show()
        self._set_active_pane(pane)
        pane.show_at_timestamp(self._current_timestamp_ns())

    def move_pane(self, pane: Any, target: Any, edge: str) -> None:
        if pane is target or target not in self._panes:
            return
        if pane in self._window_panes:
            dialog = pane.window()
            self._window_panes.remove(pane)
            pane.setParent(None)
            dialog.close()
        elif pane in self._panes:
            self._panes.remove(pane)
            self.view_layout.remove(pane)
        else:
            return
        self._maximized_pane = None
        self._panes.insert(self._panes.index(target) + 1, pane)
        self.view_layout.split(target, pane, "vertical" if edge in {"top", "bottom"} else "horizontal",
                               before=edge in {"left", "top"})
        self._layout_panes()
        self._set_active_pane(pane)

    def _pane_from_mime(self, mime: Any) -> Any | None:
        if not mime.hasFormat(VIEW_MIME):
            return None
        payload = bytes(mime.data(VIEW_MIME))
        return next((pane for pane in self._all_panes() if str(id(pane)).encode("ascii") == payload), None)

    def _build_ui(self) -> None:
        QtWidgets = self.QtWidgets
        QtCore = self.QtCore

        self._build_menus()
        self.window.setDockOptions(
            QtWidgets.QMainWindow.DockOption.AllowNestedDocks
            | QtWidgets.QMainWindow.DockOption.AllowTabbedDocks
            | QtWidgets.QMainWindow.DockOption.AnimatedDocks
        )

        self.central_spacer = QtWidgets.QWidget()
        self.central_spacer.setObjectName("centralSpacer")
        self.central_spacer.setMinimumSize(0, 0)
        self.central_spacer.setMaximumWidth(0)
        self.central_spacer.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed,
            QtWidgets.QSizePolicy.Policy.Ignored,
        )
        self.window.setCentralWidget(self.central_spacer)

        self.topic_tree = self.TopicTreeWidget()
        self.topic_tree.setHeaderLabels(["Topic", "Category", "Count"])
        self.topic_tree.setDragEnabled(True)
        self.topic_tree.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.DragOnly)
        self.topic_tree.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.topic_tree.itemSelectionChanged.connect(self._on_topic_selection_changed)
        self.topic_tree.itemDoubleClicked.connect(self._on_topic_double_clicked)
        self.topic_tree.itemChanged.connect(self._on_topic_item_changed)
        self.topic_tree.setMinimumWidth(COMPACT_TOPIC_WIDTH)
        self.topic_tree.setRootIsDecorated(True)
        self.topic_tree.setItemsExpandable(True)
        self.topic_tree.setExpandsOnDoubleClick(True)
        self.topic_tree.setIndentation(18)
        self.topic_tree.setUniformRowHeights(True)

        self.topic_panel = QtWidgets.QWidget()
        self.topic_panel.setObjectName("topicPanel")
        topic_layout = QtWidgets.QVBoxLayout(self.topic_panel)
        topic_layout.setContentsMargins(8, 8, 8, 8)
        topic_layout.setSpacing(6)
        topic_toolbar = QtWidgets.QHBoxLayout()
        topic_toolbar.setContentsMargins(0, 0, 0, 0)
        topic_toolbar.setSpacing(6)
        self.topic_search = QtWidgets.QLineEdit()
        self.topic_search.setPlaceholderText("Search topics")
        self.topic_search.setClearButtonEnabled(True)
        self.topic_search.setMinimumWidth(96)
        self.topic_search.textChanged.connect(self._filter_topic_tree)
        toolbar_icon_size = QtCore.QSize(16, 16)
        toolbar_button_size = QtCore.QSize(30, 26)
        window_style = self.window.style()
        self.topic_expand_button = QtWidgets.QToolButton()
        self.topic_expand_button.setIcon(
            window_style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_ArrowDown)
        )
        self.topic_expand_button.setIconSize(toolbar_icon_size)
        self.topic_expand_button.setFixedSize(toolbar_button_size)
        self.topic_expand_button.setAccessibleName("Expand all")
        self.topic_expand_button.setToolTip("Expand all topic groups")
        self.topic_expand_button.clicked.connect(self._expand_topic_tree)
        self.topic_collapse_button = QtWidgets.QToolButton()
        self.topic_collapse_button.setIcon(
            window_style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_ArrowUp)
        )
        self.topic_collapse_button.setIconSize(toolbar_icon_size)
        self.topic_collapse_button.setFixedSize(toolbar_button_size)
        self.topic_collapse_button.setAccessibleName("Collapse all")
        self.topic_collapse_button.setToolTip("Collapse all topic groups")
        self.topic_collapse_button.clicked.connect(self._collapse_topic_tree)
        self.topic_uncheck_button = QtWidgets.QToolButton()
        self.topic_uncheck_button.setIcon(
            window_style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DialogResetButton)
        )
        self.topic_uncheck_button.setIconSize(toolbar_icon_size)
        self.topic_uncheck_button.setFixedSize(toolbar_button_size)
        self.topic_uncheck_button.setAccessibleName("Uncheck all")
        self.topic_uncheck_button.setToolTip("Clear all checked topics")
        self.topic_uncheck_button.clicked.connect(self._uncheck_topic_tree)
        topic_toolbar.addWidget(self.topic_search, 1)
        topic_toolbar.addWidget(self.topic_expand_button)
        topic_toolbar.addWidget(self.topic_collapse_button)
        topic_toolbar.addWidget(self.topic_uncheck_button)
        topic_layout.addLayout(topic_toolbar)
        topic_layout.addWidget(self.topic_tree, 1)
        self.topic_status = QtWidgets.QLabel("No bag open")
        topic_layout.addWidget(self.topic_status)

        self.view_grid_widget = QtWidgets.QWidget()
        self.view_grid_widget.setObjectName("viewGrid")
        self.view_layout = ViewLayout(QtWidgets, QtCore, self.view_grid_widget)
        first_pane = self._new_pane()
        self._panes.append(first_pane)
        self.view_layout.add(first_pane)
        self._active_pane = first_pane
        self._layout_panes()

        settings_panel = QtWidgets.QWidget()
        self.properties_panel = settings_panel
        settings_panel.setObjectName("propertiesPanel")
        settings_layout = QtWidgets.QFormLayout(settings_panel)
        self.properties_form = settings_layout
        settings_layout.setContentsMargins(10, 10, 10, 10)
        settings_layout.setSpacing(8)
        self.visible_check = QtWidgets.QCheckBox()
        self.visible_check.setChecked(True)
        self.color_button = QtWidgets.QPushButton("Color")
        self.point_size = QtWidgets.QDoubleSpinBox()
        self.point_size.setRange(0.1, 20.0)
        self.point_size.setValue(2.0)
        self.decimation = QtWidgets.QSpinBox()
        self.decimation.setRange(1, 1_000)
        self.decimation.setValue(4)
        self.opacity = QtWidgets.QDoubleSpinBox()
        self.opacity.setRange(0.0, 1.0)
        self.opacity.setSingleStep(0.1)
        self.opacity.setValue(1.0)
        self.sync_offset = QtWidgets.QDoubleSpinBox()
        self.sync_offset.setRange(-3600.0, 3600.0)
        self.sync_offset.setDecimals(3)
        self.save_settings_button = QtWidgets.QPushButton("Save workspace...")
        self.save_settings_button.clicked.connect(lambda: self.workflows.save_workspace())
        self.properties_topic_label = QtWidgets.QLabel("No topic selected")
        self.properties_topic_label.setWordWrap(True)
        self.properties_topic_label.setMinimumWidth(0)
        self.properties_topic_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        settings_layout.addRow(self.properties_topic_label)
        settings_layout.addRow("Visible", self.visible_check)
        settings_layout.addRow("Color", self.color_button)
        settings_layout.addRow("Point size", self.point_size)
        settings_layout.addRow("Decimation", self.decimation)
        settings_layout.addRow("Opacity", self.opacity)
        settings_layout.addRow("Sync offset s", self.sync_offset)
        settings_layout.addRow(self.save_settings_button)
        self.visible_check.toggled.connect(self._store_topic_settings)
        self.point_size.valueChanged.connect(self._store_topic_settings)
        self.decimation.valueChanged.connect(self._store_topic_settings)
        self.opacity.valueChanged.connect(self._store_topic_settings)
        self.sync_offset.valueChanged.connect(self._store_topic_settings)
        self.color_button.clicked.connect(self._choose_topic_color)

        settings_scroll = QtWidgets.QScrollArea()
        settings_scroll.setObjectName("propertiesScroll")
        settings_scroll.viewport().setObjectName("propertiesViewport")
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setWidget(settings_panel)

        self.main_panel = QtWidgets.QWidget()
        self.main_panel.setObjectName("mainPanel")
        main_layout = QtWidgets.QVBoxLayout(self.main_panel)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(6)
        main_layout.addWidget(self.view_grid_widget, 1)

        timeline_group = QtWidgets.QGroupBox("Timeline")
        timeline = QtWidgets.QHBoxLayout(timeline_group)
        timeline.setContentsMargins(8, 8, 8, 8)
        self.play_button = QtWidgets.QPushButton("Play")
        self.step_button = QtWidgets.QPushButton("Step")
        self.time_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.time_slider.setRange(0, 1000)
        self.time_slider.setTracking(True)
        self.time_slider.setAccessibleName("Timeline position")
        self.time_slider.sliderPressed.connect(self._pause_playback)
        self.time_slider.valueChanged.connect(self._on_slider_changed)
        self.time_input = QtWidgets.QDoubleSpinBox()
        self.time_input.setRange(0.0, 0.0)
        self.time_input.setDecimals(3)
        self.time_input.setKeyboardTracking(False)
        self.time_input.setAccessibleName("Time in seconds")
        self.time_input.valueChanged.connect(self._on_time_input_changed)
        self.time_input.setMaximumWidth(120)
        self.rate_box = QtWidgets.QComboBox()
        self.rate_box.addItems(["0.25x", "0.5x", "1x", "2x", "4x"])
        self.rate_box.setCurrentText("1x")
        self.rate_box.setMaximumWidth(90)
        self.rate_box.currentTextChanged.connect(self._on_playback_rate_changed)
        self.theme_toggle = QtWidgets.QCheckBox("Dark mode")
        self.theme_toggle.setObjectName("themeToggle")
        self.theme_toggle.setChecked(self._theme == "dark")
        self.theme_toggle.setToolTip("Switch the GUI between dark and light mode")
        self.theme_toggle.toggled.connect(
            lambda checked: self._set_theme("dark" if checked else "light")
        )
        timeline.addWidget(self.play_button)
        timeline.addWidget(self.step_button)
        timeline.addWidget(self.time_slider, 1)
        timeline.addWidget(QtWidgets.QLabel("Time s"))
        timeline.addWidget(self.time_input)
        timeline.addWidget(QtWidgets.QLabel("Rate"))
        timeline.addWidget(self.rate_box)
        timeline.addWidget(self.theme_toggle)
        self.timeline_toolbar = QtWidgets.QToolBar("Timeline", self.window)
        self.timeline_toolbar.setObjectName("timelineToolbar")
        self.timeline_toolbar.setMovable(False)
        self.timeline_toolbar.addWidget(timeline_group)
        timeline_group.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)
        self.window.addToolBar(QtCore.Qt.ToolBarArea.BottomToolBarArea, self.timeline_toolbar)
        self._windows_menu.addAction(self.timeline_toolbar.toggleViewAction())

        output_panel = QtWidgets.QWidget()
        output_panel.setObjectName("outputPanel")
        output_layout = QtWidgets.QVBoxLayout(output_panel)
        output_layout.setContentsMargins(8, 8, 8, 8)
        output_layout.setSpacing(6)
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("Ready")
        output_layout.addWidget(self.progress_bar)
        self.log_text = QtWidgets.QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.document().setMaximumBlockCount(500)
        self.log_text.setMinimumHeight(60)
        output_layout.addWidget(self.log_text, 1)

        self.lane_overlay = self.LaneOverlayPanel(
            self.window,
            on_selection_changed=self._on_lane_overlay_selection_changed,
            on_axes_changed=self._set_lane_swap_xy,
        )

        self.preview_timer = QtCore.QTimer()
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(50)
        self.preview_timer.timeout.connect(self._update_preview)
        self.play_timer = QtCore.QTimer()
        self.play_clock = QtCore.QElapsedTimer()
        self.play_timer.setInterval(int(1000 / self._playback_fps))
        self.play_timer.timeout.connect(self._advance_playback)
        self.play_button.clicked.connect(self._toggle_playback)
        self.step_button.clicked.connect(self._step_forward)

        self.topic_dock = self._make_dock(
            "Topic list",
            self.topic_panel,
            QtCore.Qt.DockWidgetArea.LeftDockWidgetArea,
        )
        self.main_view_dock = self._make_dock(
            "Main view",
            self.main_panel,
            QtCore.Qt.DockWidgetArea.RightDockWidgetArea,
        )
        self._install_main_view_titlebar()
        self.lane_overlay_dock = self._make_dock(
            "Lane line overlay",
            self.lane_overlay,
            QtCore.Qt.DockWidgetArea.RightDockWidgetArea,
        )
        self.properties_dock = self._make_dock(
            "Properties",
            settings_scroll,
            QtCore.Qt.DockWidgetArea.RightDockWidgetArea,
        )
        self.output_dock = self._make_dock(
            "Output",
            output_panel,
            QtCore.Qt.DockWidgetArea.BottomDockWidgetArea,
        )
        self.topic_dock.setMinimumWidth(COMPACT_TOPIC_WIDTH)
        self.main_view_dock.setMinimumWidth(380)
        self.lane_overlay_dock.setMinimumWidth(300)
        self.lane_overlay_dock.setMinimumHeight(220)
        self.properties_dock.setMinimumWidth(220)
        self.window.splitDockWidget(
            self.main_view_dock,
            self.lane_overlay_dock,
            QtCore.Qt.Orientation.Vertical,
        )
        self.window.splitDockWidget(
            self.main_view_dock,
            self.properties_dock,
            QtCore.Qt.Orientation.Horizontal,
        )
        self._apply_theme()
        self._autosize_docks()
        self._set_bag_controls_enabled(False)
        self._apply_topic_settings(None)
        self.lane_overlay_dock.hide()

    def _build_menus(self) -> None:
        menu_bar = self.window.menuBar()
        menu = menu_bar.addMenu("File")
        self._file_menu = menu
        import_action = self.QtGui.QAction("Import bag...", self.window)
        self.import_action = import_action
        import_action.setShortcut(self.QtGui.QKeySequence.StandardKey.Open)
        self.import_file_action = self.QtGui.QAction("Open bag file...", self.window)
        self.import_file_action.triggered.connect(self._import_bag_file)
        export_action = self.QtGui.QAction("Export...", self.window)
        self.export_action = export_action
        version_action = self.QtGui.QAction("Version...", self.window)
        self._file_actions = [import_action, export_action, version_action]
        import_action.triggered.connect(self._import_bag)
        export_action.triggered.connect(self._show_export_dialog)
        version_action.triggered.connect(self._on_version_action_triggered)
        menu.addAction(import_action)
        menu.addAction(self.import_file_action)
        menu.addAction(export_action)
        menu.addSeparator()
        menu.addAction(version_action)
        self._build_theme_menu(menu_bar)
        self._windows_menu = menu_bar.addMenu("Windows")
        reset_action = self._windows_menu.addAction("Reset layout")
        reset_action.triggered.connect(self._reset_dock_layout)
        self._windows_menu.addSeparator()

    def _reset_dock_layout(self) -> None:
        QtCore = self.QtCore
        self.timeline_toolbar.show()
        self._maximized_pane = None
        for dock in self._dock_widgets.values():
            dock.setFloating(False)
            dock.show()
        self.window.addDockWidget(QtCore.Qt.DockWidgetArea.LeftDockWidgetArea, self.topic_dock)
        self.window.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, self.main_view_dock)
        self.window.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, self.output_dock)
        self.window.splitDockWidget(self.main_view_dock, self.lane_overlay_dock, QtCore.Qt.Orientation.Vertical)
        self.window.splitDockWidget(self.main_view_dock, self.properties_dock, QtCore.Qt.Orientation.Horizontal)
        self.lane_overlay_dock.hide()
        if self.workflows is not None:
            for dock in self.workflows.docks:
                dock.hide()
        self._layout_panes()

    def _build_theme_menu(self, menu_bar: Any) -> None:
        theme_menu = menu_bar.addMenu("Theme")
        self._theme_menu = theme_menu
        theme_group = self.QtGui.QActionGroup(self.window)
        theme_group.setExclusive(True)
        bright_action = self.QtGui.QAction("Light mode", self.window)
        dark_action = self.QtGui.QAction("Dark mode", self.window)
        for theme, action in (("light", bright_action), ("dark", dark_action)):
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, value=theme: self._set_theme(value))
            theme_group.addAction(action)
            theme_menu.addAction(action)
            self._theme_actions[theme] = action
        self._sync_theme_actions()

    def _on_version_action_triggered(self, _checked: bool = False) -> None:
        self._show_version_dialog()

    def _make_dock(self, title: str, widget: Any, area: Any) -> Any:
        dock = self.QtWidgets.QDockWidget(title, self.window)
        dock.setObjectName(title.replace(" ", "_").lower())
        dock.setWidget(widget)
        dock.setAllowedAreas(
            self.QtCore.Qt.DockWidgetArea.LeftDockWidgetArea
            | self.QtCore.Qt.DockWidgetArea.RightDockWidgetArea
            | self.QtCore.Qt.DockWidgetArea.TopDockWidgetArea
            | self.QtCore.Qt.DockWidgetArea.BottomDockWidgetArea
        )
        dock.setFeatures(
            self.QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetClosable
            | self.QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetMovable
            | self.QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.window.addDockWidget(area, dock)
        self._dock_widgets[title] = dock
        if self._windows_menu is not None:
            action = dock.toggleViewAction()
            action.setText(title)
            self._windows_menu.addAction(action)
        dock.visibilityChanged.connect(lambda _visible: self._queue_autosize_docks())
        return dock

    def _install_main_view_titlebar(self) -> None:
        QtCore = self.QtCore
        QtWidgets = self.QtWidgets

        class DockTitleBar(QtWidgets.QWidget):
            def mousePressEvent(self, event: Any) -> None:
                event.ignore()

            def mouseMoveEvent(self, event: Any) -> None:
                event.ignore()

            def mouseReleaseEvent(self, event: Any) -> None:
                event.ignore()

            def mouseDoubleClickEvent(self, event: Any) -> None:
                event.ignore()

        title_bar = DockTitleBar(self.main_view_dock)
        title_bar.setObjectName("mainViewTitleBar")
        layout = QtWidgets.QHBoxLayout(title_bar)
        layout.setContentsMargins(6, 2, 4, 2)
        layout.setSpacing(6)
        self.main_view_title = QtWidgets.QLabel("Views", title_bar)
        self.main_view_title.setObjectName("mainViewTitle")
        self.main_view_title.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        layout.addWidget(self.main_view_title, 1)
        self.main_view_dock.setTitleBarWidget(title_bar)

    def _new_pane(self, parent: Any | None = None) -> Any:
        pane = self.TopicViewPane(self, parent)
        pane.set_view_title(f"View {self._next_view_id}")
        self._next_view_id += 1
        pane.apply_theme(_theme_palette(self._theme))
        pane.set_active(False)
        return pane

    def _set_active_pane(self, pane: Any | None) -> None:
        if pane is not None and pane not in self._all_panes():
            return
        self._active_pane = pane
        self._refresh_pane_chrome()
        if hasattr(self, "properties_panel"):
            self._apply_topic_settings(pane.topic if pane is not None else None)

    def _refresh_pane_chrome(self) -> None:
        for pane in self._all_panes():
            pane.set_active(pane is self._active_pane)

    def _layout_panes(self) -> None:
        for pane in self._panes:
            pane.setVisible(self._maximized_pane is None or pane is self._maximized_pane)
        self._update_topic_minimum_width()
        self._refresh_pane_chrome()
        if hasattr(self, "main_view_dock"):
            # QDockWidget keeps the previous content minimum after a split is removed.
            self.main_view_dock.setMinimumSize(380, 0)
        self._queue_autosize_docks()

    def _queue_autosize_docks(self) -> None:
        if self._autosize_pending or self._closed:
            return
        self._autosize_pending = True

        def run() -> None:
            self._autosize_pending = False
            self._autosize_docks()

        self.QtCore.QTimer.singleShot(0, self.window, run)

    def _autosize_docks(self) -> None:
        if self._closed or not self.window.isVisible():
            return
        self._autosize_topic_columns()
        visible_docks = [
            dock for dock in [
                getattr(self, "topic_dock", None),
                getattr(self, "main_view_dock", None),
                getattr(self, "properties_dock", None),
            ]
            if dock is not None and dock.isVisible()
        ]
        if len(visible_docks) >= 2:
            topic_dock = getattr(self, "topic_dock", None)
            main_dock = getattr(self, "main_view_dock", None)
            properties_dock = getattr(self, "properties_dock", None)
            targets = self._dock_width_targets(
                has_topic=topic_dock in visible_docks,
                has_main=main_dock in visible_docks,
                has_properties=properties_dock in visible_docks,
            )
            widths = []
            for dock in visible_docks:
                if dock is main_dock:
                    widths.append(targets["main"])
                elif dock is topic_dock:
                    widths.append(targets["topic"])
                else:
                    widths.append(targets["properties"])
            self._animate_resize_docks(
                visible_docks,
                widths,
                self.QtCore.Qt.Orientation.Horizontal,
            )
        output_dock = getattr(self, "output_dock", None)
        if output_dock is not None and output_dock.isVisible():
            self._animate_resize_docks(
                [output_dock],
                [max(140, int(self.window.height() * 0.20))],
                self.QtCore.Qt.Orientation.Vertical,
            )

    def _dock_width_targets(
        self,
        *,
        has_topic: bool,
        has_main: bool,
        has_properties: bool,
    ) -> dict[str, int]:
        available = max(640, self.window.width() - 28)
        targets = {
            "topic": self._topic_width_target() if has_topic else 0,
            "main": 520 if has_main else 0,
            "properties": 260 if has_properties else 0,
        }
        minimums = {
            "topic": COMPACT_TOPIC_WIDTH if has_topic else 0,
            "main": 380 if has_main else 0,
            "properties": 220 if has_properties else 0,
        }
        overflow = sum(targets.values()) - available
        for key in ("topic", "properties", "main"):
            if overflow <= 0:
                break
            reducible = max(0, targets[key] - minimums[key])
            reduction = min(overflow, reducible)
            targets[key] -= reduction
            overflow -= reduction
        if overflow < 0 and has_main:
            targets["main"] += abs(overflow)
        return targets

    def _preferred_topic_width(self) -> int:
        columns = self._preferred_topic_column_widths()
        if not columns:
            return 340
        max_width = max(420, int(self.window.width() * 0.38))
        return max(320, min(max_width, sum(columns) + 26))

    def _topic_width_target(self) -> int:
        visible_panes = sum(1 for pane in self._panes if not pane.isHidden())
        if visible_panes > 1:
            return COMPACT_TOPIC_WIDTH
        return self._preferred_topic_width()

    def _update_topic_minimum_width(self) -> None:
        if hasattr(self, "topic_tree"):
            self.topic_tree.setMinimumWidth(COMPACT_TOPIC_WIDTH)
        if hasattr(self, "topic_dock"):
            self.topic_dock.setMinimumWidth(COMPACT_TOPIC_WIDTH)

    def _preferred_topic_column_widths(self) -> list[int]:
        if not hasattr(self, "topic_tree"):
            return []
        topics = self._topic_snapshot()
        metrics = self.topic_tree.fontMetrics()
        topic_width = metrics.horizontalAdvance("Topic") + 36
        category_width = metrics.horizontalAdvance("Category") + 28
        count_width = metrics.horizontalAdvance("Count") + 28
        for topic in topics:
            parts = [part for part in topic.name.split("/") if part]
            if not parts:
                parts = [topic.name]
            for depth, part in enumerate(parts):
                indent = 18 * depth
                topic_width = max(topic_width, indent + metrics.horizontalAdvance(part) + 58)
            category_width = max(category_width, metrics.horizontalAdvance(topic.category or "") + 28)
            count_width = max(count_width, metrics.horizontalAdvance(str(topic.message_count)) + 28)
        topic_width = max(150, min(270, topic_width))
        category_width = max(86, min(120, category_width))
        count_width = max(68, min(76, count_width))
        return [topic_width, category_width, count_width]

    def _topic_snapshot(self) -> list[TopicInfo]:
        if self.session.reader is None:
            return list(self.session.topics)
        try:
            return self.session.list_topics()
        except RuntimeError:
            return list(self.session.topics)

    def _animate_resize_docks(self, docks: list[Any], targets: list[int], orientation: Any) -> None:
        if not docks:
            return
        key = (
            "horizontal"
            if orientation == self.QtCore.Qt.Orientation.Horizontal
            else "vertical"
        )
        self._dock_resize_generations[key] += 1
        generation = self._dock_resize_generations[key]
        starts = [
            dock.width() if orientation == self.QtCore.Qt.Orientation.Horizontal else dock.height()
            for dock in docks
        ]
        steps = 6

        def step(index: int) -> None:
            if generation != self._dock_resize_generations[key]:
                return
            progress = index / steps
            eased = 1.0 - ((1.0 - progress) ** 3)
            values = [
                int(start + ((target - start) * eased))
                for start, target in zip(starts, targets)
            ]
            self.window.resizeDocks(docks, values, orientation)
            if index < steps:
                self.QtCore.QTimer.singleShot(18, self.window, lambda: step(index + 1))

        step(1)

    def _populate_topics(self) -> None:
        signals_blocked = self.topic_tree.blockSignals(True)
        try:
            self.topic_tree.clear()
            self._expanded_before_search = None
            self._topic_by_item.clear()
            if hasattr(self, "topic_search"):
                self.topic_search.clear()
            topics = self._topic_snapshot()
            self._topic_info_by_name = {topic.name: topic for topic in topics}
            nodes: dict[tuple[str, ...], Any] = {}
            folder_icon = self.window.style().standardIcon(
                self.QtWidgets.QStyle.StandardPixmap.SP_DirIcon
            )
            topic_icon = self.window.style().standardIcon(
                self.QtWidgets.QStyle.StandardPixmap.SP_FileIcon
            )
            for topic in topics:
                parts = [part for part in topic.name.split("/") if part]
                parent = None
                for depth, part in enumerate(parts):
                    key = tuple(parts[: depth + 1])
                    item = nodes.get(key)
                    if item is None:
                        is_leaf = depth == len(parts) - 1
                        item = self.QtWidgets.QTreeWidgetItem([
                            part,
                            topic.category if is_leaf else "",
                            str(topic.message_count) if is_leaf else "",
                        ])
                        flags = item.flags()
                        if is_leaf:
                            flags |= self.QtCore.Qt.ItemFlag.ItemIsDragEnabled
                            item.setIcon(0, topic_icon)
                            item.setData(0, self.QtCore.Qt.ItemDataRole.UserRole, topic.name)
                        else:
                            flags |= self.QtCore.Qt.ItemFlag.ItemIsDragEnabled
                            flags |= self.QtCore.Qt.ItemFlag.ItemIsSelectable
                            item.setIcon(0, folder_icon)
                            font = item.font(0)
                            font.setBold(True)
                            item.setFont(0, font)
                        item.setFlags(flags)
                        if parent is None:
                            self.topic_tree.addTopLevelItem(item)
                        else:
                            parent.addChild(item)
                        nodes[key] = item
                    parent = item
                if parent is not None:
                    parent.setText(1, topic.category or "")
                    parent.setText(2, str(topic.message_count))
                    parent.setIcon(0, topic_icon)
                    parent.setCheckState(0, self.QtCore.Qt.CheckState.Unchecked)
                    parent.setToolTip(0, f"{topic.name}\n{topic.msgtype}\n{topic.message_count:,} messages")
                    parent.setData(0, self.QtCore.Qt.ItemDataRole.UserRole, topic.name)
                    self._topic_by_item[id(parent)] = topic.name
            self.topic_tree.collapseAll()
        finally:
            self.topic_tree.blockSignals(signals_blocked)
        self._apply_topic_tree_item_styles()
        self._autosize_topic_columns()
        self.topic_panel.setEnabled(not self._opening)
        self._update_topic_status()

    def _update_topic_status(self) -> None:
        topics = [item for item in self._topic_tree_items() if id(item) in self._topic_by_item]
        shown = sum(not item.isHidden() for item in topics)
        checked = len(self.topic_tree._checked_topics())
        self.topic_status.setText(f"{shown}/{len(topics)} topics | {checked} checked")

    def _apply_topic_tree_item_styles(self) -> None:
        palette = _theme_palette(self._theme)
        muted_brush = self.QtGui.QBrush(self.QtGui.QColor(palette["muted"]))
        text_brush = self.QtGui.QBrush(self.QtGui.QColor(palette["text"]))
        for index in range(self.topic_tree.topLevelItemCount()):
            self._apply_topic_tree_item_style(
                self.topic_tree.topLevelItem(index),
                text_brush,
                muted_brush,
            )

    def _apply_topic_tree_item_style(
        self,
        item: Any,
        text_brush: Any,
        muted_brush: Any,
    ) -> None:
        is_topic = id(item) in self._topic_by_item
        font = item.font(0)
        font.setBold(not is_topic)
        item.setFont(0, font)
        item.setForeground(0, text_brush)
        item.setForeground(1, text_brush if is_topic else muted_brush)
        item.setForeground(2, text_brush if is_topic else muted_brush)
        for index in range(item.childCount()):
            self._apply_topic_tree_item_style(item.child(index), text_brush, muted_brush)

    def _expand_topic_tree(self) -> None:
        self.topic_tree.expandAll()

    def _collapse_topic_tree(self) -> None:
        self.topic_search.clear()
        self.topic_tree.collapseAll()

    def _uncheck_topic_tree(self) -> None:
        self._set_checked_topics([])

    def _filter_topic_tree(self, text: str) -> None:
        query = text.strip().lower()
        if query and self._expanded_before_search is None:
            self._expanded_before_search = {id(item) for item in self._topic_tree_items() if item.isExpanded()}
        for index in range(self.topic_tree.topLevelItemCount()):
            item = self.topic_tree.topLevelItem(index)
            self._filter_topic_item(item, query)
        if not query and self._expanded_before_search is not None:
            for item in self._topic_tree_items():
                item.setExpanded(id(item) in self._expanded_before_search)
            self._expanded_before_search = None
        self._update_topic_status()

    def _filter_topic_item(self, item: Any, query: str) -> bool:
        topic = item.data(0, self.QtCore.Qt.ItemDataRole.UserRole)
        searchable = str(topic or item.text(0)).lower()
        self_matches = not query or query in searchable
        child_matches = False
        for index in range(item.childCount()):
            if self._filter_topic_item(item.child(index), query):
                child_matches = True
        visible = self_matches or child_matches
        item.setHidden(not visible)
        if query:
            item.setExpanded(child_matches)
        return visible

    def _checked_lane_roles(self) -> list[str]:
        checked: set[str] = set()
        for item in self._topic_tree_items():
            if item.checkState(0) != self.QtCore.Qt.CheckState.Checked:
                continue
            topic = item.data(0, self.QtCore.Qt.ItemDataRole.UserRole)
            if not topic:
                continue
            info = self._topic_info_by_name.get(str(topic))
            if info is None:
                continue
            role = lane_role_for_topic(info)
            if role is not None:
                checked.add(role)
        return [role for role in LANE_ROLES if role in checked]

    def _topic_tree_items(self) -> list[Any]:
        items: list[Any] = []

        def collect(item: Any) -> None:
            items.append(item)
            for index in range(item.childCount()):
                collect(item.child(index))

        for index in range(self.topic_tree.topLevelItemCount()):
            collect(self.topic_tree.topLevelItem(index))
        return items

    def _set_checked_topics(self, topics: list[str]) -> None:
        selected = set(topics)
        signals_blocked = self.topic_tree.blockSignals(True)
        try:
            for item in self._topic_tree_items():
                topic = self._topic_by_item.get(id(item))
                if topic is None:
                    continue
                state = (
                    self.QtCore.Qt.CheckState.Checked
                    if topic in selected
                    else self.QtCore.Qt.CheckState.Unchecked
                )
                item.setCheckState(0, state)
        finally:
            self.topic_tree.blockSignals(signals_blocked)
        self._set_lane_selection(self._checked_lane_roles())
        self._update_topic_status()

    def _set_lane_selection(self, roles: list[str]) -> None:
        self._selected_lane_roles = roles
        for role, checkbox in self.lane_overlay.checkboxes.items():
            blocker = self.QtCore.QSignalBlocker(checkbox)
            checkbox.setChecked(role in roles)
            del blocker
        self.lane_overlay.plot.set_visible_roles(roles)
        blocker = self.QtCore.QSignalBlocker(self.topic_tree)
        for item in self._topic_tree_items():
            name = self._topic_by_item.get(id(item))
            info = self._topic_info_by_name.get(name)
            role = lane_role_for_topic(info) if info else None
            if role is not None:
                item.setCheckState(0, self.QtCore.Qt.CheckState.Checked if role in roles else self.QtCore.Qt.CheckState.Unchecked)
        del blocker
        self._refresh_lane_view_panes()
        self._update_topic_status()

    def _lane_roles_for_view(self, topic_info: TopicInfo | None) -> list[str]:
        if topic_info is None:
            return []
        role = lane_role_for_topic(topic_info)
        if role is None:
            return []
        if self._selected_lane_roles is not None:
            return self._selected_lane_roles
        checked_roles = self._checked_lane_roles()
        if checked_roles:
            return checked_roles
        if hasattr(self, "lane_overlay"):
            visible_roles = self.lane_overlay.visible_roles()
            if visible_roles:
                return visible_roles
        return [role]

    def _refresh_lane_view_panes(self) -> None:
        timestamp_ns = self._current_timestamp_ns()
        for pane in self._all_panes():
            if pane.is_lane_topic():
                pane.show_at_timestamp(timestamp_ns)

    def _set_lane_swap_xy(self, swapped: bool) -> None:
        self._lane_swap_xy = swapped
        if hasattr(self, "lane_overlay"):
            self.lane_overlay.set_swap_xy(swapped)
        for pane in self._all_panes():
            pane.set_lane_swap_xy(swapped)
        self._refresh_lane_view_panes()

    def _autosize_topic_columns(self) -> None:
        widths = self._preferred_topic_column_widths()
        for column, width in enumerate(widths):
            self.topic_tree.setColumnWidth(column, width)
        self._update_topic_minimum_width()


    def _clear_lane_overlay(self) -> None:
        self._lane_load_generation += 1
        if self._lane_load_token is not None:
            self._lane_load_token.cancel()
        self._lane_topics_by_role = {}
        self._lane_overlay_data = None
        self._selected_lane_roles = None
        if hasattr(self, "lane_overlay"):
            self.lane_overlay.set_topics({})
        if hasattr(self, "_panes"):
            self._refresh_lane_view_panes()

    def _prepare_lane_overlay(self, topics: list[TopicInfo]) -> None:
        self._lane_load_generation += 1
        generation = self._lane_load_generation
        self._lane_topics_by_role = lane_topics(topics)
        self._lane_overlay_data = None
        self.lane_overlay.set_topics(self._lane_topics_by_role)
        self._set_lane_selection(list(self._lane_topics_by_role))
        if not self._lane_topics_by_role:
            self.lane_overlay_dock.hide()
        if not self._lane_topics_by_role:
            return
        self._start_lane_overlay_load(generation)

    def _start_lane_overlay_load(self, generation: int) -> None:
        bag_path = self.session.bag_path
        if bag_path is None:
            return
        backend = self.session.backend
        topics = list(self._lane_topics_by_role.values())
        self.lane_overlay.set_loading()
        if self._lane_load_token is not None:
            self._lane_load_token.cancel()
        token = CancellationToken.create()
        self._lane_load_token = token

        def work() -> LaneOverlayData:
            worker_session = Session(backend=backend)
            try:
                worker_session.open_bag(bag_path)
                if worker_session.reader is None:
                    return LaneOverlayData(series_by_role={})
                return build_lane_overlay_data(worker_session.reader, topics, cancellation_token=token)
            finally:
                worker_session.close()

        def handle_success(data: LaneOverlayData) -> None:
            if generation != self._lane_load_generation:
                return
            self._lane_overlay_data = data
            self.lane_overlay.set_data(data)
            self.lane_overlay.show_at_timestamp(self._current_timestamp_ns())
            self._refresh_lane_view_panes()
            loaded = ", ".join(
                f"{series.role}={len(series.frames)}"
                for series in data.ordered_series()
            )
            self._log(f"Lane line overlay loaded: {loaded} frame(s)")

        def handle_error(message: str) -> None:
            if generation != self._lane_load_generation:
                return
            self.lane_overlay.set_error(f"Lane line overlay failed: {message}")
            self._log(f"Lane line overlay failed: {message}")

        self._run_background(
            title="Loading lane line overlay",
            label="Loading lane line PointCloud2 frames...",
            work=work,
            on_success=handle_success,
            on_error=handle_error,
            parent=self.window,
            modal=False,
        )

    def _on_lane_overlay_selection_changed(self) -> None:
        self._set_lane_selection(self.lane_overlay.visible_roles())
        self.lane_overlay.show_at_timestamp(self._current_timestamp_ns())
        self._refresh_lane_view_panes()

    def _on_topic_selection_changed(self) -> None:
        topic = self._selected_topic()
        self._apply_topic_settings(topic)

    def _on_topic_double_clicked(self, item: Any, _column: int) -> None:
        topic = item.data(0, self.QtCore.Qt.ItemDataRole.UserRole)
        if topic and self._active_pane is not None:
            selected = self.topic_tree.selectedItems()
            self.assign_topics_to_pane(self._active_pane, self.topic_tree._topics_under_items(selected or [item]))

    def _on_topic_item_changed(self, item: Any, _column: int) -> None:
        self._update_topic_status()
        topic = item.data(0, self.QtCore.Qt.ItemDataRole.UserRole)
        if not topic:
            return
        info = self._topic_info_by_name.get(str(topic))
        if info is None or lane_role_for_topic(info) is None:
            return
        self._set_lane_selection(self._checked_lane_roles())

    def _apply_topic_settings(self, topic: str | None) -> None:
        self._properties_topic = topic
        settings = self.settings.topics.get(topic)
        controls = (self.visible_check, self.color_button, self.point_size, self.decimation, self.opacity, self.sync_offset)
        info = self._topic_info_by_name.get(topic)
        is_cloud = info is not None and info.category == "point_cloud"
        is_lane = info is not None and lane_role_for_topic(info) is not None
        for control in controls:
            control.setEnabled(settings is not None)
        for control in (self.color_button, self.point_size, self.decimation):
            control.setEnabled(settings is not None and is_cloud and not is_lane)
            self.properties_form.setRowVisible(control, is_cloud and not is_lane)
        self.properties_topic_label.setText(topic or "No topic selected")
        self.properties_topic_label.setToolTip(topic or "")
        if settings is None:
            return
        blockers = [self.QtCore.QSignalBlocker(control) for control in controls]
        self.visible_check.setChecked(settings.visible)
        self.point_size.setValue(settings.point_size)
        self.decimation.setValue(settings.decimation)
        self.opacity.setValue(settings.opacity)
        self.sync_offset.setValue(settings.sync_offset_sec)
        self.color_button.setStyleSheet(f"background-color: {settings.color};")
        self.color_button.setText("")
        self.color_button.setAccessibleName("Point color")
        self.color_button.setToolTip(settings.color)
        del blockers

    def _store_topic_settings(self, _value: Any = None) -> None:
        settings = self.settings.topics.get(self._properties_topic)
        if settings is None:
            return
        settings.visible = self.visible_check.isChecked()
        settings.point_size = self.point_size.value()
        settings.decimation = self.decimation.value()
        settings.opacity = self.opacity.value()
        settings.sync_offset_sec = self.sync_offset.value()
        for pane in self._all_panes():
            if settings.topic in pane.point_topics and not pane.is_lane_topic():
                pane.cancel_pending()
        self._request_preview_update()

    def _choose_topic_color(self) -> None:
        settings = self.settings.topics.get(self._properties_topic)
        if settings is None:
            return
        color = self.QtWidgets.QColorDialog.getColor(self.QtGui.QColor(settings.color), self.window, "Point color")
        if color.isValid():
            settings.color = color.name()
            self._apply_topic_settings(settings.topic)
            self._store_topic_settings()

    def _on_time_input_changed(self, value: float) -> None:
        self._pause_playback()
        maximum = self.time_input.maximum() or 1.0
        self.time_slider.blockSignals(True)
        self.time_slider.setValue(int((value / maximum) * self.time_slider.maximum()))
        self.time_slider.blockSignals(False)
        self._request_preview_update()

    def _on_slider_changed(self, value: int) -> None:
        self._pause_playback()
        maximum = self.time_input.maximum()
        seconds = (value / max(1, self.time_slider.maximum())) * maximum
        self.time_input.blockSignals(True)
        self.time_input.setValue(seconds)
        self.time_input.blockSignals(False)
        self._request_preview_update()

    def _on_playback_rate_changed(self, text: str) -> None:
        try:
            self._playback_rate = float(text.rstrip("x"))
        except ValueError:
            self._playback_rate = 1.0

    def _set_theme(self, theme: str) -> None:
        normalized = _normalize_theme(theme)
        if normalized == self._theme:
            self._sync_theme_actions()
            self._sync_theme_toggle()
            return
        self._theme = normalized
        self._update_settings.setValue("ui/theme", normalized)
        self._sync_theme_actions()
        self._apply_theme()

    def _sync_theme_actions(self) -> None:
        for theme, action in self._theme_actions.items():
            action.blockSignals(True)
            action.setChecked(theme == self._theme)
            action.blockSignals(False)

    def _sync_theme_toggle(self) -> None:
        if not hasattr(self, "theme_toggle"):
            return
        self.theme_toggle.blockSignals(True)
        self.theme_toggle.setChecked(self._theme == "dark")
        self.theme_toggle.blockSignals(False)

    def _apply_theme(self) -> None:
        if self.workflows is not None:
            self.workflows.apply_theme()
        palette = _theme_palette(self._theme)
        stylesheet = _theme_stylesheet(palette)
        app = self.QtWidgets.QApplication.instance()
        if app is not None:
            if app.style().objectName().lower() != "rosbagel":
                app.setStyle(create_gui_style(self.QtWidgets, self.QtCore, self.QtGui))
            qt_palette = self.QtGui.QPalette()
            roles = [
                self.QtGui.QPalette.ColorGroup.Active,
                self.QtGui.QPalette.ColorGroup.Inactive,
            ]
            for group in roles:
                qt_palette.setColor(group, self.QtGui.QPalette.ColorRole.Mid, self.QtGui.QColor(palette["muted"]))
                qt_palette.setColor(group, self.QtGui.QPalette.ColorRole.Window, self.QtGui.QColor(palette["window"]))
                qt_palette.setColor(group, self.QtGui.QPalette.ColorRole.WindowText, self.QtGui.QColor(palette["text"]))
                qt_palette.setColor(group, self.QtGui.QPalette.ColorRole.Base, self.QtGui.QColor(palette["input"]))
                qt_palette.setColor(group, self.QtGui.QPalette.ColorRole.AlternateBase, self.QtGui.QColor(palette["panel"]))
                qt_palette.setColor(group, self.QtGui.QPalette.ColorRole.Text, self.QtGui.QColor(palette["text"]))
                qt_palette.setColor(group, self.QtGui.QPalette.ColorRole.Button, self.QtGui.QColor(palette["button"]))
                qt_palette.setColor(group, self.QtGui.QPalette.ColorRole.ButtonText, self.QtGui.QColor(palette["text"]))
                qt_palette.setColor(group, self.QtGui.QPalette.ColorRole.Highlight, self.QtGui.QColor(palette["accent"]))
                qt_palette.setColor(group, self.QtGui.QPalette.ColorRole.HighlightedText, self.QtGui.QColor(palette["highlight_text"]))
                qt_palette.setColor(group, self.QtGui.QPalette.ColorRole.ToolTipBase, self.QtGui.QColor(palette["panel_alt"]))
                qt_palette.setColor(group, self.QtGui.QPalette.ColorRole.ToolTipText, self.QtGui.QColor(palette["text"]))
                qt_palette.setColor(group, self.QtGui.QPalette.ColorRole.PlaceholderText, self.QtGui.QColor(palette["muted"]))
            qt_palette.setColor(self.QtGui.QPalette.ColorGroup.Disabled, self.QtGui.QPalette.ColorRole.WindowText, self.QtGui.QColor(palette["muted"]))
            qt_palette.setColor(self.QtGui.QPalette.ColorGroup.Disabled, self.QtGui.QPalette.ColorRole.Text, self.QtGui.QColor(palette["muted"]))
            qt_palette.setColor(self.QtGui.QPalette.ColorGroup.Disabled, self.QtGui.QPalette.ColorRole.ButtonText, self.QtGui.QColor(palette["muted"]))
            app.setPalette(qt_palette)
        self.window.setStyleSheet(stylesheet)
        for pane in self._all_panes():
            pane.apply_theme(palette)
        if hasattr(self, "lane_overlay"):
            self.lane_overlay.apply_theme(palette)
        if hasattr(self, "topic_tree"):
            self._apply_topic_tree_item_styles()
            for button, symbol in (
                (self.topic_expand_button, self.QtWidgets.QStyle.StandardPixmap.SP_ArrowDown),
                (self.topic_collapse_button, self.QtWidgets.QStyle.StandardPixmap.SP_ArrowUp),
                (self.topic_uncheck_button, self.QtWidgets.QStyle.StandardPixmap.SP_DialogResetButton),
            ):
                button.setIcon(tinted_standard_icon(self.QtGui, self.window, symbol, palette))
        self._sync_theme_toggle()

    def _request_preview_update(self) -> None:
        if self.play_timer.isActive():
            self.preview_timer.stop()
            self._update_preview()
            return
        self.preview_timer.start()

    def _toggle_playback(self) -> None:
        if self.play_timer.isActive():
            self._pause_playback()
            return
        if self._opening or self._bag_start_ns is None or self.time_input.maximum() <= 0:
            return
        if self.time_input.value() >= self.time_input.maximum():
            self._set_timeline_seconds(0.0, immediate=False)
        if not self._prepare_image_playback():
            return
        self.play_button.setText("Pause")
        self.time_slider.setTracking(True)
        self.preview_timer.stop()
        self._update_preview()
        self.play_clock.start()
        self.play_timer.start()

    def _pause_playback(self) -> None:
        self.play_timer.stop()
        self.play_button.setText("Play")

    def _prepare_image_playback(self) -> bool:
        image_panes = [
            pane for pane in self._all_panes()
            if pane.isVisible() and pane.is_image_topic()
        ]
        if not image_panes:
            self._request_preview_update()
            return True
        for pane in image_panes:
            if not pane.ensure_rendered_for_playback():
                self._log(f"Playback render was cancelled for {pane.topic}.")
                return False
        return True

    def _advance_playback(self) -> None:
        elapsed = self.play_clock.nsecsElapsed() / 1e9 if self.play_clock.isValid() else 1.0 / self._playback_fps
        self.play_clock.start()
        if any(pane._rendering_playback for pane in self._all_panes() if pane.isVisible()):
            return
        next_time = self.time_input.value() + (elapsed * self._playback_rate)
        reached_end = False
        if next_time >= self.time_input.maximum():
            next_time = self.time_input.maximum()
            reached_end = True
        self._set_timeline_seconds(next_time, immediate=True)
        if reached_end:
            self._pause_playback()

    def _step_forward(self) -> None:
        self._pause_playback()
        self._set_timeline_seconds(
            min(self.time_input.maximum(), self.time_input.value() + (1.0 / self._playback_fps)),
            immediate=True,
        )

    def _set_timeline_seconds(self, seconds: float, *, immediate: bool) -> None:
        maximum = self.time_input.maximum() or 1.0
        self.time_input.blockSignals(True)
        self.time_input.setValue(seconds)
        self.time_input.blockSignals(False)
        self.time_slider.blockSignals(True)
        self.time_slider.setValue(round((self.time_input.value() / maximum) * self.time_slider.maximum()))
        self.time_slider.blockSignals(False)
        if immediate:
            self._update_preview()
        else:
            self._request_preview_update()

    def _selected_topic(self) -> str | None:
        items = self.topic_tree.selectedItems()
        if not items:
            return None
        return self._topic_by_item.get(id(items[0]))

    def _topics_from_mime(self, mime: Any) -> list[str]:
        if mime.hasFormat(TOPICS_MIME):
            payload = bytes(mime.data(TOPICS_MIME)).decode("utf-8", errors="replace")
            return self._dedupe_topic_text(payload)
        if mime.hasFormat(TOPIC_MIME):
            payload = bytes(mime.data(TOPIC_MIME)).decode("utf-8", errors="replace")
            return self._dedupe_topic_text(payload)
        if mime.hasText():
            return self._dedupe_topic_text(mime.text())
        return []

    def _dedupe_topic_text(self, text: str) -> list[str]:
        topics: list[str] = []
        seen: set[str] = set()
        for line in text.splitlines():
            topic = line.strip()
            if not topic or topic in seen:
                continue
            seen.add(topic)
            topics.append(topic)
        return topics

    def _current_timestamp_ns(self) -> int | None:
        if self._bag_start_ns is None:
            return None
        seconds = self.time_input.value()
        return self._bag_start_ns + int(seconds * 1e9)

    def _update_preview(self) -> None:
        if self._opening or self._closed:
            return
        timestamp_ns = self._current_timestamp_ns()
        if timestamp_ns is None:
            return
        self.workflows.on_time(timestamp_ns)
        if self.preview is not None:
            for pane in self._all_panes():
                if pane.isVisible():
                    pane.show_at_timestamp(timestamp_ns)
        self.lane_overlay.plot.set_display_settings(self._lane_display_settings())
        self.lane_overlay.show_at_timestamp(timestamp_ns)

    def _lane_display_settings(self) -> dict[str, tuple[bool, float, float]]:
        result = {}
        for role, info in self._lane_topics_by_role.items():
            settings = self.settings.topics.get(info.name)
            if settings:
                result[role] = (settings.visible, settings.opacity, settings.sync_offset_sec)
        return result

    def _maybe_offer_startup_update_check(self) -> None:
        if self._closed:
            return
        mode = str(self._update_settings.value("updates/mode", "") or "")
        if mode not in {"check", "auto", "off"}:
            mode = self._ask_update_preference()
            self._update_settings.setValue("updates/mode", mode)
        if mode == "off":
            return

        def handle_result(info: UpdateInfo | None) -> None:
            if info is None or not info.update_available:
                return
            if mode == "auto":
                self._run_upgrade_from_gui(info, automatic=True)
            else:
                self._show_version_dialog(info)

        self._start_update_check(show_no_update=False, on_result=handle_result)

    def _ask_update_preference(self) -> str:
        box = self.QtWidgets.QMessageBox(self.window)
        box.setWindowTitle("Update checker")
        box.setIcon(self.QtWidgets.QMessageBox.Icon.Question)
        box.setText("How should ROSBagel check for updates on startup?")
        box.setInformativeText(
            "The checker contacts the GitHub release API. You can change this later "
            "from File > Version."
        )
        auto_button = box.addButton("Auto update", self.QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        check_button = box.addButton("Check only", self.QtWidgets.QMessageBox.ButtonRole.YesRole)
        off_button = box.addButton("Turn off", self.QtWidgets.QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(check_button)
        box.exec()
        clicked = box.clickedButton()
        if clicked is auto_button:
            return "auto"
        if clicked is off_button:
            return "off"
        return "check"

    def _show_version_dialog(self, update_info: UpdateInfo | None = None) -> None:
        dialog = self.QtWidgets.QDialog(self.window)
        dialog.setWindowTitle("ROSBagel version")
        dialog.resize(720, 560)
        layout = self.QtWidgets.QVBoxLayout(dialog)

        current_label = self.QtWidgets.QLabel(f"Current version: {current_version()}")
        current_label.setStyleSheet("font-weight: 600;")
        status_label = self.QtWidgets.QLabel()
        status_label.setWordWrap(True)
        notes = self.QtWidgets.QTextEdit()
        notes.setReadOnly(True)
        notes.setPlainText(_local_changelog_text())
        layout.addWidget(current_label)
        credits = self.QtWidgets.QLabel("Author: Owen ZI-WEN ZHOU (Nanato)\nLicense: AGPL-3.0-or-later")
        credits.setWordWrap(True)
        layout.addWidget(credits)
        layout.addWidget(status_label)
        layout.addWidget(notes, 1)

        preference_row = self.QtWidgets.QHBoxLayout()
        preference_row.addWidget(self.QtWidgets.QLabel("Startup updates"))
        preference_box = self.QtWidgets.QComboBox()
        preference_box.addItem("Check only", "check")
        preference_box.addItem("Auto update", "auto")
        preference_box.addItem("Off", "off")
        stored_mode = str(self._update_settings.value("updates/mode", "check") or "check")
        index = preference_box.findData(stored_mode)
        preference_box.setCurrentIndex(max(0, index))
        preference_box.currentIndexChanged.connect(
            lambda _index: self._update_settings.setValue(
                "updates/mode", preference_box.currentData()
            )
        )
        preference_row.addWidget(preference_box)
        preference_row.addStretch(1)
        layout.addLayout(preference_row)

        theme_row = self.QtWidgets.QHBoxLayout()
        theme_row.addWidget(self.QtWidgets.QLabel("Appearance"))
        dark_mode = self.QtWidgets.QCheckBox("Dark mode")
        dark_mode.setChecked(self._theme == "dark")
        dark_mode.toggled.connect(lambda checked: self._set_theme("dark" if checked else "light"))
        theme_row.addWidget(dark_mode)
        theme_row.addStretch(1)
        layout.addLayout(theme_row)

        buttons = self.QtWidgets.QDialogButtonBox(
            self.QtWidgets.QDialogButtonBox.StandardButton.Close
        )
        check_button = self.QtWidgets.QPushButton("Check update")
        upgrade_button = self.QtWidgets.QPushButton("Upgrade")
        buttons.addButton(check_button, self.QtWidgets.QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(upgrade_button, self.QtWidgets.QDialogButtonBox.ButtonRole.ActionRole)
        layout.addWidget(buttons)

        def apply_info(info: UpdateInfo | None) -> None:
            upgrade_button.setEnabled(bool(info and info.update_available and info.latest_ref))
            if info is None:
                status_label.setText("Local changelog is shown below.")
                return
            self._latest_update_info = info
            if info.error:
                status_label.setText(f"Update check failed: {info.error}")
                return
            latest = info.latest_ref or info.latest_version or "unknown"
            if info.update_available:
                status_label.setText(
                    f"New version available: {latest} "
                    f"(installed {info.current_version})."
                )
                notes.setPlainText(info.changes or "No release notes were provided.")
            else:
                status_label.setText(
                    f"No newer version found. Latest: {latest}; "
                    f"installed: {info.current_version}."
                )

        def check_now() -> None:
            check_button.setEnabled(False)

            def finish(info: UpdateInfo | None) -> None:
                apply_info(info)
                check_button.setEnabled(True)

            self._start_update_check(
                show_no_update=True,
                parent=dialog,
                on_result=finish,
            )

        def upgrade_now() -> None:
            if self._latest_update_info is not None:
                self._run_upgrade_from_gui(self._latest_update_info, parent=dialog)

        check_button.clicked.connect(check_now)
        upgrade_button.clicked.connect(upgrade_now)
        buttons.rejected.connect(dialog.reject)
        apply_info(update_info)
        dialog.exec()

    def _start_update_check(
        self,
        *,
        show_no_update: bool,
        parent: Any | None = None,
        on_result: Any | None = None,
    ) -> None:
        def handle_success(info: UpdateInfo) -> None:
            self._latest_update_info = info
            if info.error:
                if show_no_update:
                    self._show_warning(f"Update check failed: {info.error}")
            elif not info.update_available and show_no_update:
                latest = info.latest_ref or info.latest_version or "unknown"
                self.QtWidgets.QMessageBox.information(
                    parent or self.window,
                    "ROSBagel update",
                    f"No newer version found.\nLatest: {latest}\nInstalled: {info.current_version}",
                )
            if on_result is not None:
                on_result(info)

        def handle_error(message: str) -> None:
            if show_no_update:
                self._show_warning(f"Update check failed: {message}")
            if on_result is not None:
                on_result(None)

        self._run_background(
            title="Checking for updates",
            label="Checking for ROSBagel updates...",
            work=check_for_update,
            on_success=handle_success,
            on_error=handle_error,
            parent=parent,
        )

    def _run_upgrade_from_gui(
        self,
        info: UpdateInfo,
        *,
        parent: Any | None = None,
        automatic: bool = False,
    ) -> None:
        if not info.latest_ref:
            self._show_warning("No upgrade target was found. Run Check update first.")
            return
        if not automatic:
            answer = self.QtWidgets.QMessageBox.question(
                parent or self.window,
                "Upgrade ROSBagel",
                f"Upgrade ROSBagel to {info.latest_ref}?\n\n"
                "The application should be restarted after upgrade.",
                self.QtWidgets.QMessageBox.StandardButton.Yes
                | self.QtWidgets.QMessageBox.StandardButton.No,
            )
            if answer != self.QtWidgets.QMessageBox.StandardButton.Yes:
                return
        def work() -> None:
            run_upgrade(build_upgrade_plan(ref=info.latest_ref))

        def finish(_result: object) -> None:
            self.QtWidgets.QMessageBox.information(
                parent or self.window,
                "Upgrade complete",
                "Upgrade finished. Restart ROSBagel to use the updated code.",
            )

        self._run_background(
            title="Upgrade ROSBagel",
            label=f"Upgrading ROSBagel to {info.latest_ref}...",
            work=work,
            on_success=finish,
            on_error=lambda message: self._show_warning(f"Upgrade failed: {message}"),
            parent=parent,
        )

    def _import_bag(self) -> None:
        path = self.QtWidgets.QFileDialog.getExistingDirectory(
            self.window, "Select ROS bag folder"
        )
        if path:
            self.open_bag(path)

    def _import_bag_file(self) -> None:
        path, _filter = self.QtWidgets.QFileDialog.getOpenFileName(
            self.window, "Open ROS bag file", "", "ROS bags (*.bag *.db3 *.mcap);;All files (*)"
        )
        if path:
            self.open_bag(path)

    def _show_export_dialog(self) -> None:
        from rosbagel.gui.export_dialog import show_export_dialog
        show_export_dialog(self)

    def _all_panes(self) -> list[Any]:
        return [*self._panes, *self._window_panes]

    def _start_progress(self, label: str, total: int | None) -> None:
        self.progress_bar.setTextVisible(True)
        if total is None or total <= 0:
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setFormat(label)
        else:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat(f"{label} 0/{total}")

    def _set_progress(self, label: str, value: int, total: int | None) -> None:
        if total is None or total <= 0:
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setFormat(f"{label} {value}")
        else:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(min(value, total))
            self.progress_bar.setFormat(f"{label} {min(value, total)}/{total}")

    def _finish_progress(self, label: str = "Ready") -> None:
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat(label)

    def _show_warning(self, message: str) -> None:
        self.QtWidgets.QMessageBox.warning(self.window, "ROSBagel", message)
        self._log(message)

    def _log(self, message: str) -> None:
        self.log_text.append(message)

    def _run_background(
        self,
        *,
        title: str,
        label: str,
        work: Any,
        on_success: Any,
        on_error: Any | None = None,
        parent: Any | None = None,
        modal: bool = True,
    ) -> None:
        progress = self.QtWidgets.QProgressDialog(
            label,
            None,
            0,
            0,
            parent or self.window,
        )
        progress.setWindowTitle(title)
        progress.setWindowModality(self.QtCore.Qt.WindowModality.WindowModal if modal else self.QtCore.Qt.WindowModality.NonModal)
        progress.setMinimumDuration(0)

        results: Queue[tuple[bool, object]] = Queue(maxsize=1)

        def target() -> None:
            try:
                results.put((True, work()))
            except Exception as exc:
                results.put((False, str(exc)))

        thread = Thread(target=target, daemon=True)
        timer = self.QtCore.QTimer(parent or self.window)
        timer.setInterval(50)
        job = (thread, timer, progress, results)
        self._background_jobs.append(job)

        def cleanup() -> None:
            timer.stop()
            progress.close()
            if job in self._background_jobs:
                self._background_jobs.remove(job)

        def poll() -> None:
            try:
                ok, payload = results.get_nowait()
            except Empty:
                return
            cleanup()
            if ok:
                on_success(payload)
            elif on_error is not None:
                on_error(str(payload))
            else:
                self._show_warning(str(payload))

        timer.timeout.connect(poll)
        progress.show()
        timer.start()
        thread.start()


def run_gui(bag_path: str | Path | None = None) -> None:
    _QtCore, _QtGui, QtWidgets = _require_pyside6()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    viewer = TimelineViewer(bag_path)
    viewer.show()
    try:
        app.exec()
    finally:
        viewer._close()


def _create_topic_tree_class(QtWidgets: Any, QtCore: Any) -> type:
    class TopicTreeWidget(QtWidgets.QTreeWidget):
        def resizeEvent(self, event: Any) -> None:
            super().resizeEvent(event)
            compact = self.viewport().width() < 380
            self.setColumnHidden(1, compact)
            self.setColumnHidden(2, compact)
            mode = QtWidgets.QHeaderView.ResizeMode.Stretch if compact else QtWidgets.QHeaderView.ResizeMode.Interactive
            self.header().setSectionResizeMode(0, mode)

        def mimeData(self, items: list[Any]) -> Any:
            mime = QtCore.QMimeData()
            topics = self._drag_topics(items)
            if not topics:
                return mime
            text = "\n".join(topics)
            mime.setData(TOPIC_MIME, topics[0].encode("utf-8"))
            mime.setData(TOPICS_MIME, text.encode("utf-8"))
            mime.setText(text)
            return mime

        def _drag_topics(self, items: list[Any]) -> list[str]:
            folders = [item for item in items if item.childCount() > 0]
            if folders:
                return self._dedupe_topics(self._topics_under_items(items))
            checked_topics = self._checked_topics()
            if checked_topics and any(item.checkState(0) == QtCore.Qt.CheckState.Checked for item in items):
                return checked_topics
            return self._dedupe_topics(self._topics_under_items(items))

        def _checked_topics(self) -> list[str]:
            root = self.invisibleRootItem()
            topics: list[str] = []

            def collect(item: Any) -> None:
                topic = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
                if (
                    topic
                    and item.checkState(0) == QtCore.Qt.CheckState.Checked
                ):
                    topics.append(str(topic))
                for index in range(item.childCount()):
                    collect(item.child(index))

            for index in range(root.childCount()):
                collect(root.child(index))
            return self._dedupe_topics(topics)

        def _topics_under_items(self, items: list[Any]) -> list[str]:
            topics: list[str] = []

            for item in items:
                topic = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
                if topic:
                    topics.append(str(topic))
                for index in range(item.childCount()):
                    topics.extend(self._topics_under_items([item.child(index)]))
            return topics

        @staticmethod
        def _dedupe_topics(topics: list[str]) -> list[str]:
            deduped: list[str] = []
            seen: set[str] = set()
            for topic in topics:
                if topic in seen:
                    continue
                seen.add(topic)
                deduped.append(topic)
            return deduped

    return TopicTreeWidget


def _create_view_pane_class(QtWidgets: Any, QtCore: Any, QtGui: Any) -> type:
    class TopicViewPane(QtWidgets.QFrame):
        def __init__(self, owner: TimelineViewer, parent: Any | None = None) -> None:
            super().__init__(parent)
            self.owner = owner
            self.view_title = "View"
            self.topic: str | None = None
            self.topic_info: TopicInfo | None = None
            self.point_topics: list[str] = []
            self.rendered_frames: list[RenderedFrame] = []
            self.rendered_timestamps: list[int] = []
            self.rendered_size: tuple[int, int] | None = None
            self.rendered_window_start_ns: int | None = None
            self.rendered_reaches_topic_end = False
            self._rendering_playback = False
            self._preview_loading = False
            self._latest_timestamp_ns: int | None = None
            self._failed_render = False
            self._last_preview_error: str | None = None
            self._drag_start: Any = None
            self._drag_cancelled = False
            self._drag_in_progress = False
            self._lane_plot_data: LaneOverlayData | None = None
            self._lane_plot_roles: tuple[str, ...] = ()
            self.setAcceptDrops(True)
            self.setObjectName("topicViewPane")
            self.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)

            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(6, 6, 6, 6)
            layout.setSpacing(2)
            top_bar = QtWidgets.QHBoxLayout()
            top_bar.setContentsMargins(0, 0, 0, 0)
            self.title_label = QtWidgets.QLabel("Drop topic here")
            self.title_label.setObjectName("viewTitle")
            self.title_label.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
            self.title_label.setMinimumWidth(56)
            self.title_label.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Ignored,
                QtWidgets.QSizePolicy.Policy.Preferred,
            )
            self.render_button = QtWidgets.QToolButton()
            self.render_button.setText("Render")
            self.render_button.setFixedSize(58, 26)
            self.render_button.setEnabled(False)
            self.render_button.setToolTip("Prepare an image playback cache")
            self.render_button.setAccessibleName("Render playback")
            self.split_button = QtWidgets.QToolButton()
            self.split_button.setText("+")
            self.split_button.setFixedSize(28, 26)
            self.split_button.setToolTip("Split this view")
            self.split_button.setAccessibleName("Split view")
            self.xy_button = QtWidgets.QToolButton()
            self.xy_button.setText("XY")
            self.xy_button.setFixedSize(34, 26)
            self.xy_button.setCheckable(True)
            self.xy_button.setEnabled(False)
            self.xy_button.setToolTip("Swap x/y axes for lane line plots")
            self.xy_button.setAccessibleName("Swap axes")
            self.view_help_button = QtWidgets.QToolButton()
            self.view_help_button.setObjectName("viewHelpIndicator")
            self.view_help_button.setText("?")
            self.view_help_button.setFixedSize(28, 26)
            self.view_help_button.setAutoRaise(True)
            self.view_help_button.setVisible(False)
            self.fit_button = QtWidgets.QToolButton()
            self.fit_button.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DialogResetButton))
            self.fit_button.setFixedSize(28, 26)
            self.fit_button.setAccessibleName("Fit view")
            self.fit_button.setToolTip("Fit current points to the view")
            self.fit_button.setVisible(False)
            self.fit_button.clicked.connect(self._fit_view)
            pane_icon_size = QtCore.QSize(16, 16)
            pane_button_size = QtCore.QSize(28, 26)
            pane_style = self.style()
            self.max_button = QtWidgets.QToolButton()
            self.max_button.setIcon(
                pane_style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_TitleBarMaxButton)
            )
            self.max_button.setIconSize(pane_icon_size)
            self.max_button.setFixedSize(pane_button_size)
            self.max_button.setAccessibleName("Maximize view")
            self.max_button.setToolTip("Maximize or restore this view")
            self.new_window_button = QtWidgets.QToolButton()
            self.new_window_button.setIcon(
                pane_style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_TitleBarNormalButton)
            )
            self.new_window_button.setIconSize(pane_icon_size)
            self.new_window_button.setFixedSize(pane_button_size)
            self.new_window_button.setAccessibleName("New window")
            self.new_window_button.setToolTip("Move this view to a new window")
            self.delete_button = QtWidgets.QToolButton()
            self.delete_button.setIcon(
                pane_style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_TitleBarCloseButton)
            )
            self.delete_button.setIconSize(pane_icon_size)
            self.delete_button.setFixedSize(pane_button_size)
            self.delete_button.setAccessibleName("Close view")
            self.delete_button.setToolTip("Close this view")
            top_bar.addWidget(self.title_label, 1)
            top_bar.addWidget(self.view_help_button)
            top_bar.addWidget(self.fit_button)
            top_bar.addWidget(self.render_button)
            top_bar.addWidget(self.split_button)
            top_bar.addWidget(self.xy_button)
            top_bar.addWidget(self.max_button)
            top_bar.addWidget(self.new_window_button)
            top_bar.addWidget(self.delete_button)
            layout.addLayout(top_bar)
            self.cloud_legend = QtWidgets.QLabel()
            self.cloud_legend.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
            self.cloud_legend.hide()
            layout.addWidget(self.cloud_legend)

            self.stack = QtWidgets.QStackedWidget()
            self.image_label = QtWidgets.QLabel("Drop a topic here.")
            self.image_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self.image_label.setMinimumSize(320, 160)
            self.point_renderer = create_point_cloud_renderer(QtWidgets)
            self.point_widget = self.point_renderer.widget()
            self.lane_plot = owner.LaneOverlayPanel.PlotWidget(self)
            self.lane_plot.set_empty_text("Drop a lane line PointCloud2 topic here.")
            self.raw_text = QtWidgets.QTextEdit()
            self.raw_text.setReadOnly(True)
            for content_widget in (
                self.image_label,
                self.point_widget,
                self.lane_plot,
                self.raw_text,
            ):
                content_widget.setSizePolicy(
                    QtWidgets.QSizePolicy.Policy.Ignored,
                    QtWidgets.QSizePolicy.Policy.Ignored,
                )
            self.stack.addWidget(self.image_label)
            self.stack.addWidget(self.point_widget)
            self.stack.addWidget(self.lane_plot)
            self.stack.addWidget(self.raw_text)
            layout.addWidget(self.stack, 1)
            self.frame_status = QtWidgets.QLabel()
            self.frame_status.setMinimumWidth(0)
            self.frame_status.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
            layout.addWidget(self.frame_status)
            self.opacity_effect = QtWidgets.QGraphicsOpacityEffect(self.image_label)
            self.image_label.setGraphicsEffect(self.opacity_effect)
            self.opacity_effect.setOpacity(1.0)
            self.raw_opacity_effect = QtWidgets.QGraphicsOpacityEffect(self.raw_text)
            self.raw_text.setGraphicsEffect(self.raw_opacity_effect)
            self.raw_opacity_effect.setOpacity(1.0)
            self.drop_indicator = QtWidgets.QLabel(self)
            self.drop_indicator.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self.drop_indicator.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.drop_indicator.hide()
            for widget in self.findChildren(QtWidgets.QWidget):
                widget.installEventFilter(self)

            self.render_button.clicked.connect(self._on_render_clicked)
            self.split_button.clicked.connect(lambda: self.owner.split_pane(self, "horizontal"))
            self.xy_button.toggled.connect(self._on_xy_toggled)
            self.max_button.clicked.connect(lambda: self.owner.toggle_maximize_pane(self))
            self.new_window_button.clicked.connect(lambda: self.owner.detach_pane(self))
            self.delete_button.clicked.connect(lambda: self.owner.delete_pane(self))

        def cancel_pending(self) -> None:
            self.owner._io_tasks.cancel(f"preview-{id(self)}")
            self.owner._io_tasks.cancel(f"render-{id(self)}")
            self._preview_loading = False
            self._rendering_playback = False
            self.render_button.setText("Render")

        def _on_render_clicked(self) -> None:
            if self._rendering_playback:
                self.cancel_pending()
                self._failed_render = True
                self.render_button.setText("Render")
                self.frame_status.setText("Render cancelled")
                self.owner._finish_progress("Render cancelled")
                self.owner._pause_playback()
                return
            self._failed_render = False
            self.ensure_rendered_for_playback()

        def set_view_title(self, title: str) -> None:
            self.view_title = title
            self._refresh_title()

        def set_active(self, active: bool) -> None:
            self.setProperty("active", active)
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()

        def apply_theme(self, palette: dict[str, str]) -> None:
            self.image_label.setStyleSheet(
                f"background: {palette['viewer_bg']}; color: {palette['muted']};"
            )
            self.raw_text.setStyleSheet(
                f"background: {palette['input']}; color: {palette['text']};"
            )
            self.lane_plot.apply_theme(palette)
            self.point_renderer.apply_theme(palette)
            for button, symbol in (
                (self.max_button, QtWidgets.QStyle.StandardPixmap.SP_TitleBarMaxButton),
                (self.new_window_button, QtWidgets.QStyle.StandardPixmap.SP_TitleBarNormalButton),
                (self.delete_button, QtWidgets.QStyle.StandardPixmap.SP_TitleBarCloseButton),
                (self.fit_button, QtWidgets.QStyle.StandardPixmap.SP_BrowserReload),
            ):
                button.setIcon(tinted_standard_icon(QtGui, self, symbol, palette))
            self.drop_indicator.setStyleSheet(
                f"background: {palette['panel_alt']}; color: {palette['text']}; border: 2px solid {palette['accent']};"
            )

        def _fit_view(self) -> None:
            if self.is_lane_topic():
                self.lane_plot.reset_view()
            else:
                self.point_renderer.reset_view()

        def clear_topic(self) -> None:
            self.cancel_pending()
            self.topic = None
            self.topic_info = None
            self.point_topics = []
            self._failed_render = False
            self._latest_timestamp_ns = None
            self.rendered_frames.clear()
            self.rendered_timestamps.clear()
            self.rendered_size = None
            self.rendered_window_start_ns = None
            self.rendered_reaches_topic_end = False
            self._lane_plot_data = None
            self._lane_plot_roles = ()
            self._refresh_title()
            self.image_label.clear()
            self.image_label.setText("No topic")
            self.stack.setCurrentWidget(self.image_label)
            self.stack.setVisible(True)
            self.opacity_effect.setOpacity(1.0)
            self.raw_opacity_effect.setOpacity(1.0)
            self.frame_status.clear()
            self.cloud_legend.clear()
            self.cloud_legend.hide()
            self.render_button.setText("Render")
            self.render_button.setEnabled(False)
            self.lane_plot.set_data(None)
            self.lane_plot.set_visible_roles(())
            self.xy_button.setEnabled(False)
            self.set_lane_swap_xy(False)
            self._refresh_view_help()
            self.raw_text.clear()

        def set_topic(self, topic: str, topic_info: TopicInfo) -> None:
            self.clear_topic()
            self.topic = topic
            self.topic_info = topic_info
            self.point_topics = [topic] if topic_info.category == "point_cloud" else []
            self.rendered_frames.clear()
            self.rendered_timestamps.clear()
            self.rendered_size = None
            self.rendered_window_start_ns = None
            self.rendered_reaches_topic_end = False
            self._lane_plot_data = None
            self._lane_plot_roles = ()
            self._refresh_title()
            self.title_label.setToolTip(topic)
            self.raw_text.setPlainText(f"{topic}\n{topic_info.msgtype}\n{topic_info.category}")
            is_lane = self.is_lane_topic()
            self.xy_button.setEnabled(is_lane)
            self.set_lane_swap_xy(self.owner._lane_swap_xy if is_lane else False)
            self._refresh_view_help()
            self.render_button.setEnabled(self.is_image_topic())
            self.render_button.setVisible(self.is_image_topic())
            self.xy_button.setVisible(is_lane)
            self.point_renderer.reset_view()

        def _refresh_title(self) -> None:
            if self.topic is None:
                self.title_label.setText(self.view_title)
                self.title_label.setToolTip("Drop a topic here")
                return
            leaf = self.topic.rsplit("/", 1)[-1]
            category = self.topic_info.category if self.topic_info is not None else "topic"
            self.title_label.setText(f"{self.view_title}: {leaf}")
            if len(self.point_topics) > 1:
                self.title_label.setText(f"{self.view_title}: {len(self.point_topics)} point clouds")
            self.title_label.setToolTip(f"{self.topic}\n{category}")
            if len(self.point_topics) > 1:
                self.title_label.setToolTip("\n".join(self.point_topics))

        def is_image_topic(self) -> bool:
            return _is_image_topic(self.topic_info)

        def is_lane_topic(self) -> bool:
            if self.topic_info is None:
                return False
            return lane_role_for_topic(self.topic_info) is not None

        def set_lane_swap_xy(self, swapped: bool) -> None:
            effective = swapped if self.is_lane_topic() else False
            self.xy_button.blockSignals(True)
            self.xy_button.setChecked(effective)
            self.xy_button.blockSignals(False)
            self.lane_plot.set_swap_xy(effective)

        def _refresh_view_help(self) -> None:
            self.fit_button.setVisible(self.topic_info is not None and self.topic_info.category == "point_cloud")
            if self.is_lane_topic():
                self.view_help_button.setToolTip(LANE_PLOT_HELP_TEXT)
                self.view_help_button.setVisible(True)
                return
            if self.topic_info is not None and self.topic_info.category == "point_cloud":
                self.view_help_button.setToolTip(POINT_CLOUD_HELP_TEXT)
                self.view_help_button.setVisible(True)
                return
            self.view_help_button.setToolTip("")
            self.view_help_button.setVisible(False)

        def _on_xy_toggled(self, checked: bool) -> None:
            self.owner._set_lane_swap_xy(checked)

        def ensure_rendered_for_playback(self) -> bool:
            if self.topic is None or self.topic_info is None:
                self.owner._log("Assign an image topic to this view before rendering.")
                return False
            if self.is_lane_topic():
                self._show_lane_plot_at_timestamp(self.owner._current_timestamp_ns())
                if self.owner._lane_overlay_data is None:
                    self.owner._log("Lane line frames are still loading; the plot will update when ready.")
                return True
            if not self.is_image_topic():
                self.show_at_timestamp(self.owner._current_timestamp_ns())
                return True
            timestamp_ns = self.owner._current_timestamp_ns()
            if timestamp_ns is not None:
                settings = self.owner.settings.topics.get(self.topic, TopicDisplaySettings(self.topic))
                timestamp_ns += round(settings.sync_offset_sec * 1e9)
                self._latest_timestamp_ns = timestamp_ns
            return self._render_playback_window(timestamp_ns)

        def _render_playback_window(self, start_timestamp_ns: int | None = None) -> bool:
            if self._rendering_playback:
                return True
            if self.topic is None or self.owner.session.reader is None:
                return False
            if self.rendered_frames and start_timestamp_ns is not None and not self._timestamp_requires_render(start_timestamp_ns):
                self._display_rendered_frame(start_timestamp_ns)
                return True
            self._rendering_playback = True
            self._failed_render = False
            self.render_button.setText("Cancel")
            self.owner._start_progress(f"Rendering {self.topic}", None)
            topic = self.topic
            reader = self.owner.session.reader
            requested_size = _usable_label_size(self.image_label)
            size = (min(1280, requested_size[0]), min(720, requested_size[1]))

            def to_image(array: Any) -> Any:
                return _array_to_qimage(QtGui, array).scaled(
                    *size, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                    QtCore.Qt.TransformationMode.FastTransformation,
                )

            def work(context: Any) -> Any:
                return load_playback_window(reader, topic, start_timestamp_ns, _decode_record_frame, to_image, context)

            def finish(result: Any) -> None:
                self._rendering_playback = False
                self.render_button.setText("Render")
                self.rendered_frames = result.frames
                self.rendered_timestamps = [frame.timestamp_ns for frame in result.frames]
                self.rendered_size = size
                self.rendered_window_start_ns = start_timestamp_ns
                self.rendered_reaches_topic_end = result.reaches_end
                for message in result.errors:
                    self.owner._log(f"Skipped {topic} frame: {message}")
                if not result.frames:
                    failed(f"No decodable image frames in {topic}")
                    return
                self.owner._finish_progress()
                timestamp_ns = (
                    self._latest_timestamp_ns if self._latest_timestamp_ns is not None
                    else start_timestamp_ns if start_timestamp_ns is not None
                    else self.rendered_timestamps[0]
                )
                # A seek while loading replaces the target, not the in-flight reader.
                if timestamp_ns != start_timestamp_ns and self._timestamp_requires_render(timestamp_ns):
                    self._render_playback_window(timestamp_ns)
                else:
                    self._display_rendered_frame(timestamp_ns)

            def failed(message: str) -> None:
                self._rendering_playback = False
                self._failed_render = True
                self.render_button.setText("Render")
                self.frame_status.setText(message)
                self.frame_status.setToolTip(message)
                self.owner._finish_progress("Render failed")
                self.owner._pause_playback()
                self.owner._log(message)

            self.owner._io_tasks.submit(
                f"render-{id(self)}", work, finish, failed,
                on_progress=self.owner._set_progress,
            )
            return True

        def show_at_timestamp(self, timestamp_ns: int | None) -> None:
            if timestamp_ns is None or self.topic is None or self.topic_info is None:
                return
            settings = replace(self.owner.settings.topics.get(self.topic, TopicDisplaySettings(self.topic)))
            if self.is_lane_topic():
                self.stack.setVisible(True)
                self.opacity_effect.setOpacity(1.0)
                self._show_lane_plot_at_timestamp(timestamp_ns)
                return
            is_cloud_group = bool(self.point_topics) and not self.is_lane_topic()
            visible = any(self.owner.settings.topics.get(name, TopicDisplaySettings(name)).visible for name in self.point_topics) if is_cloud_group else settings.visible
            self.stack.setVisible(visible)
            self.opacity_effect.setOpacity(1.0 if is_cloud_group else settings.opacity)
            self.raw_opacity_effect.setOpacity(settings.opacity)
            if not visible:
                self.frame_status.setText("Hidden")
                return
            timestamp_ns += round(settings.sync_offset_sec * 1e9)
            self._latest_timestamp_ns = timestamp_ns
            if self.is_image_topic():
                if self._rendering_playback:
                    return
                if self.rendered_frames:
                    self._show_rendered_frame(timestamp_ns)
                elif not self._failed_render:
                    self._render_playback_window(timestamp_ns)
                return
            if self._preview_loading or self.owner.preview is None:
                return
            preview = self.owner.preview
            topic = self.topic
            point_topics = list(self.point_topics)
            display_settings = {
                name: replace(self.owner.settings.topics.get(name, TopicDisplaySettings(name)))
                for name in point_topics
            }
            self._preview_loading = True

            def work(context: Any) -> Any:
                if point_topics:
                    clouds = []
                    for name in point_topics:
                        context.throw_if_cancelled()
                        display = display_settings[name]
                        if not display.visible:
                            continue
                        target = timestamp_ns - round(settings.sync_offset_sec * 1e9) + round(display.sync_offset_sec * 1e9)
                        cloud = preview.point_cloud_preview(
                            name, target, max_points=max(100, 20_000 // display.decimation), cancellation_token=context,
                        )
                        if cloud is not None:
                            clouds.append((cloud, display))
                    return clouds
                context.throw_if_cancelled()
                record = preview.nearest_record(topic, timestamp_ns, cancellation_token=context)
                return record

            def finish(result: Any) -> None:
                self._preview_loading = False
                self._last_preview_error = None
                if point_topics:
                    self.point_renderer.set_clouds(result)
                    leaf_names = [cloud.topic.rsplit("/", 1)[-1] for cloud, _display in result]
                    self.cloud_legend.setText(" &nbsp; ".join(
                        f'<span style="color:{display.color}">&#9679;</span> {escape(cloud.topic if leaf_names.count(leaf) > 1 else leaf)}'
                        for (cloud, display), leaf in zip(result, leaf_names)
                    ))
                    self.cloud_legend.setToolTip("\n".join(cloud.topic for cloud, _display in result))
                    self.cloud_legend.show()
                    count = sum(cloud.preview_point_count for cloud, _settings in result)
                    self.frame_status.setText(f"{len(result)} topic(s) | {count:,} points | {timestamp_ns} ns")
                    self.stack.setCurrentWidget(self.point_widget)
                else:
                    from rosbagel.core.decoder import summarize_message
                    summary = summarize_message(result.decoded, result.raw) if result is not None else {}
                    self.raw_text.setPlainText(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
                    self.stack.setCurrentWidget(self.raw_text)
                    self.frame_status.setText(f"{result.timestamp_ns} ns" if result is not None else "No messages")
                self.frame_status.setToolTip(self.frame_status.text())
                if self._latest_timestamp_ns != timestamp_ns:
                    target = self._latest_timestamp_ns - round(settings.sync_offset_sec * 1e9)
                    self.show_at_timestamp(target)

            def failed(message: str) -> None:
                self._preview_loading = False
                self.frame_status.setText(f"Preview failed: {message}")
                self.frame_status.setToolTip(message)
                if message != self._last_preview_error:
                    self.owner._log(f"Preview error for {topic}: {message}")
                    self._last_preview_error = message

            self.owner._io_tasks.submit(f"preview-{id(self)}", work, finish, failed)

        def _show_rendered_frame(self, timestamp_ns: int) -> None:
            if self._timestamp_requires_render(timestamp_ns):
                if not self._failed_render:
                    self._render_playback_window(timestamp_ns)
                return
            self._display_rendered_frame(timestamp_ns)

        def _timestamp_requires_render(self, timestamp_ns: int) -> bool:
            if not self.rendered_timestamps:
                return True
            if timestamp_ns < self.rendered_timestamps[0]:
                return self.rendered_window_start_ns is not None and timestamp_ns < self.rendered_window_start_ns
            if timestamp_ns > self.rendered_timestamps[-1]:
                return not self.rendered_reaches_topic_end
            return False

        def _display_rendered_frame(self, timestamp_ns: int) -> None:
            if not self.rendered_timestamps:
                return
            index = bisect_left(self.rendered_timestamps, timestamp_ns)
            if index <= 0:
                selected = 0
            elif index >= len(self.rendered_timestamps):
                selected = len(self.rendered_timestamps) - 1
            else:
                before = self.rendered_timestamps[index - 1]
                after = self.rendered_timestamps[index]
                selected = index - 1 if abs(before - timestamp_ns) <= abs(after - timestamp_ns) else index
            frame = self.rendered_frames[selected]
            pixmap = QtGui.QPixmap.fromImage(frame.image)
            self.image_label.setPixmap(pixmap.scaled(
                *_usable_label_size(self.image_label), QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            ))
            self.stack.setCurrentWidget(self.image_label)
            self.frame_status.setText(f"{frame.width} x {frame.height} | {frame.encoding} | {frame.timestamp_ns} ns")
            self.frame_status.setToolTip(self.frame_status.text())

        def resizeEvent(self, event: Any) -> None:
            super().resizeEvent(event)
            if self.rendered_frames and self._latest_timestamp_ns is not None:
                QtCore.QTimer.singleShot(0, self, lambda: self._display_rendered_frame(self._latest_timestamp_ns))

        def _show_lane_plot_at_timestamp(self, timestamp_ns: int | None) -> None:
            self.lane_plot.set_display_settings(self.owner._lane_display_settings())
            data = self.owner._lane_overlay_data
            roles = tuple(self.owner._lane_roles_for_view(self.topic_info))
            if data is None:
                self.lane_plot.set_empty_text("Loading lane line PointCloud2 frames...")
                if self._lane_plot_data is not None:
                    self.lane_plot.set_data(None)
                    self._lane_plot_data = None
                if self._lane_plot_roles != roles:
                    self.lane_plot.set_visible_roles(roles)
                    self._lane_plot_roles = roles
                self.lane_plot.show_at_timestamp(timestamp_ns)
                self.stack.setCurrentWidget(self.lane_plot)
                return
            self.lane_plot.set_empty_text("No lane line frames were loaded.")
            if self._lane_plot_data is not data:
                self.lane_plot.set_data(data)
                self._lane_plot_data = data
                self._lane_plot_roles = ()
            if self._lane_plot_roles != roles:
                self.lane_plot.set_visible_roles(roles)
                self._lane_plot_roles = roles
            self.lane_plot.show_at_timestamp(timestamp_ns)
            self.stack.setCurrentWidget(self.lane_plot)

        def contextMenuEvent(self, event: Any) -> None:
            menu = QtWidgets.QMenu(self)
            inspect = menu.addAction("Inspect message / plot fields")
            inspect.setEnabled(self.topic is not None)
            snapshot = menu.addAction("Save view image...")
            menu.addSeparator()
            split_horizontal = menu.addAction("Split horizontally")
            split_vertical = menu.addAction("Split vertically")
            menu.addSeparator()
            maximize = menu.addAction("Maximize / restore")
            duplicate = menu.addAction("Duplicate window")
            delete = menu.addAction("Delete view")
            action = menu.exec(event.globalPos())
            if action == snapshot:
                path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save view image", "view.png", "PNG image (*.png)")
                if path and not self.grab().save(path, "PNG"):
                    self.owner._show_warning(f"Could not save {path}")
            elif action == inspect:
                workflows = self.owner.workflows
                workflows.inspector.topic_box.setCurrentText(self.topic)
                workflows.show_dock(workflows.inspector_dock)
                workflows.inspector.reload()
            elif action == split_horizontal:
                self.owner.split_pane(self, "horizontal")
            elif action == split_vertical:
                self.owner.split_pane(self, "vertical")
            elif action == maximize:
                self.owner.toggle_maximize_pane(self)
            elif action == duplicate:
                self.owner.duplicate_pane_window(self)
            elif action == delete:
                self.owner.delete_pane(self)

        def _drop_edge(self, position: Any) -> str:
            x, y = position.x() / max(1, self.width()), position.y() / max(1, self.height())
            distances = {"left": x, "right": 1 - x, "top": y, "bottom": 1 - y}
            edge = min(distances, key=distances.get)
            return edge if distances[edge] < 0.25 else "center"

        def _accepts_mime(self, mime: Any) -> bool:
            pane = self.owner._pane_from_mime(mime)
            if pane is not None:
                return pane is not self and self in self.owner._panes
            return any(topic in self.owner._topic_info_by_name for topic in self.owner._topics_from_mime(mime))

        def dragEnterEvent(self, event: Any) -> None:
            if self._accepts_mime(event.mimeData()):
                event.acceptProposedAction()
            else:
                event.ignore()

        def dragMoveEvent(self, event: Any) -> None:
            if not self._accepts_mime(event.mimeData()):
                event.ignore()
                return
            edge = self._drop_edge(event.position())
            if edge == "center" and self.owner._pane_from_mime(event.mimeData()):
                edge = "right"
            rect = self.rect().adjusted(4, 4, -4, -4)
            if edge == "left":
                rect.setWidth(rect.width() // 2)
            elif edge == "right":
                rect.setLeft(rect.center().x())
            elif edge == "top":
                rect.setHeight(rect.height() // 2)
            elif edge == "bottom":
                rect.setTop(rect.center().y())
            self.drop_indicator.setGeometry(rect)
            self.drop_indicator.setText("Replace topics" if edge == "center" else f"Split {edge}")
            self.drop_indicator.show()
            self.drop_indicator.raise_()
            event.acceptProposedAction()

        def dragLeaveEvent(self, event: Any) -> None:
            self.drop_indicator.hide()
            event.accept()

        def dropEvent(self, event: Any) -> None:
            self.drop_indicator.hide()
            edge = self._drop_edge(event.position())
            pane = self.owner._pane_from_mime(event.mimeData())
            if pane is not None:
                if pane is self or self not in self.owner._panes:
                    event.ignore()
                    return
                self.owner.move_pane(pane, self, "right" if edge == "center" else edge)
                event.acceptProposedAction()
                return
            topics = [topic for topic in self.owner._topics_from_mime(event.mimeData())
                      if topic in self.owner._topic_info_by_name]
            if not topics:
                event.ignore()
                return
            target = self
            if edge != "center" and self.topic is not None:
                target = self.owner.split_pane(self, "vertical" if edge in {"top", "bottom"} else "horizontal")
                if target is None:
                    event.ignore()
                    return
                if edge in {"left", "top"}:
                    self.owner.move_pane(target, self, edge)
            self.owner.assign_topics_to_pane(target, topics)
            event.acceptProposedAction()

        def eventFilter(self, watched: Any, event: Any) -> bool:
            event_type = event.type()
            if self._drag_in_progress and event_type == QtCore.QEvent.Type.KeyPress and event.key() == QtCore.Qt.Key.Key_Escape:
                self._drag_cancelled = True
            if event_type == QtCore.QEvent.Type.MouseButtonPress:
                self.owner._set_active_pane(self)
                if watched is self.title_label and event.button() == QtCore.Qt.MouseButton.LeftButton:
                    self._drag_start = event.position().toPoint()
            elif watched is self.title_label and event_type == QtCore.QEvent.Type.MouseButtonRelease:
                self._drag_start = None
            elif watched is self.title_label and event_type == QtCore.QEvent.Type.MouseMove and self._drag_start is not None:
                if not event.buttons() & QtCore.Qt.MouseButton.LeftButton:
                    self._drag_start = None
                    return False
                if (event.position().toPoint() - self._drag_start).manhattanLength() < QtWidgets.QApplication.startDragDistance():
                    return False
                self._drag_start = None
                mime = QtCore.QMimeData()
                mime.setData(VIEW_MIME, str(id(self)).encode("ascii"))
                drag = QtGui.QDrag(self.title_label)
                drag.setMimeData(mime)
                self._drag_cancelled = False
                self._drag_in_progress = True
                app = QtWidgets.QApplication.instance()
                app.installEventFilter(self)
                try:
                    action = drag.exec(QtCore.Qt.DropAction.MoveAction)
                finally:
                    self._drag_in_progress = False
                    app.removeEventFilter(self)
                if action == QtCore.Qt.DropAction.IgnoreAction and not self._drag_cancelled:
                    position = QtGui.QCursor.pos()
                    inside = any(window.frameGeometry().contains(position) for window in
                                 [self.owner.window, *[pane.window() for pane in self.owner._window_panes]])
                    if not inside:
                        self.owner.detach_pane(self)
                return True
            return super().eventFilter(watched, event)

        def mousePressEvent(self, event: Any) -> None:
            self.owner._set_active_pane(self)
            super().mousePressEvent(event)

    return TopicViewPane


def _create_drop_window(QtWidgets: Any, QtCore: Any, open_callback: Any, close_callback: Any) -> Any:
    class DropWindow(QtWidgets.QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.setAcceptDrops(True)

        def closeEvent(self, event: Any) -> None:
            close_callback()
            super().closeEvent(event)

        def dragEnterEvent(self, event: Any) -> None:
            if event.mimeData().hasUrls():
                event.acceptProposedAction()
            else:
                event.ignore()

        def dropEvent(self, event: Any) -> None:
            urls = event.mimeData().urls()
            for url in urls:
                if not url.isLocalFile():
                    continue
                open_callback(Path(url.toLocalFile()))
                event.acceptProposedAction()
                return
            event.ignore()

    return DropWindow()


def _require_pyside6() -> tuple[Any, Any, Any]:
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except Exception as exc:
        raise RuntimeError(
            "PySide6 is required for the GUI. Install with: py -m pip install -e .[gui]"
        ) from exc
    return QtCore, QtGui, QtWidgets


def _decode_record_frame(record: object) -> Any:
    decoded = getattr(record, "decoded", None)
    if decoded is None:
        raise ValueError("message was not decoded")
    msgtype = str(getattr(record, "msgtype", ""))
    if msgtype == "sensor_msgs/msg/Image":
        return decode_sensor_image(decoded)
    if msgtype == "sensor_msgs/msg/CompressedImage":
        return decode_compressed_image(decoded)
    raise ValueError(f"topic type {msgtype} is not an image type")




def _array_to_qimage(QtGui: Any, array: Any) -> Any:
    import cv2
    import numpy as np

    image = np.ascontiguousarray(array)
    if image.dtype != np.uint8:
        image = cv2.normalize(image, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    if image.ndim == 2:
        height, width = image.shape
        qimage = QtGui.QImage(image.data, width, height, width, QtGui.QImage.Format.Format_Grayscale8)
    elif image.ndim == 3 and image.shape[2] == 3:
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        height, width = rgb.shape[:2]
        qimage = QtGui.QImage(rgb.data, width, height, width * 3, QtGui.QImage.Format.Format_RGB888)
    elif image.ndim == 3 and image.shape[2] == 4:
        rgba = cv2.cvtColor(image, cv2.COLOR_BGRA2RGBA)
        height, width = rgba.shape[:2]
        qimage = QtGui.QImage(rgba.data, width, height, width * 4, QtGui.QImage.Format.Format_RGBA8888)
    else:
        raise ValueError(f"Unsupported image shape: {image.shape}")
    return qimage.copy()


def _usable_label_size(label: Any) -> tuple[int, int]:
    width = max(64, int(label.width() or 640))
    height = max(64, int(label.height() or 480))
    return width, height


def _is_image_topic(topic: TopicInfo | None) -> bool:
    if topic is None:
        return False
    return topic.msgtype in {"sensor_msgs/msg/Image", "sensor_msgs/msg/CompressedImage"} or topic.category in IMAGE_CATEGORIES


