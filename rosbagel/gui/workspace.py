from __future__ import annotations

import base64
import json
import math
from pathlib import Path
import re
from typing import Any

from rosbagel.core.preview import PreviewSessionSettings


def workspace_snapshot(viewer: Any) -> dict[str, Any]:
    def pane_data(pane):
        return {"topics": list(pane.point_topics) or ([pane.topic] if pane.topic else [])}

    def layout_node(widget):
        if isinstance(widget, viewer.QtWidgets.QSplitter):
            return {"split": "horizontal" if widget.orientation() == viewer.QtCore.Qt.Orientation.Horizontal else "vertical",
                    "sizes": widget.sizes(), "children": [layout_node(widget.widget(i)) for i in range(widget.count())]}
        return pane_data(widget)

    return {
        "format": "rosbagel-workspace", "version": 1,
        "bag_path": str(viewer.session.bag_path.resolve()), "backend": viewer.session.backend,
        "settings": viewer.settings.to_dict(), "theme": viewer._theme,
        "time_sec": viewer.time_input.value(), "rate": viewer._playback_rate,
        "checked_topics": viewer.topic_tree._checked_topics(), "lane_swap_xy": viewer._lane_swap_xy,
        "layout": layout_node(viewer.view_layout.layout.itemAt(0).widget()),
        "windows": [pane_data(pane) for pane in viewer._window_panes],
        "chart_fields": [list(pair) for pair in viewer.workflows.chart.selections],
        "inspector_topic": viewer.workflows.inspector.topic_box.currentText(),
        "dock_state": base64.b64encode(bytes(viewer.window.saveState())).decode("ascii"),
    }


def validate_workspace(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("format") != "rosbagel-workspace" or data.get("version") != 1:
        raise ValueError("Not a supported ROSBagel workspace (version 1)")
    if not isinstance(data.get("bag_path"), str) or not data["bag_path"]:
        raise ValueError("Workspace has no bag path")
    if data.get("backend", "auto") not in {"auto", "rosbags", "sqlite"}:
        raise ValueError("Workspace has an invalid bag backend")
    if data.get("theme", "dark") not in {"dark", "light"}:
        raise ValueError("Workspace has an invalid theme")
    if not isinstance(data.get("inspector_topic", ""), str) or not isinstance(data.get("lane_swap_xy", False), bool):
        raise ValueError("Invalid inspector topic or lane axes")
    for key, minimum, maximum in (("time_sec", 0, 1e12), ("rate", .01, 100)):
        value = data.get(key, 0 if key == "time_sec" else 1)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(f"Workspace {key} is invalid")
    count = 0

    def check_node(node, depth=0):
        nonlocal count
        if not isinstance(node, dict) or depth > 16:
            raise ValueError("Invalid workspace view layout")
        if "children" in node:
            children = node["children"]
            sizes = node.get("sizes", [])
            if node.get("split") not in {"horizontal", "vertical"} or not isinstance(children, list) or not 2 <= len(children) <= 16:
                raise ValueError("Invalid workspace split")
            if not isinstance(sizes, list) or len(sizes) != len(children) or any(not isinstance(size, int) or not 0 <= size <= 100000 for size in sizes):
                raise ValueError("Invalid splitter sizes")
            for child in children:
                check_node(child, depth + 1)
            return
        topics = node.get("topics")
        if not isinstance(topics, list) or len(topics) > 1000 or any(not isinstance(topic, str) for topic in topics):
            raise ValueError("Invalid view topics")
        count += 1
        if count > 16:
            raise ValueError("Workspace exceeds the 16-view limit")

    check_node(data.get("layout"))
    windows = data.get("windows", [])
    if not isinstance(windows, list):
        raise ValueError("Invalid detached views")
    for window in windows:
        if not isinstance(window, dict) or "children" in window:
            raise ValueError("Invalid detached view")
        check_node(window)
    chart_fields = data.get("chart_fields", [])
    if not isinstance(chart_fields, list) or len(chart_fields) > 16 or any(not isinstance(pair, list) or len(pair) != 2 or not all(isinstance(value, str) for value in pair) for pair in chart_fields):
        raise ValueError("Invalid chart field selections")
    if not isinstance(data.get("checked_topics", []), list) or any(not isinstance(name, str) for name in data.get("checked_topics", [])):
        raise ValueError("Invalid checked topics")
    settings = data.get("settings", {})
    try:
        parsed = PreviewSessionSettings.from_dict(settings)
        for name, topic in parsed.topics.items():
            if not isinstance(name, str) or topic.topic != name or not isinstance(topic.visible, bool):
                raise ValueError("Invalid topic settings")
            if not isinstance(topic.color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", topic.color):
                raise ValueError(f"Invalid color for {name}")
            for value, low, high in ((topic.opacity, 0, 1), (topic.point_size, .1, 20), (topic.decimation, 1, 1000), (topic.sync_offset_sec, -3600, 3600)):
                if not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                    raise ValueError(f"Invalid display value for {name}")
            if not isinstance(topic.decimation, int):
                raise ValueError(f"Invalid decimation for {name}")
        state = data.get("dock_state", "")
        if not isinstance(state, str) or len(state) > 1000000:
            raise ValueError("Invalid dock state")
        base64.b64decode(state, validate=True)
    except (TypeError, AttributeError, KeyError, IndexError, ValueError) as exc:
        raise ValueError(f"Invalid workspace settings: {exc}") from exc
    return data


def restore_workspace(viewer: Any, data: dict[str, Any]) -> None:
    validate_workspace(data)
    available = viewer._topic_info_by_name
    saved = PreviewSessionSettings.from_dict(data.get("settings", {}))
    viewer.settings.topics.update({key: value for key, value in saved.topics.items() if key in available})
    viewer._pause_playback()
    viewer._maximized_pane = None
    viewer._set_theme(data.get("theme", "dark"))
    for pane in viewer._all_panes():
        pane.cancel_pending()
    for pane in list(viewer._window_panes):
        pane.window().close()
    old_root = viewer.view_layout.layout.takeAt(0).widget()
    old_root.setParent(None)
    old_root.deleteLater()
    viewer._panes.clear()
    viewer._active_pane = None
    viewer._next_view_id = 1
    missing = set()

    def make_pane(node):
        pane = viewer._new_pane()
        topics = [topic for topic in node["topics"] if topic in available]
        missing.update(set(node["topics"]) - set(topics))
        if topics:
            pane.set_topic(topics[0], available[topics[0]])
            if available[topics[0]].category == "point_cloud":
                pane.point_topics = [topic for topic in topics if available[topic].category == "point_cloud"]
            pane._refresh_title()
        return pane

    def build(node):
        if "children" not in node:
            pane = make_pane(node)
            viewer._panes.append(pane)
            return pane
        splitter = viewer.QtWidgets.QSplitter(viewer.QtCore.Qt.Orientation.Horizontal if node["split"] == "horizontal" else viewer.QtCore.Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)
        for child in node["children"]:
            splitter.addWidget(build(child))
        splitter.setSizes(node["sizes"])
        return splitter

    viewer.view_layout.add(build(data["layout"]))
    for node in data.get("windows", []):
        viewer._show_pane_window(make_pane(node))
    viewer._set_checked_topics([topic for topic in data.get("checked_topics", []) if topic in available])
    viewer._set_lane_swap_xy(bool(data.get("lane_swap_xy", False)))
    viewer._set_active_pane(viewer._panes[0])
    viewer.rate_box.setCurrentText(f"{data.get('rate', 1):g}x")
    viewer._playback_rate = data.get("rate", 1.)
    chart = viewer.workflows.chart
    chart.selections = [tuple(pair) for pair in data.get("chart_fields", []) if pair[0] in available]
    chart.refresh_series()
    chart.load()
    viewer.workflows.inspector.topic_box.setCurrentText(data.get("inspector_topic", ""))
    if data.get("dock_state"):
        if not viewer.window.restoreState(viewer.QtCore.QByteArray(base64.b64decode(data["dock_state"]))):
            viewer._log("Saved dock placement could not be restored; views were loaded.")
    viewer._layout_panes()
    viewer._set_timeline_seconds(data.get("time_sec", 0), immediate=True)
    if missing:
        viewer._log("Workspace topics missing from bag: " + ", ".join(sorted(missing)))


def save_workspace_dialog(viewer: Any) -> None:
    if viewer.session.reader is None:
        return
    path, _ = viewer.QtWidgets.QFileDialog.getSaveFileName(viewer.window, "Save workspace", "session.bagel.json", "ROSBagel workspace (*.bagel.json)")
    if not path:
        return
    try:
        data = workspace_snapshot(viewer)
        file = viewer.QtCore.QSaveFile(path)
        if not file.open(viewer.QtCore.QIODevice.OpenModeFlag.WriteOnly):
            raise OSError(file.errorString())
        payload = (json.dumps(data, indent=2) + "\n").encode("utf-8")
        if file.write(payload) != len(payload) or not file.commit():
            raise OSError(file.errorString())
        viewer._log(f"Saved workspace to {path}")
    except (OSError, ValueError) as exc:
        viewer._show_warning(f"Could not save workspace: {exc}")


def load_workspace_dialog(viewer: Any) -> None:
    if viewer._opening or viewer._exporting:
        viewer._show_warning("Wait for the current open or export to finish.")
        return
    path, _ = viewer.QtWidgets.QFileDialog.getOpenFileName(viewer.window, "Open workspace", "", "ROSBagel workspace (*.bagel.json);;JSON (*.json)")
    if not path:
        return
    try:
        file = Path(path)
        if file.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("Workspace file exceeds 4 MiB")
        data = validate_workspace(json.loads(file.read_text(encoding="utf-8")))
        bag = Path(data["bag_path"])
        if not bag.is_absolute():
            bag = file.parent / bag
        if not bag.exists():
            raise ValueError(f"Workspace bag does not exist: {bag}")
        if viewer.session.bag_path and bag.resolve() == viewer.session.bag_path.resolve() and viewer.session.backend == data.get("backend", "auto"):
            restore_workspace(viewer, data)
        else:
            viewer.workflows.pending_workspace = data
            viewer.open_bag(bag, backend=data.get("backend", "auto"))
    except (OSError, ValueError, TypeError) as exc:
        viewer._show_warning(f"Could not open workspace: {exc}")
