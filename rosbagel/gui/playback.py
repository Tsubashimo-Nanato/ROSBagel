from __future__ import annotations

from dataclasses import dataclass
from typing import Any


MAX_RENDERED_PLAYBACK_FRAMES = 300
MAX_RENDERED_PLAYBACK_BYTES = 96 * 1024 * 1024


@dataclass(slots=True)
class RenderedFrame:
    timestamp_ns: int
    image: Any
    width: int
    height: int
    encoding: str


@dataclass(slots=True)
class PlaybackWindow:
    frames: list[RenderedFrame]
    reaches_end: bool
    errors: list[str]


def load_playback_window(reader: Any, topic: str, start_ns: int | None,
                         decode: Any, to_image: Any, context: Any) -> PlaybackWindow:
    frames: list[RenderedFrame] = []
    errors: list[str] = []
    byte_count = 0
    previous: Any = None
    reaches_end = True
    # Retain the preceding record so seeking between messages uses nearest-frame
    # semantics, including requests after this topic's final message.
    for record in reader.iter_messages(topics=[topic]):
        context.throw_if_cancelled()
        if start_ns is not None and record.timestamp_ns < start_ns:
            previous = record
            continue
        records = [previous, record] if previous is not None else [record]
        previous = None
        for candidate in records:
            try:
                frame = decode(candidate)
                image = to_image(frame.array)
            except Exception as exc:
                if len(errors) < 5:
                    errors.append(f"{candidate.timestamp_ns}: {exc}")
                continue
            frames.append(RenderedFrame(candidate.timestamp_ns, image, frame.width, frame.height, frame.encoding))
            byte_count += image.sizeInBytes()
        context.report(f"Rendering {topic}", len(frames), MAX_RENDERED_PLAYBACK_FRAMES)
        if len(frames) >= MAX_RENDERED_PLAYBACK_FRAMES or byte_count >= MAX_RENDERED_PLAYBACK_BYTES:
            reaches_end = False
            break
    if not frames and previous is not None:
        context.throw_if_cancelled()
        try:
            frame = decode(previous)
            image = to_image(frame.array)
            frames.append(RenderedFrame(previous.timestamp_ns, image, frame.width, frame.height, frame.encoding))
        except Exception as exc:
            errors.append(str(exc))
    return PlaybackWindow(frames, reaches_end, errors)
