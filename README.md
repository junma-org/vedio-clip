# 极简视频剪辑工具 | Simple Video Clipper

[English](./README_EN.md) | 简体中文

一个专为小白用户设计的 Windows 视频剪辑工具，界面简洁，操作简单。

A simple and user-friendly video clipping tool for Windows beginners.

---

## 功能特点 | Features

- **拖放操作**: 直接拖放视频文件到窗口即可
- **剪掉开头**: 输入要剪掉的前 N 秒（默认 30 秒）
- **分辨率选择**: 支持多种常用分辨率
- **实时进度**: 显示处理进度和状态
- **单文件运行**: 打包后只有一个 exe 文件

---

- **Drag & Drop**: Simply drag and drop video files into the window
- **Trim Start**: Specify seconds to trim from the beginning (default 30s)
- **Resolution Options**: Support for multiple common resolutions
- **Real-time Progress**: Display processing progress and status
- **Single Executable**: Standalone exe file after packaging

---

## 系统要求 | System Requirements

- Windows 7/10/11
- 无需安装 Python（使用打包版）
- 需要 FFmpeg（软件会提示如何获取）

---

- Windows 7/10/11
- No Python installation required (for packaged version)
- FFmpeg required (the app will guide you on how to obtain it)

---

## 使用方法 | Usage

### 方式一：使用打包版（推荐）| Option 1: Use Packaged Version (Recommended)

1. 下载 `VideoClipper.exe` 从 [Releases](../../releases) 页面
2. （可选）将 `ffmpeg.exe` 和 `ffprobe.exe` 放到同目录
3. 双击运行即可

---

1. Download `VideoClipper.exe` from the [Releases](../../releases) page
2. (Optional) Place `ffmpeg.exe` and `ffprobe.exe` in the same directory
3. Double-click to run

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
build.bat
```

或者手动打包：

Or manually package:

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name "VideoClipper" main.py
```

---

## FFmpeg 下载 | FFmpeg Download

如果软件提示未找到 FFmpeg：

If the app reports FFmpeg not found:

1. 访问 https://ffmpeg.org/download.html
2. 下载 Windows builds (essentials 版本)
3. 解压后找到 `ffmpeg.exe` 和 `ffprobe.exe`
4. 放到软件同目录下，或添加到系统 PATH

---

1. Visit https://ffmpeg.org/download.html
2. Download Windows builds (essentials version)
3. Extract and locate `ffmpeg.exe` and `ffprobe.exe`
4. Place them in the same directory as the app, or add to system PATH

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
├── ffmpeg_utils.py  # FFmpeg 工具函数 | FFmpeg utilities
├── requirements.txt # Python 依赖 | Python dependencies
├── build.bat        # 一键打包脚本 | One-click build script
├── README.md        # 中文说明 | Chinese documentation
├── README_EN.md     # 英文说明 | English documentation
└── .github/
    └── workflows/
        └── build.yml # CI/CD 工作流 | CI/CD workflow
```

---

## 技术栈 | Tech Stack

- Python 3.8+
- PySide6 (GUI 框架 | GUI framework)
- FFmpeg (视频处理 | Video processing)
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

![主界面](./docs/screenshot_main.png)

*(截图将在后续版本添加 | Screenshots will be added in future versions)*
