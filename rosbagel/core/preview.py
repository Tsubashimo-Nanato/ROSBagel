from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from collections.abc import Iterator
from pathlib import Path
from typing import Any
import json
import math

from rosbagel.core.decoder import (
    ImageFrame,
    decode_compressed_image,
    decode_sensor_image,
    flatten_message,
    summarize_message,
)
from rosbagel.core.jobs import CancellationToken
from rosbagel.core.models import MessageRecord
from rosbagel.core.point_cloud import (
    expanded_point_field_names,
    expanded_point_row,
    iter_point_cloud_rows,
    point_cloud_point_count,
)
from rosbagel.core.session import Session


@dataclass(slots=True)
class PreviewFrame:
    topic: str
    timestamp_ns: int
    width: int
    height: int
    encoding: str
    image: Any
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PointCloudPreview:
    topic: str
    timestamp_ns: int
    fields: list[str]
    points: Any
    color_values: Any | None
    original_point_count: int
    preview_point_count: int
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ScalarSeriesPreview:
    topic: str
    field: str
    timestamps_ns: list[int]
    values: list[float]


@dataclass(slots=True)
class _TopicCursor:
    iterator: Iterator[MessageRecord]
    before: MessageRecord | None = None
    after: MessageRecord | None = None
    exhausted: bool = False


@dataclass(slots=True)
class TopicDisplaySettings:
    topic: str
    visible: bool = True
    color: str = "#ff9f1c"
    opacity: float = 1.0
    point_size: float = 2.0
    decimation: int = 4
    sync_offset_sec: float = 0.0
    export_format: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TopicDisplaySettings":
        return cls(**data)


@dataclass(slots=True)
class PreviewSessionSettings:
    bag_path: str | None = None
    time_range_sec: tuple[float | None, float | None] = (None, None)
    topics: dict[str, TopicDisplaySettings] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bag_path": self.bag_path,
            "time_range_sec": list(self.time_range_sec),
            "topics": {
                topic: settings.to_dict()
                for topic, settings in sorted(self.topics.items())
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PreviewSessionSettings":
        raw_range = data.get("time_range_sec") or [None, None]
        return cls(
            bag_path=data.get("bag_path"),
            time_range_sec=(raw_range[0], raw_range[1]),
            topics={
                topic: TopicDisplaySettings.from_dict(settings)
                for topic, settings in (data.get("topics") or {}).items()
            },
        )


class PreviewService:
    """Lazy preview facade for GUI callers.

    The service reads only the selected topic when resolving a preview. It does
    not scan or decode the full bag on open.
    """

    def __init__(self, session: Session, *, cache_size: int = 16) -> None:
        self.session = session
        self.cache_size = cache_size
        self._record_cache: OrderedDict[tuple[str, int], MessageRecord | None] = OrderedDict()
        self._cursor_by_topic: dict[str, _TopicCursor] = {}

    def clear_cache(self) -> None:
        self._record_cache.clear()
        self._cursor_by_topic.clear()

    def nearest_record(
        self,
        topic: str,
        timestamp_ns: int,
        *,
        cancellation_token: CancellationToken | None = None,
    ) -> MessageRecord | None:
        resolved = self.session._resolve_topic_name(topic)
        cache_key = (resolved, timestamp_ns)
        if cache_key in self._record_cache:
            self._record_cache.move_to_end(cache_key)
            return self._record_cache[cache_key]

        reader = self.session._require_reader()
        cursor = self._cursor_by_topic.get(resolved)
        if cursor is None or _cursor_is_behind_request(cursor, timestamp_ns):
            cursor = _TopicCursor(iterator=iter(reader.iter_messages(topics=[resolved])))
            self._cursor_by_topic[resolved] = cursor
        best = _nearest_from_cursor(
            cursor,
            timestamp_ns,
            cancellation_token=cancellation_token,
        )
        self._record_cache[cache_key] = best
        while len(self._record_cache) > self.cache_size:
            self._record_cache.popitem(last=False)
        return best

    def image_preview(self, topic: str, timestamp_ns: int) -> PreviewFrame | None:
        record = self.nearest_record(topic, timestamp_ns)
        if record is None or record.decoded is None:
            return None
        frame = _decode_image_record(record)
        return PreviewFrame(
            topic=record.topic,
            timestamp_ns=record.timestamp_ns,
            width=frame.width,
            height=frame.height,
            encoding=frame.encoding,
            image=frame.array,
            warnings=frame.warnings,
        )

    def point_cloud_preview(
        self,
        topic: str,
        timestamp_ns: int,
        *,
        max_points: int = 20_000,
        cancellation_token: CancellationToken | None = None,
    ) -> PointCloudPreview | None:
        import numpy as np

        record = self.nearest_record(topic, timestamp_ns, cancellation_token=cancellation_token)
        if record is None or record.decoded is None:
            return None
        fields = expanded_point_field_names(record.decoded)
        original_count = point_cloud_point_count(record.decoded)
        stride = max(1, math.ceil(original_count / max_points)) if max_points > 0 else 1
        xyz: list[list[float]] = []
        color_values: list[float] = []
        for index, row in enumerate(iter_point_cloud_rows(record.decoded)):
            if cancellation_token is not None:
                cancellation_token.throw_if_cancelled()
            if index % stride != 0:
                continue
            values = dict(zip(fields, expanded_point_row(record.decoded, row)))
            point = [
                float(values.get("x", 0.0) or 0.0),
                float(values.get("y", 0.0) or 0.0),
                float(values.get("z", 0.0) or 0.0),
            ]
            if not all(math.isfinite(value) for value in point):
                continue
            xyz.append(point)
            color_source = values.get("intensity", values.get("rgb", values.get("rgba")))
            if isinstance(color_source, (int, float)):
                color_values.append(float(color_source))
        return PointCloudPreview(
            topic=record.topic,
            timestamp_ns=record.timestamp_ns,
            fields=fields,
            points=np.asarray(xyz, dtype=np.float32).reshape(-1, 3),
            color_values=np.asarray(color_values, dtype=np.float32) if color_values else None,
            original_point_count=original_count,
            preview_point_count=len(xyz),
        )

    def summary_preview(self, topic: str, timestamp_ns: int) -> dict[str, Any]:
        record = self.nearest_record(topic, timestamp_ns)
        if record is None:
            return {}
        return summarize_message(record.decoded, record.raw)

    def scalar_series(
        self,
        topic: str,
        field: str,
        *,
        max_messages: int = 10_000,
        cancellation_token: CancellationToken | None = None,
    ) -> ScalarSeriesPreview:
        resolved = self.session._resolve_topic_name(topic)
        reader = self.session._require_reader()
        timestamps: list[int] = []
        values: list[float] = []
        for record in reader.iter_messages(topics=[resolved]):
            if cancellation_token is not None:
                cancellation_token.throw_if_cancelled()
            if record.decoded is None:
                continue
            flattened = flatten_message(record.decoded)
            value = flattened.get(field)
            if isinstance(value, (int, float, bool)):
                timestamps.append(record.timestamp_ns)
                values.append(float(value))
            if len(timestamps) >= max_messages:
                break
        return ScalarSeriesPreview(
            topic=resolved,
            field=field,
            timestamps_ns=timestamps,
            values=values,
        )


def load_preview_settings(path: str | Path) -> PreviewSessionSettings:
    with Path(path).open("r", encoding="utf-8") as handle:
        return PreviewSessionSettings.from_dict(json.load(handle))


def save_preview_settings(settings: PreviewSessionSettings, path: str | Path) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(settings.to_dict(), handle, indent=2, sort_keys=True)
        handle.write("\n")
    return output_path


def _decode_image_record(record: MessageRecord) -> ImageFrame:
    if record.msgtype == "sensor_msgs/msg/Image":
        return decode_sensor_image(record.decoded)
    if record.msgtype == "sensor_msgs/msg/CompressedImage":
        return decode_compressed_image(record.decoded)
    raise ValueError(f"{record.msgtype} is not an image type")


def _cursor_is_behind_request(cursor: _TopicCursor, timestamp_ns: int) -> bool:
    if cursor.before is None:
        return False
    if cursor.after is not None:
        return timestamp_ns < cursor.before.timestamp_ns
    return timestamp_ns < cursor.before.timestamp_ns


def _nearest_from_cursor(
    cursor: _TopicCursor,
    timestamp_ns: int,
    *,
    cancellation_token: CancellationToken | None,
) -> MessageRecord | None:
    while not cursor.exhausted and (
        cursor.after is None or cursor.after.timestamp_ns < timestamp_ns
    ):
        if cancellation_token is not None:
            cancellation_token.throw_if_cancelled()
        if cursor.after is not None:
            cursor.before = cursor.after
        try:
            cursor.after = next(cursor.iterator)
        except StopIteration:
            cursor.exhausted = True
            cursor.after = None
            break

    before = cursor.before
    after = cursor.after
    if before is None:
        return after
    if after is None:
        return before
    before_delta = abs(before.timestamp_ns - timestamp_ns)
    after_delta = abs(after.timestamp_ns - timestamp_ns)
    return before if before_delta <= after_delta else after
