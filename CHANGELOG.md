# Changelog

All notable changes for this project will be documented in this file.

## [Unreleased]

### Added

- GUI Analysis workflows: background bag scans, searchable coverage/frequency/format reports, manifest/topic CSV saves, nearest-message comparisons and topic duration.
- Structured-message inspector with bounded field/raw previews, previous/next message navigation, full JSON saves, and numeric-field plotting.
- Generic multi-topic numeric charts with editable field paths, up to 16 series, bounded full-range extrema sampling, timeline seeking, pan/zoom/fit, and PNG/CSV saves.
- Versioned GUI workspace files restoring split layouts, detached views, topic settings, chart fields and time; recent bags and Close bag actions.
- Batch GUI exports with shared, per-topic or scan-based suggested formats, select-all/clear, and results/warnings tables. View image capture is available from the view context menu.
- GUI bag-file picker, layout reset, point-fit controls, per-topic display editing, and a software XY point-cloud renderer.
- Local splitter resizing, view-title drag/drop indicators, and moving views between detached windows and the main layout.
- Background bag opening, previews, bounded image loading, and cancellable exports with checked-topic preselection.
- Added shell completion for `scan --all`, `scan --out`, and output paths.
- Added the interactive `manifest` command with bag path, backend, and output-path completion.
- Expanded the shell welcome with the current version, author, license, and repository.

### Fixed

- Preserve the current bag when a new open fails; stop timers and discard stale tasks on topic replacement or window close.
- Render every selected generic point-cloud topic, with independent visibility, color, size, decimation, opacity, and time offset. Initialize VisPy through its PySide6 backend.
- Keep subsecond timelines accurate, pause when stepping/seeking, and use elapsed time for playback rate. Refilling an image cache no longer recursively renders or blocks the Qt thread; short final windows can seek backward.
- Honor the newest seek and color settings while a load is in flight; explicit image cancellation waits for a retry. Preserve equal XY scale when resizing a manually panned or zoomed software point view.
- Show relevant properties only, synchronize lane checkboxes, retain tree expansion across searches, and keep checked/unchecked indicators, tree arrows, and icons legible in both themes.
- Split only the target view and restore valid active views and minimum sizes after closing or redocking. New window now moves the original pane; duplication remains a separate context-menu action.

- Report invalid or unreadable bag paths as concise command errors instead of Python tracebacks.
- Keep the interactive shell open after malformed quoted input and report the parse error inline.
- Keep split views inside the `1280x760` default layout with compact, accessible topic and pane controls.
- Use one coordinate transform for lane-line points, tick labels, and zero axes while preserving equal x/y scale and fit margin.

### Removed

- Removed synchronous image/export loops and their GUI event-pumping progress helper. Preview and export IO now share one worker thread, including SQLite reader cleanup.

### Changed

- The timeline is now a global control bar and remains usable on analysis pages. Lane overlay is opt-in through Windows instead of opening automatically for AIformula bags.
- The GUI's Save sidecar action is replaced by Save workspace with an explicit destination. Existing core display-settings serialization remains unchanged; source bags are not used as the default workspace output location.

## [1.6.2] - 2026-07-14

### Changed

- Completed the product migration to ROSBagel: the distribution is `ROSBagel`, the Python import package is `rosbagel`, and the terminal command is `bagel`.
- Renamed GUI titles, shell prompts, local history, sidecar settings, Windows launch helpers, documentation, and update metadata to the ROSBagel identity.
- Reworked `bagel uninstall` to confirm package removal and separately ask whether shared runtime and GUI dependencies should also be removed. Dependencies are kept by default.

### Removed

- Removed the historical command and Python import path. Install ROSBagel and use `bagel` / `import rosbagel`.

### Migration

- Uninstall the historical package before installing this release. `bagel uninstall` also recognizes historical package names so stale installations can be removed cleanly.

## [1.6.1] - 2026-07-10

### Changed

- Improved GUI view interactions with explicit split controls, clearer active-view selection, topic drag handling, and checked-topic cleanup controls.
- Renamed pane popout controls to "New window" / "Duplicate" where appropriate and removed the old popout wording from the GUI path.
- Updated point-cloud and lane-line plot previews to auto-fit visible data with margin before user pan/zoom adjustments.

### Fixed

- Fixed lane-line preview fitting so the current frame drives bounds instead of stale full-topic state.
- Removed the extra lane-line filled area overlay and corrected left/right/center visual ordering in the plot.

## [1.6.0] - 2026-06-13

### Changed

- Split export format policy and exporter dispatch out of the session orchestration layer, keeping the existing CLI, REPL, and exporter behavior compatible.
- Split REPL parsing and completion into focused modules while preserving existing command parsing, Windows path handling, and tab-completion behavior.
- Split GUI theme, playback cache metadata, and progress-dialog plumbing out of the timeline viewer coordinator without changing the visible GUI workflow.
- Updated package metadata, README status, and upgrade examples for the `v1.6.0` release.

### Tested

- Verified the refactor against the existing unittest and pytest suites, plus focused coverage for export policy/dispatch alignment.

## [1.5.2] - 2026-06-06

### Fixed

- Fixed GUI startup without an opened bag by making dock autosize tolerate an empty topic list.
- Moved GUI update checks and upgrade execution onto a background worker so network and pip work no longer run directly on the Qt UI thread.
- Bounded GUI image playback rendering to a fixed-size frame window instead of caching an entire image topic in memory.
- Reduced normal point cloud preview and PCD/PLY export overhead by using validated `PointCloud2` metadata for point counts instead of always doing a separate count pass.

## [1.5.1] - 2026-05-24

### Added

- Added a GUI dark/light appearance switch in `File > Version...`.
- Added a modal GUI loading progress dialog while opening bags.
- Added short eased dock-resize animation for panel autosizing.

### Changed

- GUI theme colors now drive the main view, tile, topic list, output, and control backgrounds consistently.
- GUI topic-list autosizing now uses topic data and bounded dock targets instead of relying on stale visible tree column hints.
- GUI dock resize animation now tracks horizontal and vertical animations independently, avoiding cancelled autosize updates.
- The unused central spacer is collapsed so dock panels do not leave a blank middle area between the topic list and main view.

## [1.5] - 2026-05-24

### Added

- Added Windows `install.bat`, `uninstall.bat`, and repo-local `bagel.bat` launch scripts.
- Added native `pcd` and `ply` sequence exports for decoded `sensor_msgs/msg/PointCloud2` topics.
- Added `npz` export for point clouds, numeric topics, and image/depth image frames.
- Added an optional PySide6 GUI command, `bagel gui [BAG_PATH]`, with an offline timeline viewer shell and sidecar display settings.
- Added `gui [BAG_PATH]` inside the interactive shell.
- Added GUI drag-and-drop support for bag folders/files.
- Added GUI `File > Import bag...`, `File > Export...`, and `File > Version...` actions.
- Added GUI topic drag-and-drop into preview panes.
- Added GUI right-click pane splitting with a 4x4 maximum grid.
- Added GUI pane controls for render, maximize/restore, new window, and delete.
- Added GUI progress feedback for bag loading, image playback-cache rendering, and topic exports.
- Added GUI `Windows` menu toggles for dockable panels.
- Added GUI playback rate selection for `0.25x`, `0.5x`, `1x`, `2x`, and `4x`.
- Added GUI version/update dialog with current version, local changelog, GitHub update check, release notes for newer versions, and an upgrade button.
- Added first-startup GUI update preference prompt for check-only, auto-update, or update-checker-off modes.
- Added GUI tests for dock-menu creation, default folded topic tree behavior, playback rate changes, and Version menu availability.
- Added update-check tests for GitHub release and tag metadata parsing.
- Added a reusable preview API for lazy nearest-message image, point cloud, scalar, and summary previews.
- Added cancellation primitives for future GUI background jobs.

### Changed

- Point cloud CSV, Parquet, and SQLite exports now stream or batch point rows instead of collecting the full bag export in one in-memory list.
- Image decoding now supports `mono16`, `16UC1`, and `32FC1`; non-8-bit display exports are normalized where needed.
- `export-all` can use point cloud native/default formats after decoded point cloud scans.
- GUI timeline preview now debounces slider updates and uses a forward cursor cache for smoother image/video scrubbing.
- GUI Play/Pause now renders image topics to display-sized preview frames before playback, avoiding per-tick image decoding during playback.
- GUI playback now updates visible panes immediately while the timer runs instead of waiting for the manual-scrub debounce timer.
- GUI topic trees now start folded by default.
- GUI `File > Import bag...` now opens the folder browser directly instead of showing an intermediate import dialog.
- GUI panels now use movable, closable Qt dock widgets instead of a fixed crowded splitter layout.
- GUI view tiles now have stable titles and less crowded labels.

## [1.4.3] - 2026-05-18

Shell upgrade and Windows progress fallback release.

### Added

- Added `bagel upgrade` and interactive `upgrade` shell support for upgrading the installed package from GitHub or PyPI.
- Added `--ref` support for upgrading from a specific GitHub branch, tag, commit, or exact PyPI version.

### Fixed

- Switched Windows consoles to a bounded single-line progress fallback to avoid repeated-line output flooding in `cmd.exe`.

## [1.4.2] - 2026-05-14

Stress-test and progress performance release.

### Changed

- Replaced the default progress bar with a fixed-width block bar.
- Batched progress updates for large scans and exports to reduce terminal rendering overhead.
- Optimized `inspect --time` to use a streaming nearest-message query when bag time bounds are available, avoiding a full in-memory timestamp index for common bags.

### Tested

- Added synthetic stress coverage for progress rendering and streaming inspect behavior.
- Stress-tested scan, timestamp indexing, and streaming inspect on a synthetic 250,000-message, 80-topic reader.

## [1.4.1] - 2026-05-14

Shell and topic display cleanup release.

### Changed

- Simplified topic tree colors and removed repeated full path text from tree rows.
- Highlighted topic names in orange in scan table and topic tree output.
- Moved browsing variants to `topics`, with `topics`, `topics -all`, and `topics -s`.
- Simplified `scan` to the full scan workflow and removed the scan backend/view options.
- Updated REPL completion so `scan` suggests `--all` after a bag is open and `topics` suggests `-all` and `-s`.

## [1.4.0] - 2026-05-14

Selected export and shell UX release.

### Added

- Added interactive `export-select` mode for queueing multiple topic exports, reviewing a confirmation table, and exporting the selected set.
- Added `inspect --dur TOPIC` so nearest-message inspection and topic duration checks can be run from one inspect command.

### Changed

- REPL Ctrl+C handling now interrupts the current action without closing the shell.
- REPL completion now covers `export-select`, `inspect --dur`, and the `topics -v` shortcut more directly.
- Topic tree and opened-bag status rendering now use clearer color-coded paths.
- Progress bars now include ETA when the backend provides a message count and print an interruption summary when cancelled.

## [1.3.4] - 2026-05-14

Export compatibility release.

### Changed

- Topic exports now reject incompatible media formats early, such as exporting point clouds or custom structs as `mp4`, `png`, or `jpg`.
- Flexible data exports remain available across topic types, including image topics exported as `csv`, `jsonl`, `parquet`, `sqlite`, or `raw`.
- Topic export suggestions now show tabular/raw flexibility for image topics and JSONL flexibility for point clouds.

## [1.3.2] - 2026-05-14

Interactive shell completion release.

### Changed

- REPL tab completion now advances through the next expected arguments and options for common commands, such as `export TOPIC --format FORMAT --out OUT_DIR`.
- REPL option completion now avoids suggesting options that are already present by either their short or long form.

## [1.3.1] - 2026-05-12

Performance release.

### Changed

- Single-topic exports now use backend metadata for bag start/end timestamps when available instead of building a full-bag timestamp index before export.
- Bag time bounds are cached per session after they are discovered.

## [1.3.0] - 2026-05-12

Progress and package naming release.

### Added

- Rich progress display for bag opening and progress bars for scan/indexing, exports, image sequence output, and MP4 video output.
- Shared progress callback plumbing for manifest scans, timestamp indexing, and all implemented exporters.

### Changed

- Aligned the Python import package with the distribution and command name: `rosbagel`.
- Removed the unused `tqdm` runtime dependency; progress rendering now uses the existing Rich dependency.

## [1.2.2] - 2026-05-11

Bugfix and export robustness release.

### Fixed

- Rejected unknown bag reader backends before opening a session and preserved the previous session state when open failed.
- Preserved empty arrays and dictionaries when flattening decoded messages.
- Respected `PointCloud2` row padding and avoided reading fields across point boundaries.
- Suggested tabular export formats for point cloud topics.
- Preserved distinct SQLite topic table names for topic paths that differ only by punctuation.
- Inferred numeric SQLite column types from exported row values.
- Deduplicated raw export warnings.

## [1.2.0] - 2026-05-06

Phase 4 tabular export release.

### Added

- Parquet export for flattened tabular topic data.
- SQLite session export with metadata tables, message rows, export records, and per-topic flattened tables.
- Shared tabular export collection for CSV, Parquet, and SQLite outputs, including PointCloud2 point-row expansion and raw metadata fallback rows.
- CLI and interactive shell support for dispatching `parquet` and `sqlite` export formats.

## [1.0.0] - 2026-04-28

Initial public release preparation.

### Added

- Offline ROS bag scanning with topic metadata, message counts, timestamp ranges, and category hints.
- Interactive `bagel` shell with command history and topic/path completion.
- CLI commands for `scan`, `topics`, `export`, `export-all`, `inspect`, `dur`, `manifest`, `formats`, and `uninstall`.
- CSV export for scalar/simple decoded messages and point-row export for decoded `sensor_msgs/msg/PointCloud2`.
- JSONL export for arbitrary decoded messages.
- PNG/JPG image sequence export for supported decoded image topics, with timestamp CSV sidecars.
- MP4 export for decoded image topics at constant FPS, with timestamp CSV sidecars preserving true ROS timestamps.
- Raw serialized export for undecoded or unsupported topics.
- Installation via `pip install -e .`.

### Known Incomplete Areas

- At the time of the 1.0.0 release, Parquet and SQLite exports were recognized as planned formats but were not implemented yet.
- The PySide6 GUI timeline viewer is reserved for future work and is not implemented.
- Image decoding is limited to common 8-bit encodings and OpenCV-decodable compressed images.
- Custom message support depends on what `rosbags` can deserialize from bag metadata unless future custom definition loading is added.
