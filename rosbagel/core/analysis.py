from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
import math
from numbers import Real
from typing import Any


@dataclass(slots=True)
class MessageField:
    path: str
    type_name: str
    text: str
    numeric: bool


def message_fields(message: Any, *, limit: int = 2048, array_limit: int = 32) -> list[MessageField]:
    """Bound the inspector without materializing image/cloud payload arrays."""
    rows: list[MessageField] = []

    def visit(value: Any, path: str, depth: int) -> None:
        if len(rows) >= limit:
            return
        if depth > 12:
            rows.append(MessageField(path, type(value).__name__, "Nested value (depth limit)", False))
            return
        numeric = isinstance(value, Real) and math.isfinite(float(value))
        if numeric or value is None or isinstance(value, (str, bytes, bytearray, Real)):
            text = f"{len(value):,} bytes" if isinstance(value, (bytes, bytearray)) else str(value)
            rows.append(MessageField(path or "value", type(value).__name__, text[:512], numeric))
            return
        if is_dataclass(value):
            children = ((item.name, getattr(value, item.name)) for item in fields(value) if not item.name.startswith("_"))
        elif isinstance(value, Mapping):
            children = value.items()
        elif hasattr(value, "shape") or isinstance(value, Sequence):
            length = len(value) if not hasattr(value, "ndim") or value.ndim else 0
            rows.append(MessageField(path or "value", type(value).__name__, f"{length:,} items", False))
            children = ((str(index), value[index]) for index in range(min(length, array_limit)))
        elif hasattr(value, "__slots__"):
            children = ((key, getattr(value, key)) for key in value.__slots__ if not key.startswith("_") and hasattr(value, key))
        elif hasattr(value, "__dict__"):
            children = ((key, item) for key, item in vars(value).items() if not key.startswith("_"))
        else:
            rows.append(MessageField(path or "value", type(value).__name__, str(value)[:512], False))
            return
        for key, item in children:
            visit(item, f"{path}.{key}" if path else str(key), depth + 1)
            if len(rows) >= limit:
                break

    visit(message, "", 0)
    return rows


def numeric_field(message: Any, path: str) -> float | None:
    if path == "value" and isinstance(message, Real):
        value = message
    else:
        value = message
        for part in path.split("."):
            try:
                if isinstance(value, Mapping):
                    value = value[part]
                elif part.isdecimal() and (isinstance(value, Sequence) or hasattr(value, "shape")):
                    value = value[int(part)]
                else:
                    value = getattr(value, part)
            except (KeyError, AttributeError, IndexError, TypeError):
                return None
    return float(value) if isinstance(value, Real) and math.isfinite(float(value)) else None


@dataclass(slots=True)
class NumericCurve:
    topic: str
    field: str
    timestamps_ns: list[int]
    values: list[float]
    sample_count: int


def load_numeric_curves(reader: Any, selections: list[tuple[str, str]], start_ns: int,
                        end_ns: int, context: Any, *, max_points: int = 10000) -> list[NumericCurve]:
    if not selections or len(selections) > 16:
        raise ValueError("Select between 1 and 16 numeric fields")
    if end_ns < start_ns or max_points < 4:
        raise ValueError("Invalid chart time range or point budget")
    selections = list(dict.fromkeys(selections))
    # Two extrema per time bucket preserve spikes across the entire bag range.
    bucket_count = max_points // 2
    buckets: list[dict[int, list[tuple[int, float]]]] = [{} for _ in selections]
    counts = [0] * len(selections)
    by_topic: dict[str, list[tuple[int, str]]] = {}
    for index, (topic, field) in enumerate(selections):
        by_topic.setdefault(topic, []).append((index, field))
    for index, record in enumerate(reader.iter_messages(topics=list(by_topic))):
        context.throw_if_cancelled()
        if record.decoded is None or not start_ns <= record.timestamp_ns <= end_ns:
            continue
        bucket = min(bucket_count - 1, (record.timestamp_ns - start_ns) * bucket_count // max(1, end_ns - start_ns))
        for series_index, field in by_topic.get(record.topic, []):
            value = numeric_field(record.decoded, field)
            if value is None:
                continue
            counts[series_index] += 1
            point = (record.timestamp_ns, value)
            extremes = buckets[series_index].setdefault(bucket, [point, point])
            if value < extremes[0][1]:
                extremes[0] = point
            if value >= extremes[1][1]:
                extremes[1] = point
        if index % 100 == 0:
            context.report("Loading numeric fields", index + 1, None)
    result = []
    for index, (topic, field) in enumerate(selections):
        points = sorted({point for extremes in buckets[index].values() for point in extremes})
        result.append(NumericCurve(topic, field, [p[0] for p in points], [p[1] for p in points], counts[index]))
    return result


def adjacent_timestamp(reader: Any, topic: str, timestamp_ns: int, direction: int, context: Any) -> int | None:
    previous = None
    for record in reader.iter_messages(topics=[topic]):
        context.throw_if_cancelled()
        if record.timestamp_ns > timestamp_ns and direction > 0:
            return record.timestamp_ns
        if record.timestamp_ns >= timestamp_ns and direction < 0:
            return previous
        previous = record.timestamp_ns
    return previous if direction < 0 else None
