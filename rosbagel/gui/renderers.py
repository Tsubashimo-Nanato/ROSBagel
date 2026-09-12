from __future__ import annotations

from typing import Any, Protocol

from rosbagel.gui.point_plot import create_point_plot


POINT_CLOUD_HELP_TEXT = (
    "3D: left-drag to rotate; Shift-drag to pan\n"
    "2D: middle-drag to pan\n"
    "Wheel: zoom\n"
    "Fit: restore automatic point bounds"
)
POINT_CLOUD_FIT_MARGIN_RATIO = 0.04


class PointCloudRenderer(Protocol):
    def widget(self) -> Any: ...
    def set_clouds(self, clouds: list[Any]) -> None: ...
    def reset_view(self) -> None: ...
    def apply_theme(self, palette: dict[str, str]) -> None: ...


class VisPyPointCloudRenderer:
    """Optional 3D renderer; scene data stays separate from Qt controls."""

    def __init__(self) -> None:
        import numpy as np
        from vispy import scene

        self._np = np
        self._scene = scene
        self._canvas = scene.SceneCanvas(app="pyside6", keys=None, bgcolor="#101010")
        self._view = self._canvas.central_widget.add_view()
        self._view.camera = "turntable"
        scene.visuals.XYZAxis(parent=self._view.scene)
        self._scatter_by_topic: dict[str, Any] = {}
        self._points: Any = np.empty((0, 3))
        self._auto_fit = True
        self._canvas.native.setToolTip(POINT_CLOUD_HELP_TEXT)
        self._canvas.events.mouse_press.connect(self._on_camera_interaction)
        self._canvas.events.mouse_wheel.connect(self._on_camera_interaction)

    def widget(self) -> Any:
        return self._canvas.native

    def _on_camera_interaction(self, _event: Any) -> None:
        self._auto_fit = False

    def apply_theme(self, palette: dict[str, str]) -> None:
        self._canvas.bgcolor = palette["viewer_bg"]

    def reset_view(self) -> None:
        self._auto_fit = True
        self._fit_camera(self._points)

    def set_clouds(self, clouds: list[Any]) -> None:
        visible = {cloud.topic for cloud, _settings in clouds}
        for topic, scatter in self._scatter_by_topic.items():
            scatter.visible = topic in visible
        arrays = []
        for cloud, settings in clouds:
            points = self._np.asarray(cloud.points, dtype=self._np.float32).reshape(-1, 3)
            points = points[self._np.isfinite(points).all(axis=1)]
            scatter = self._scatter_by_topic.get(cloud.topic)
            if scatter is None:
                scatter = self._scene.visuals.Markers(parent=self._view.scene)
                self._scatter_by_topic[cloud.topic] = scatter
            scatter.visible = bool(len(points))
            if not len(points):
                continue
            from vispy.color import Color
            color = Color(settings.color, alpha=settings.opacity)
            scatter.set_data(points, face_color=color.rgba, edge_width=0, size=settings.point_size)
            arrays.append(points)
        for topic in list(self._scatter_by_topic):
            if topic not in visible:
                self._scatter_by_topic.pop(topic).parent = None
        self._points = self._np.concatenate(arrays) if arrays else self._np.empty((0, 3))
        if self._auto_fit:
            self._fit_camera(self._points)
        self._canvas.update()

    def _fit_camera(self, points: Any) -> None:
        if points.size == 0:
            return
        mins, maxs = points.min(axis=0), points.max(axis=0)
        margin = max(float((maxs - mins).max()), 1.0) * POINT_CLOUD_FIT_MARGIN_RATIO
        self._view.camera.set_range(
            x=(float(mins[0] - margin), float(maxs[0] + margin)),
            y=(float(mins[1] - margin), float(maxs[1] + margin)),
            z=(float(mins[2] - margin), float(maxs[2] + margin)),
            margin=0.02,
        )


def create_point_cloud_renderer(QtWidgets: Any) -> PointCloudRenderer:
    app = QtWidgets.QApplication.instance()
    # The offscreen Qt platform has no native OpenGL surface.
    if app is not None and app.platformName() in {"offscreen", "minimal"}:
        return create_point_plot(QtWidgets)
    try:
        return VisPyPointCloudRenderer()
    except (ImportError, RuntimeError) as exc:
        plot = create_point_plot(QtWidgets)
        plot.setToolTip(f"{plot.toolTip()}\n3D unavailable: {exc}")
        return plot
