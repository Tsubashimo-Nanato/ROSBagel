# ROSBagel

[English](README.md) | 简体中文

[![Release](https://img.shields.io/badge/release-v1.6.2-f59e0b)](https://github.com/Tsubashimo-Nanato/ROSBagel/releases)
![Python](https://img.shields.io/badge/python-3.10--3.13-3776ab)
[![License](https://img.shields.io/badge/license-AGPL--3.0--or--later-4c1)](LICENSE)
![GUI](https://img.shields.io/badge/GUI-active_development-ef4444)

在 Windows 上直接打开 ROS 1/2 bag，检查其内容，并在无需完整安装 ROS 环境的情况下导出所需数据。

ROSBagel 是一个离线、只读的 bag 检查与导出工具，终端命令为 `bagel`。它提供 CLI、交互式 shell，以及基于 PySide6 的时间线查看器，可用于图像、车道线点云、topic 元数据和同步预览。

![ROSBagel 时间线查看器](docs/media/gui-timeline.png)

> [!NOTE]
> CLI 和导出器已经可以投入实际使用。GUI 仍在积极开发中，交互和渲染会继续改进。

## 快速开始

```powershell
git clone https://github.com/Tsubashimo-Nanato/ROSBagel.git
cd ROSBagel
py -m pip install -e .[gui]

bagel topics .\my_bag
bagel gui .\my_bag
```

仓库也提供了 Windows 安装脚本：

```bat
install.bat
install.bat gui
```

如果执行 `install.bat` 后终端暂时找不到 `bagel`，请重启终端。

## 能做什么

| 范围 | 实际用途 |
| --- | --- |
| 检查 | Topic 树、详细扫描、消息数量、持续时间、时间戳和最近消息查询 |
| 导出 | CSV、Parquet、SQLite、JSONL、NPZ、原始 CDR、PNG/JPG、MP4、PCD 和 PLY |
| 探索 | 带历史记录、topic 补全和批量导出队列的交互式 shell |
| 可视化 | 时间线拖动、图像播放、分屏、车道线叠加、点云预览和明暗主题 |
| 保持离线 | 通过 `rosbags` 读取 rosbag1/rosbag2；必要时使用 SQLite 读取原始 ROS 2 数据 |

工具不会重写 bag。GUI 设置可以保存在 sidecar JSON 中，无损导出仍由独立的导出器完成。

## 真实 Bag，真实画面

下面的 2x2 图来自本地验证 bag 的同一时间点，包含 RGB 相机、带标注的感知结果、二值 mask，以及三条车道线 `PointCloud2` topic。

![验证 bag 的四路同步视图](docs/media/validation-run-2x2.png)

同一次验证 run 的动态预览：

![标注结果和二值 mask 动画](docs/media/validation-run.gif)

[打开 MP4 预览](docs/media/validation-run.mp4)可获得一致的播放时序。

上方媒体均通过文档所述的导出流程从验证 bag 生成，使用的是记录帧，没有以合成道路场景或手绘车道线替代。

## 常用命令

启动交互式 shell：

```powershell
bagel
```

一个简短的交互过程如下：

```text
bagel> open .\my_bag
bagel> topics
bagel> scan --all
bagel> inspect --time 22.2 --dur /camera/image_raw
bagel> export /camera/image_raw --format mp4 --fps 30 --out .\export
bagel> export /points --format pcd --out .\export
bagel> gui
```

也可以直接使用对应命令：

```powershell
bagel scan .\my_bag --out .\scan
bagel export .\my_bag --topic /imu --format parquet --out .\export
bagel export .\my_bag --topic /camera/image_raw --format png --out .\export
bagel export .\my_bag --topic /points --format ply --out .\export
bagel export-all .\my_bag --out .\export
```

升级已安装版本：

```powershell
bagel upgrade --yes
```

## 导出说明

- 图像和视频导出支持已解码的 `sensor_msgs/msg/Image` 与 `sensor_msgs/msg/CompressedImage` topic。
- PCD 和 PLY 会保留受支持的 `PointCloud2` 数值字段，例如 `x`、`y`、`z`、`intensity`、`rgb`、`ring` 和 `time`。
- MP4 使用固定 FPS 播放，并将原始 ROS 时间戳写入 sidecar CSV。
- PNG/JPG、点云序列、NPZ 序列、MP4 和 raw 导出会在适用时附带时间戳 sidecar。
- 不支持或无法解码的自定义消息仍可导出为原始序列化数据。

## GUI 开发状态

GUI 仍在积极开发中。从 **File** 打开 bag 文件夹或文件后，双击 topic，或将选择项拖入 view。勾选的 topic 可以一起拖动；点云在同一视图叠加，不兼容的 topic 分别打开。

- **+** 只分割当前视图，分隔条可调整大小。拖动标题可以重排、独立成窗或拖回其他视图旁，放置区域会高亮。**New window** 移动原视图，右键菜单的 **Duplicate window** 创建副本。
- 拖动时间线、播放和单步共用同一时间位置。图像加载和导出在后台执行，每个图像缓存最多 300 帧，并设有 96 MiB 内存预算，允许最后接纳的一帧略微超出预算。
- **Properties** 修改所选 topic 的可见性、透明度和时间偏移；普通点云还可调整颜色、点大小和采样密度。平移或缩放后，用 **Fit** 恢复自动取景。
- 时间线上的 **Dark mode** 切换明暗主题；**Windows > Reset layout** 恢复隐藏面板。导出对话框自动选中左侧已勾选的 topic。

它有意保持只读：这不是实时 ROS subscriber、recorder、节点图检查器或完整的 RViz2 替代品。可选 3D 渲染依赖 VisPy/OpenGL；无法初始化时（包括离屏环境），使用可平移、缩放的 XY 点云投影。

直接启动 GUI：

```powershell
bagel gui .\my_bag
```

## 示例数据来源

上述媒体使用的示例 bag 来自一次采用开源 [aiformula-support/aiformula](https://github.com/aiformula-support/aiformula) 技术栈、由 [Sophia-AI-formula-team/aiformula_sophia](https://github.com/Sophia-AI-formula-team/aiformula_sophia) 完成的验证 run。

室外自动驾驶项目的背景资料可参阅 [AI Formula 开发页面](https://sites.google.com/p.chibakoudai.jp/rdc-lab/development/ai-formula)。该 bag 仅作为本地调试数据，不随本仓库分发。

这些链接用于说明数据来源和项目背景，不代表相关组织维护、认可或审计 ROSBagel。

## 已知限制

- 图像解码目前覆盖常见的 RGB/BGR/RGBA/BGRA、mono8/16、`16UC1` 和 `32FC1` 编码。
- MP4 codec 可用性取决于 OpenCV 构建和运行平台。
- SQLite fallback 无法反序列化 CDR 消息；需要解码导出时请使用 `rosbags` backend。
- 自定义消息支持取决于 `rosbags` 能否从 bag 元数据获得对应定义。
- 大尺寸、高分辨率图像 topic 在刷新受限播放窗口时可能短暂停顿。
- Bag 可能包含私有相机、传感器、地图或实验室数据，请在发布导出内容前完成检查。

## 维护者

维护者：Owen Zi-Wen ZHOU<br>
所属：Sophia University，Control Engineering / AI Formula

## 参与贡献

欢迎聚焦的问题报告、边界情况说明和小型 pull request。请参阅 [CONTRIBUTING.md](CONTRIBUTING.md)。除非已经完成检查和脱敏，请勿将私有 bag 附加到公开 issue。

## 许可证

ROSBagel 使用 `AGPL-3.0-or-later` 许可证，详见 [LICENSE](LICENSE)。
