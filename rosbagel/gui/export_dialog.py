from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any

from rosbagel.core.export_policy import compatible_export_formats
from rosbagel.core.jobs import CancelledError


def show_export_dialog(viewer: Any) -> None:
    QtWidgets, QtCore = viewer.QtWidgets, viewer.QtCore
    session = viewer.session
    if session.reader is None:
        return
    task: Any = None

    class ExportDialog(QtWidgets.QDialog):
        def reject(self) -> None:
            if task is not None and not task.future.done():
                task.context.cancel()
                cancel_button.setEnabled(False)
                summary.setText("Cancelling export...")
                return
            super().reject()

    dialog = ExportDialog(viewer.window)
    dialog.setWindowTitle("Export topics")
    dialog.resize(760, 520)
    layout = QtWidgets.QVBoxLayout(dialog)
    search = QtWidgets.QLineEdit()
    search.setPlaceholderText("Search topics")
    search.setClearButtonEnabled(True)
    layout.addWidget(search)
    topic_list = QtWidgets.QListWidget()
    topic_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
    checked = set(viewer.topic_tree._checked_topics())
    if not checked and viewer._active_pane and viewer._active_pane.topic:
        checked.add(viewer._active_pane.topic)
    for topic in session.topics:
        item = QtWidgets.QListWidgetItem(f"{topic.name}  [{topic.category}]")
        item.setData(QtCore.Qt.ItemDataRole.UserRole, topic.name)
        item.setCheckState(QtCore.Qt.CheckState.Checked if topic.name in checked else QtCore.Qt.CheckState.Unchecked)
        topic_list.addItem(item)
    layout.addWidget(topic_list, 1)
    form = QtWidgets.QFormLayout()
    format_box = QtWidgets.QComboBox()
    out_edit = QtWidgets.QLineEdit()
    out_edit.setAccessibleName("Output directory")
    out_row = QtWidgets.QHBoxLayout()
    out_row.addWidget(out_edit, 1)
    browse = QtWidgets.QPushButton("Browse...")
    out_row.addWidget(browse)
    fps = QtWidgets.QDoubleSpinBox()
    fps.setRange(1.0, 240.0)
    fps.setValue(30)
    form.addRow("Format", format_box)
    form.addRow("Output directory", out_row)
    form.addRow("MP4 FPS", fps)
    layout.addLayout(form)
    summary = QtWidgets.QLabel()
    summary.setWordWrap(True)
    layout.addWidget(summary)
    progress = QtWidgets.QProgressBar()
    progress.setVisible(False)
    layout.addWidget(progress)
    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
    export_button = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
    cancel_button = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
    export_button.setText("Export")
    layout.addWidget(buttons)

    def selected_topics() -> list[str]:
        return [str(topic_list.item(i).data(QtCore.Qt.ItemDataRole.UserRole))
                for i in range(topic_list.count())
                if topic_list.item(i).checkState() == QtCore.Qt.CheckState.Checked]

    def refresh_summary() -> None:
        selected = selected_topics()
        fmt = format_box.currentText()
        out = out_edit.text().strip()
        export_button.setEnabled(bool(selected and fmt and out))
        fps.setEnabled(fmt == "mp4")
        summary.setText(f"{len(selected)} topic(s) selected" if selected else "No topics selected")

    def refresh_formats() -> None:
        selected = selected_topics()
        common: set[str] | None = None
        for name in selected:
            values = set(compatible_export_formats(viewer._topic_info_by_name[name]))
            common = values if common is None else common & values
        current = format_box.currentText()
        format_box.clear()
        format_box.addItems(sorted(common or []))
        if current in (common or set()):
            format_box.setCurrentText(current)
        refresh_summary()

    def filter_topics(text: str) -> None:
        for index in range(topic_list.count()):
            item = topic_list.item(index)
            item.setHidden(text.casefold() not in item.text().casefold())

    def browse_output() -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(dialog, "Select export directory")
        if path:
            out_edit.setText(path)

    def start_export() -> None:
        nonlocal task
        out = Path(out_edit.text().strip()).expanduser()
        selected = selected_topics()
        fmt = format_box.currentText()
        if not selected or not fmt or not out_edit.text().strip():
            return
        if out.exists() and not out.is_dir():
            summary.setText(f"Output is not a directory: {out}")
            return
        answer = QtWidgets.QMessageBox.question(
            dialog, "Confirm export", f"Export {len(selected)} topic(s) as {fmt} to:\n{out}?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        selections = [session.prepare_export_selection(name, fmt, out, fps=fps.value()) for name in selected]
        completed: list[Any] = []
        failures: list[str] = []
        viewer._pause_playback()
        viewer._exporting = True
        cancel_button.setText("Cancel")
        for widget in (topic_list, search, format_box, out_edit, browse, fps, export_button):
            widget.setEnabled(False)
        progress.setVisible(True)
        progress.setRange(0, 0)

        def work(context: Any) -> None:
            @contextmanager
            def progress_factory(label: str, total: int | None) -> Any:
                value = 0

                def advance(amount: int = 1) -> None:
                    nonlocal value
                    value += amount
                    context.report(label, value, total)

                context.report(label, 0, total)
                yield advance

            for selection in selections:
                context.throw_if_cancelled()
                try:
                    completed.append(session.export_topic(selection.topic, selection.format, selection.out_dir,
                                                          fps=selection.fps, progress_factory=progress_factory))
                except CancelledError:
                    raise
                except Exception as exc:
                    failures.append(f"{selection.topic}: {exc}")

        def show_progress(label: str, value: int, total: int | None) -> None:
            progress.setRange(0, total or 0)
            progress.setValue(value)
            progress.setFormat("%p%" if total else "Working")
            summary.setText(label)

        def finish(_result: Any = None, *, cancelled: bool = False) -> None:
            nonlocal task
            task = None
            viewer._exporting = False
            for message in failures:
                viewer._log(f"Export failed: {message}")
            message = f"Exported {len(completed)}/{len(selections)} topic(s) to {out}"
            if cancelled:
                message += ". Cancelled; partial output files may remain."
            elif failures:
                message += f". {len(failures)} failed; see Output."
            viewer._log(message)
            summary.setText(message)
            progress.setVisible(False)
            cancel_button.setEnabled(True)
            cancel_button.setText("Close")
            if failures or cancelled:
                for widget in (topic_list, search, format_box, out_edit, browse):
                    widget.setEnabled(True)
                refresh_formats()
                summary.setText(message)
            else:
                dialog.accept()

        def failed(message: str) -> None:
            failures.append(message)
            finish()

        task = viewer._io_tasks.submit("export", work, finish, failed, on_progress=show_progress,
                                       on_cancel=lambda: finish(cancelled=True))

    search.textChanged.connect(filter_topics)
    topic_list.itemChanged.connect(lambda _: refresh_formats())
    format_box.currentTextChanged.connect(lambda _: refresh_summary())
    out_edit.textChanged.connect(lambda _: refresh_summary())
    browse.clicked.connect(browse_output)
    buttons.accepted.connect(start_export)
    buttons.rejected.connect(dialog.reject)
    refresh_formats()
    dialog.exec()
