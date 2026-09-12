from __future__ import annotations

from pathlib import Path
from typing import Any

from rosbagel.core.export_policy import compatible_export_formats, default_export_formats
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
    select_row = QtWidgets.QHBoxLayout()
    select_row.addWidget(search, 1)
    select_all = QtWidgets.QPushButton("Select all")
    clear_all = QtWidgets.QPushButton("Clear")
    select_row.addWidget(select_all)
    select_row.addWidget(clear_all)
    layout.addLayout(select_row)
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
    mode = QtWidgets.QComboBox()
    mode.setAccessibleName("Export format mode")
    mode.addItems(("Shared format", "Per-topic formats", "Suggested formats"))
    format_box = QtWidgets.QComboBox()
    per_topic = QtWidgets.QTableWidget(0, 2)
    per_topic.setHorizontalHeaderLabels(("Topic", "Format"))
    per_topic.horizontalHeader().setStretchLastSection(True)
    per_topic.setColumnWidth(0, 480)
    per_topic.setMaximumHeight(180)
    per_topic.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
    layout.addWidget(per_topic)
    topic_formats: dict[str, str] = {}
    out_edit = QtWidgets.QLineEdit()
    out_edit.setAccessibleName("Output directory")
    out_row = QtWidgets.QHBoxLayout()
    out_row.addWidget(out_edit, 1)
    browse = QtWidgets.QPushButton("Browse...")
    out_row.addWidget(browse)
    fps = QtWidgets.QDoubleSpinBox()
    fps.setRange(1.0, 240.0)
    fps.setValue(30)
    form.addRow("Format mode", mode)
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
        formats = [fmt for name, fmt in export_pairs()]
        out = out_edit.text().strip()
        needs_scan = mode.currentText() == "Suggested formats" and session.manifest is None
        export_button.setEnabled(bool(formats and out and not needs_scan))
        fps.setEnabled("mp4" in formats)
        summary.setText("Scan the bag in Analysis to determine suggested formats." if needs_scan else
                        f"{len(selected)} topic(s), {len(formats)} export(s)" if selected else "No topics selected")

    def export_pairs() -> list[tuple[str, str]]:
        result = []
        for name in selected_topics():
            if mode.currentText() == "Suggested formats":
                info = viewer._topic_info_by_name[name]
                result.extend((name, fmt) for fmt in default_export_formats(info) if info.message_count)
            else:
                fmt = topic_formats.get(name, "") if mode.currentText() == "Per-topic formats" else format_box.currentText()
                if fmt:
                    result.append((name, fmt))
        return result

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
        format_box.setEnabled(mode.currentText() == "Shared format")
        per_topic.setVisible(mode.currentText() == "Per-topic formats")
        per_topic.setRowCount(len(selected))
        for row, name in enumerate(selected):
            options = compatible_export_formats(viewer._topic_info_by_name[name])
            topic_formats.setdefault(name, options[0] if options else "")
            combo = QtWidgets.QComboBox()
            combo.addItems(options)
            combo.setCurrentText(topic_formats[name])
            def changed(value, topic=name):
                topic_formats[topic] = value
                refresh_summary()
            combo.currentTextChanged.connect(changed)
            per_topic.setItem(row, 0, QtWidgets.QTableWidgetItem(name))
            per_topic.setCellWidget(row, 1, combo)
        refresh_summary()

    def set_all(checked: bool) -> None:
        blocker = QtCore.QSignalBlocker(topic_list)
        for index in range(topic_list.count()):
            topic_list.item(index).setCheckState(QtCore.Qt.CheckState.Checked if checked else QtCore.Qt.CheckState.Unchecked)
        del blocker
        refresh_formats()

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
        pairs = export_pairs()
        if not pairs or not out_edit.text().strip():
            return
        if out.exists() and not out.is_dir():
            summary.setText(f"Output is not a directory: {out}")
            return
        answer = QtWidgets.QMessageBox.question(
            dialog, "Confirm export", f"Run {len(pairs)} export(s) for {len(selected)} topic(s) to:\n{out}?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        selections = [session.prepare_export_selection(name, fmt, out, fps=fps.value()) for name, fmt in pairs]
        completed: list[Any] = []
        failures: list[str] = []
        viewer._pause_playback()
        viewer._exporting = True
        cancel_button.setText("Cancel")
        for widget in (topic_list, search, format_box, out_edit, browse, fps, export_button, mode, per_topic, select_all, clear_all):
            widget.setEnabled(False)
        progress.setVisible(True)
        progress.setRange(0, 0)

        def work(context: Any) -> None:
            for selection in selections:
                context.throw_if_cancelled()
                try:
                    completed.append(session.export_topic(selection.topic, selection.format, selection.out_dir,
                                                          fps=selection.fps, progress_factory=context.progress_factory))
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
            message = f"Completed {len(completed)}/{len(selections)} exports to {out}"
            if cancelled:
                message += ". Cancelled; partial output files may remain."
            elif failures:
                message += f". {len(failures)} failed; see Output."
            viewer._log(message)
            viewer.workflows.record_exports(completed, failures)
            summary.setText(message)
            progress.setVisible(False)
            cancel_button.setEnabled(True)
            cancel_button.setText("Close")
            if failures or cancelled:
                for widget in (topic_list, search, format_box, out_edit, browse, mode, per_topic, select_all, clear_all):
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
    select_all.clicked.connect(lambda: set_all(True))
    clear_all.clicked.connect(lambda: set_all(False))
    mode.currentTextChanged.connect(lambda _: refresh_formats())
    topic_list.itemChanged.connect(lambda _: refresh_formats())
    format_box.currentTextChanged.connect(lambda _: refresh_summary())
    out_edit.textChanged.connect(lambda _: refresh_summary())
    browse.clicked.connect(browse_output)
    buttons.accepted.connect(start_export)
    buttons.rejected.connect(dialog.reject)
    refresh_formats()
    dialog.exec()
