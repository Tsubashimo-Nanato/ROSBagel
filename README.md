# ROSBagel

English | [简体中文](README.zh-CN.md)

[![Version](https://img.shields.io/badge/version-v1.6.3-f59e0b)](https://github.com/Tsubashimo-Nanato/ROSBagel/tree/v1.6.3)
![Python](https://img.shields.io/badge/python-3.10--3.13-3776ab)
[![License](https://img.shields.io/badge/license-AGPL--3.0--or--later-4c1)](LICENSE)
![GUI](https://img.shields.io/badge/GUI-active_development-ef4444)

Open ROS 1/2 bags on Windows, inspect their contents, and export selected data without installing a full ROS environment.

ROSBagel is an offline, read-only bag inspector and exporter. Its terminal command is `bagel`. It provides a CLI, an interactive shell, and a PySide6 timeline viewer for images, lane-line point clouds, topic metadata, and synchronized previews.

![ROSBagel Timeline Viewer](docs/media/gui-timeline.png)

> [!NOTE]
> The CLI and exporters are usable today. The GUI is under active development, so interaction and rendering behavior may change between releases.

## Quick Start

```powershell
git clone https://github.com/Tsubashimo-Nanato/ROSBagel.git
cd ROSBagel
py -m pip install -e .[gui]

bagel topics .\my_bag
bagel gui .\my_bag
```

Windows helpers are also included:

```bat
install.bat
install.bat gui
```

Restart the terminal after `install.bat` if the `bagel` command is not immediately available.

## What It Does

| Area | Practical use |
| --- | --- |
| Inspect | Topic tree, detailed scan, message counts, durations, timestamps, and nearest-message lookup |
| Export | CSV, Parquet, SQLite, JSONL, NPZ, raw CDR, PNG/JPG, MP4, PCD, and PLY |
| Explore | Interactive shell with history, topic completion, and selected-export queues |
| Visualize | Timeline scrubbing, image playback, split views, lane-line overlays, point-cloud previews, and light/dark themes |
| Stay offline | Read rosbag1 and rosbag2 through `rosbags`; fall back to SQLite for raw ROS 2 access |

Bag files are never rewritten. GUI layouts and settings can be saved in a separate workspace JSON file, while lossless exports stay on their dedicated exporter path.

## Real Bag, Real Frames

The following 2x2 was generated at one timeline position from the local validation bag. It combines the RGB camera, annotated perception output, binary mask, and three lane-line `PointCloud2` topics.

![Four synchronized views from the validation bag](docs/media/validation-run-2x2.png)

The same run in motion:

![Annotated and binary mask animation](docs/media/validation-run.gif)

[Open the MP4 preview](docs/media/validation-run.mp4) for consistent playback timing.

The media above was generated from the validation bag using the documented export workflow. It uses recorded frames; no synthetic road scene or hand-drawn lane data was substituted.

## Common Commands

Start the interactive shell:

```powershell
bagel
```

A short session looks like this:

```text
bagel> open .\my_bag
bagel> topics
bagel> scan --all
bagel> inspect --time 22.2 --dur /camera/image_raw
bagel> export /camera/image_raw --format mp4 --fps 30 --out .\export
bagel> export /points --format pcd --out .\export
bagel> gui
```

The same operations are available as direct commands:

```powershell
bagel scan .\my_bag --out .\scan
bagel export .\my_bag --topic /imu --format parquet --out .\export
bagel export .\my_bag --topic /camera/image_raw --format png --out .\export
bagel export .\my_bag --topic /points --format ply --out .\export
bagel export-all .\my_bag --out .\export
```

Upgrade an installed copy with:

```powershell
bagel upgrade --yes
```

## Export Notes

- Image and video exports support decoded `sensor_msgs/msg/Image` and `sensor_msgs/msg/CompressedImage` topics.
- PCD and PLY preserve supported numeric `PointCloud2` fields such as `x`, `y`, `z`, `intensity`, `rgb`, `ring`, and `time`.
- MP4 uses constant-FPS playback and writes the original ROS timestamps to a sidecar CSV.
- PNG/JPG, point-cloud sequences, NPZ sequences, MP4, and raw exports include timestamp sidecars where applicable.
- Unsupported or undecoded custom messages can still be exported as raw serialized data.

## GUI Development

The timeline viewer is under active development. Open a bag folder or file from **File**, then double-click a topic or drag a selection into a view. Checked topics can be dragged together; point clouds share one view, while incompatible topics get separate views.

- Split the selected view with **+** and resize the divider. Drag a view title to rearrange it, detach it, or dock it beside another view; the drop area is highlighted. **New window** moves the view; **Duplicate window** in the context menu creates a copy.
- Scrub, play, or step along the shared timeline. Image loading and export run in the background. Each image cache holds at most 300 frames, with a 96 MiB budget plus the final admitted frame.
- Edit the selected topic's visibility, opacity, and time offset in **Properties**. Point clouds also have color, point-size, and decimation controls. Use **Fit** to resume automatic point framing after panning or zooming.
- Switch **Dark mode** in the timeline, or recover hidden panels with **Windows > Reset layout**. Export starts with your checked topics selected.

**Analysis** adds the rest of the offline workflow:

- **Bag report / Scan bag**: topic counts, coverage, mean frequency, format compatibility, warnings, and saved manifest/CSV reports. **Inspect all at current time** compares nearest-message offsets across topics.
- **Message inspector**: searchable fields, raw bytes, previous/next messages, and full-message JSON saves. Select numeric fields and plot them, or enter a field path with **+** in **Numeric chart**.
- **Numeric chart**: up to 16 fields across topics, full-time-range extrema sampling, a shared timeline cursor, pan/zoom/fit, and PNG/CSV output. CSV contains the plotted samples; use topic export for every message.
- **File > Save/Open workspace** restores splits, detached views, checked topics, display settings, chart fields and playback position. Recent bags and Close bag are in File. Workspace files are separate from source bags.
- **Export** supports one shared format, individual topic formats, or the core's suggested formats after a scan. Results and warnings appear in Bag analysis; Select all includes the full topic list.

The timeline stays available when switching analysis pages. The AIformula lane dock is optional under **Windows**; ordinary ROS messages do not need it. Right-click a view to inspect its message or save its image.

It is intentionally view-only: this is not a live ROS subscriber, recorder, node-graph inspector, or full RViz2 replacement. The optional 3D renderer requires working VisPy/OpenGL support. A pannable, zoomable XY projection is available when that renderer cannot initialize, including offscreen environments.

Run the GUI directly:

```powershell
bagel gui .\my_bag
```

## Sample Data Provenance

The example bag used for the media above was produced with the open [aiformula-support/aiformula](https://github.com/aiformula-support/aiformula) stack during a validation run by [Sophia-AI-formula-team/aiformula_sophia](https://github.com/Sophia-AI-formula-team/aiformula_sophia).

Background on the outdoor autonomous-driving program is available from the [AI Formula development page](https://sites.google.com/p.chibakoudai.jp/rdc-lab/development/ai-formula). The bag is local debug data and is not distributed in this repository.

These references describe data provenance and project context. They do not imply that the linked organizations maintain, endorse, or audit ROSBagel.

## Known Limits

- Image decoding currently covers common RGB/BGR/RGBA/BGRA, mono8/16, `16UC1`, and `32FC1` encodings.
- Codec availability for MP4 varies by OpenCV build and platform.
- The SQLite fallback cannot deserialize CDR messages; use the `rosbags` backend for decoded exports.
- Custom message support depends on definitions available to `rosbags` from the bag metadata.
- Playback waits for an image cache refill; the GUI remains responsive. Cancelling an export can leave partial files in its output directory.
- Bags may contain private camera, sensor, map, or laboratory data. Review exports before publishing them.

## Maintainer

Maintainer: Owen Zi-Wen ZHOU<br>
Affiliation: Sophia University, Control Engineering / AI Formula

## Contributing

Focused bug reports, edge-case notes, and small pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md). Please do not attach private bags to public issues unless they have been reviewed and sanitized.

## License

ROSBagel is released under `AGPL-3.0-or-later`. See [LICENSE](LICENSE).
