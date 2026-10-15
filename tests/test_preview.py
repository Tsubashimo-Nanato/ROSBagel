from __future__ import annotations

import dataclasses
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rosbagel.core.jobs import CancelledError, CancellationToken
from rosbagel.core.models import MessageRecord, TopicInfo
from rosbagel.core.point_cloud import iter_point_cloud_rows
from rosbagel.core.preview import (
    PreviewService,
    PreviewSessionSettings,
    TopicDisplaySettings,
    load_preview_settings,
    save_preview_settings,
)
from rosbagel.core.session import Session


@dataclasses.dataclass
class FakeScalar:
    value: float


@dataclasses.dataclass
class FakePointField:
    name: str
    offset: int
    datatype: int
    count: int = 1


@dataclasses.dataclass
class FakePointCloud2:
    width: int
    height: int
    fields: list[FakePointField]
    is_bigendian: bool
    point_step: int
    row_step: int
    data: bytes


class FakeReader:
    def __init__(self, records: list[MessageRecord]) -> None:
        self.records = records
        self.iter_starts = 0

    def get_topics(self) -> list[TopicInfo]:
        return [TopicInfo(name="/speed", msgtype="std_msgs/msg/Float64", category="scalar")]

    def iter_messages(self, topics: list[str] | None = None) -> object:
        self.iter_starts += 1
        topic_filter = set(topics or [])
        for record in self.records:
            if not topic_filter or record.topic in topic_filter:
                yield record

    def close(self) -> None:
        return None


class PreviewTests(unittest.TestCase):
    def test_nearest_record_uses_selected_topic(self) -> None:
        session = _session()
        preview = PreviewService(session)

        record = preview.nearest_record("/speed", 160)

        self.assertIsNotNone(record)
        self.assertEqual(record.timestamp_ns, 200)

    def test_nearest_record_reuses_forward_cursor_for_playback(self) -> None:
        session = _session()
        reader = session.reader
        preview = PreviewService(session)

        first = preview.nearest_record("/speed", 150)
        second = preview.nearest_record("/speed", 180)

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(getattr(reader, "iter_starts"), 1)

    def test_scalar_series_reads_numeric_field(self) -> None:
        session = _session()
        preview = PreviewService(session)

        series = preview.scalar_series("/speed", "value")

        self.assertEqual(series.timestamps_ns, [100, 200])
        self.assertEqual(series.values, [1.0, 2.0])

    def test_point_cloud_preview_uses_single_row_pass_when_metadata_is_valid(self) -> None:
        import struct

        cloud = FakePointCloud2(
            width=2,
            height=1,
            fields=[
                FakePointField("x", 0, 7),
                FakePointField("y", 4, 7),
                FakePointField("z", 8, 7),
            ],
            is_bigendian=False,
            point_step=12,
            row_step=24,
            data=struct.pack("<ffffff", 1.0, 2.0, 3.0, 4.0, 5.0, 6.0),
        )
        session = Session()
        reader = FakeReader([
            MessageRecord(
                "/points",
                100,
                "sensor_msgs/msg/PointCloud2",
                decoded=cloud,
            )
        ])
        session.reader = reader  # type: ignore[assignment]
        session.topics = [
            TopicInfo(
                name="/points",
                msgtype="sensor_msgs/msg/PointCloud2",
                category="point_cloud",
            )
        ]
        preview = PreviewService(session)

        with patch("rosbagel.core.preview.iter_point_cloud_rows", wraps=iter_point_cloud_rows) as rows:
            cloud_preview = preview.point_cloud_preview("/points", 100, max_points=1)

        self.assertIsNotNone(cloud_preview)
        self.assertEqual(rows.call_count, 1)

    def test_display_settings_round_trip(self) -> None:
        settings = PreviewSessionSettings(
            bag_path="bag",
            topics={
                "/speed": TopicDisplaySettings(
                    topic="/speed",
                    visible=False,
                    color="#00ff00",
                    point_size=3.0,
                    decimation=8,
                    sync_offset_sec=0.25,
                    export_format="csv",
                )
            },
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = save_preview_settings(settings, Path(temp_dir) / "rosbagel_session.json")
            loaded = load_preview_settings(path)

        self.assertEqual(loaded.bag_path, "bag")
        self.assertFalse(loaded.topics["/speed"].visible)
        self.assertEqual(loaded.topics["/speed"].export_format, "csv")

    def test_point_preview_filters_nonfinite_points_and_keeps_empty_shape(self) -> None:
        import struct

        for points, expected in (
            ([(1.0, 2.0, 3.0), (float("nan"), 0.0, 0.0), (0.0, float("inf"), 0.0)], 1),
            ([(float("nan"), 0.0, 0.0)], 0),
        ):
            with self.subTest(expected=expected):
                cloud = FakePointCloud2(len(points), 1,
                    [FakePointField("x", 0, 7), FakePointField("y", 4, 7), FakePointField("z", 8, 7)],
                    False, 12, len(points) * 12, b"".join(struct.pack("<fff", *point) for point in points))
                session = Session()
                session.reader = FakeReader([MessageRecord("/points", 100, "sensor_msgs/msg/PointCloud2", decoded=cloud)])
                session.topics = [TopicInfo("/points", "sensor_msgs/msg/PointCloud2", category="point_cloud")]
                result = PreviewService(session).point_cloud_preview("/points", 100)
                self.assertEqual(result.points.shape, (expected, 3))
                self.assertEqual(result.preview_point_count, expected)
                self.assertEqual(result.original_point_count, len(points))

    def test_point_preview_propagates_cancellation(self) -> None:
        preview = PreviewService(_session())
        token = CancellationToken.create()
        token.cancel()
        with self.assertRaises(CancelledError):
            preview.point_cloud_preview("/speed", 100, cancellation_token=token)

    def test_cancellation_token_raises(self) -> None:
        token = CancellationToken.create()
        token.cancel()

        with self.assertRaises(CancelledError):
            token.throw_if_cancelled()


def _session() -> Session:
    records = [
        MessageRecord("/speed", 100, "std_msgs/msg/Float64", decoded=FakeScalar(1.0)),
        MessageRecord("/speed", 200, "std_msgs/msg/Float64", decoded=FakeScalar(2.0)),
    ]
    session = Session()
    reader = FakeReader(records)
    session.reader = reader  # type: ignore[assignment]
    session.topics = reader.get_topics()
    session.bag_path = Path("fake")
    return session


if __name__ == "__main__":
    unittest.main()
