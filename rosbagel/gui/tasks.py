from __future__ import annotations

from concurrent.futures import CancelledError as FutureCancelledError, Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Event, Lock
from typing import Any, Callable

from rosbagel.core.jobs import CancelledError, CancellationToken


@dataclass
class TaskContext(CancellationToken):
    _event: Event = field(default_factory=Event)
    _lock: Lock = field(default_factory=Lock)
    _progress: tuple[str, int, int | None] | None = None

    def report(self, label: str, value: int = 0, total: int | None = None) -> None:
        self.throw_if_cancelled()
        with self._lock:
            self._progress = (label, value, total)

    def take_progress(self) -> tuple[str, int, int | None] | None:
        with self._lock:
            progress, self._progress = self._progress, None
        return progress

    @contextmanager
    def progress_factory(self, label: str, total: int | None):
        value = 0

        def advance(amount: int = 1) -> None:
            nonlocal value
            value += amount
            self.report(label, value, total)

        self.report(label, 0, total)
        yield advance


@dataclass
class GuiTask:
    key: str
    context: TaskContext
    future: Future
    on_success: Callable[[Any], None]
    on_error: Callable[[str], None]
    on_progress: Callable[[str, int, int | None], None] | None
    on_cancel: Callable[[], None] | None


class GuiTaskRunner:
    """Keep bag readers on one worker thread; deliver results on the Qt thread."""

    def __init__(self, QtCore: Any, parent: Any) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bagel-io")
        self._tasks: list[GuiTask] = []
        self._closed = False
        self._timer = QtCore.QTimer(parent)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._poll)

    def submit(
        self,
        key: str,
        work: Callable[[TaskContext], Any],
        on_success: Callable[[Any], None],
        on_error: Callable[[str], None],
        *,
        on_progress: Callable[[str, int, int | None], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
    ) -> GuiTask:
        if self._closed:
            raise RuntimeError("The viewer is closed")
        self.cancel(key)
        context = TaskContext()

        def run() -> Any:
            context.throw_if_cancelled()
            return work(context)

        task = GuiTask(key, context, self._executor.submit(run), on_success,
                       on_error, on_progress, on_cancel)
        self._tasks.append(task)
        self._timer.start()
        return task

    def cancel(self, key: str) -> None:
        for task in self._tasks:
            if task.key == key:
                task.context.cancel()
                task.future.cancel()
                # A replacement owns the UI now; the old callback must not clear it.
                task.on_cancel = None

    @property
    def busy(self) -> bool:
        return bool(self._tasks)

    def _poll(self) -> None:
        for task in list(self._tasks):
            if self._closed:
                return
            if task not in self._tasks:
                continue
            progress = task.context.take_progress()
            if progress is not None and task.on_progress and not task.context.is_cancelled:
                task.on_progress(*progress)
            if not task.future.done():
                continue
            self._tasks.remove(task)
            try:
                result = task.future.result()
            except (CancelledError, FutureCancelledError):
                if task.on_cancel:
                    task.on_cancel()
            except Exception as exc:
                if not task.context.is_cancelled:
                    task.on_error(str(exc))
            else:
                if task.context.is_cancelled:
                    if task.on_cancel:
                        task.on_cancel()
                else:
                    task.on_success(result)
        if not self._tasks:
            self._timer.stop()

    def close(self, cleanup: Callable[[], None]) -> None:
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        for task in self._tasks:
            task.context.cancel()
            task.future.cancel()
        self._tasks.clear()
        # SQLite connections must be closed on the thread that opened them.
        self._executor.submit(cleanup)
        self._executor.shutdown(wait=False)
