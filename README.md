# 极简视频剪辑工具 | Simple Video Clipper

[English](./README_EN.md) | 简体中文

一个专为小白用户设计的 Windows 视频剪辑工具，界面简洁，操作简单。

A simple and user-friendly video clipping tool for Windows beginners.

---

## 功能特点 | Features

- **拖放操作**: 直接拖放视频文件到窗口即可
- **剪掉开头**: 输入要剪掉的前 N 秒（默认 30 秒）
- **删除区间**: 可添加多个指定秒数区间，例如删除 10-20 秒、80-100 秒
- **达人编辑台**: 自动最大化、视频预览、时间轴拖选、删除选区、删除当前帧
- **ASS 字幕链路**: 支持 ASS/SRT 导入、剪贴板导入、字幕列表编辑、实时预览和硬字幕烧录
- **分辨率选择**: 支持多种常用分辨率
- **视频预览**: 选中文件后显示第一帧，方便确认内容
- **自动打开目录**: 处理完成后自动打开导出文件所在目录
- **实时进度**: 显示处理进度和状态
- **单文件运行**: 打包后只有一个 exe 文件

---

- **Drag & Drop**: Simply drag and drop video files into the window
- **Trim Start**: Specify seconds to trim from the beginning (default 30s)
- **Delete Ranges**: Add multiple time ranges to remove, such as 10-20s and 80-100s
- **Expert Mode MVP**: Preview video, seek to a timestamp, set in/out points, and add delete ranges
- **Basic Subtitles**: Add manual subtitles, import SRT files, and burn subtitles into exported video
- **Resolution Options**: Support for multiple common resolutions
- **Video Preview**: Show the first frame after selecting a file
- **Open Output Folder**: Automatically open the output folder after processing
- **Real-time Progress**: Display processing progress and status
- **Single Executable**: Standalone exe file after packaging

---

## 系统要求 | System Requirements

- Windows 7/10/11
- 无需安装 Python（使用打包版）
- 打包版已内置 FFmpeg

---

- Windows 7/10/11
- No Python installation required (for packaged version)
- FFmpeg is bundled in packaged releases

---

## 使用方法 | Usage

### 方式一：使用打包版（推荐）| Option 1: Use Packaged Version (Recommended)

1. 下载 `VideoClipper.exe` 从 [Releases](../../releases) 页面
2. 双击运行即可

---

1. Download `VideoClipper.exe` from the [Releases](../../releases) page
2. Double-click to run

### 方式二：从源码运行 | Option 2: Run from Source

```bash
# 克隆或下载代码 | Clone or download the code
git clone https://github.com/yourusername/video-clipper.git
cd video-clipper

# 安装依赖 | Install dependencies
pip install -r requirements.txt

# 运行 | Run
python main.py
```

---

## 打包方法 | Packaging

开发者可以打包为独立 exe：

Developers can package it as a standalone exe:

```bash
# 运行打包脚本 | Run build script
build_spec.bat
```

或者手动打包：

Or manually package after placing `ffmpeg.exe` and `ffprobe.exe` in the project folder:

```bash
pip install -r requirements.txt
pyinstaller VideoClipper.spec --clean --noconfirm
```

---

## FFmpeg 下载 | FFmpeg Download

打包脚本和 GitHub Actions 会自动下载 FFmpeg essentials build，并将 `ffmpeg.exe` 和 `ffprobe.exe` 打包进最终 EXE。

The build script and GitHub Actions automatically download the FFmpeg essentials build and bundle `ffmpeg.exe` and `ffprobe.exe` into the final EXE.

如果从源码直接运行，请将 `ffmpeg.exe` 和 `ffprobe.exe` 放到项目目录，或添加到系统 PATH。

If running from source, place `ffmpeg.exe` and `ffprobe.exe` in the project folder, or add them to system PATH.

---

## 支持的格式 | Supported Formats

**输入格式 | Input Formats:** MP4, AVI, MKV, MOV, FLV, WMV, WEBM, M4V

**输出格式 | Output Format:** MP4 (H.264 编码，兼容性最佳 | H.264 encoded, best compatibility)

---

## 项目结构 | Project Structure

```
video-clipper/
├── main.py          # 主程序入口 | Main entry point
├── gui.py           # PySide6 界面 | PySide6 GUI
├── edit_model.py    # 统一编辑模型 | Unified edit model
├── subtitle_model.py # 字幕工程模型与 ASS/SRT 读写 | Subtitle project model and ASS/SRT I/O
├── timeline_state.py # 时间轴纯逻辑 | Timeline logic
├── timeline_widget.py # 达人模式时间轴控件 | Expert timeline widget
├── ffmpeg_utils.py  # FFmpeg 工具函数 | FFmpeg utilities
├── requirements.txt # Python 依赖 | Python dependencies
├── build_spec.bat   # 一键打包脚本 | One-click build script
├── scripts/         # 构建辅助脚本 | Build helper scripts
├── README.md        # 中文说明 | Chinese documentation
├── README_EN.md     # 英文说明 | English documentation
└── .github/
    └── workflows/
        └── build.yml # CI/CD 工作流 | CI/CD workflow
```

---

## 技术栈 | Tech Stack

- Python 3.10+
- PySide6 (GUI 框架 | GUI framework)
- FFmpeg (视频处理 | Video processing)
- pysubs2 (字幕读写与 ASS/SRT 转换 | Subtitle parsing and ASS/SRT conversion)
- PyInstaller (打包工具 | Packaging tool)

---

## 许可证 | License

MIT License - 详见 [LICENSE](./LICENSE) 文件

MIT License - See [LICENSE](./LICENSE) file for details

---

## 贡献 | Contributing

欢迎提交 Issue 和 Pull Request！

Issues and Pull Requests are welcome!

---

## 截图 | Screenshots

![主界面](./docs/screenshot_1.png)
