# Simple Video Clipper

[简体中文](./README.md) | English

A simple and user-friendly video clipping tool for Windows beginners.

---

## Features

- **Drag & Drop**: Simply drag and drop video files into the window
- **Trim Start**: Specify seconds to trim from the beginning (default 30s)
- **Resolution Options**: Support for multiple common resolutions
- **Video Preview**: Show the first frame after selecting a file
- **Open Output Folder**: Automatically open the output folder after processing
- **Real-time Progress**: Display processing progress and status
- **Single Executable**: Standalone exe file after packaging

---

## System Requirements

- Windows 7/10/11
- No Python installation required (for packaged version)
- FFmpeg is bundled in packaged releases

---

## Usage

### Option 1: Use Packaged Version (Recommended)

1. Download `VideoClipper.exe` from the [Releases](../../releases) page
2. Double-click to run

### Option 2: Run from Source

```bash
# Clone or download the code
git clone https://github.com/yourusername/video-clipper.git
cd video-clipper

# Install dependencies
pip install -r requirements.txt

# Run
python main.py
```

---

## Packaging

Developers can package it as a standalone exe:

```bash
# Run build script
build_spec.bat
```

Or manually package after placing `ffmpeg.exe` and `ffprobe.exe` in the project folder:

```bash
pip install -r requirements.txt
pyinstaller VideoClipper.spec --clean --noconfirm
```

---

## FFmpeg Download

The build script and GitHub Actions automatically download the FFmpeg essentials build and bundle `ffmpeg.exe` and `ffprobe.exe` into the final EXE.

If running from source, place `ffmpeg.exe` and `ffprobe.exe` in the project folder, or add them to system PATH.

---

## Supported Formats

**Input Formats:** MP4, AVI, MKV, MOV, FLV, WMV, WEBM, M4V

**Output Format:** MP4 (H.264 encoded, best compatibility)

---

## Project Structure

```
video-clipper/
├── main.py          # Main entry point
├── gui.py           # PySide6 GUI
├── ffmpeg_utils.py  # FFmpeg utilities
├── requirements.txt # Python dependencies
├── build_spec.bat   # One-click build script
├── scripts/         # Build helper scripts
├── README.md        # Chinese documentation
├── README_EN.md     # English documentation
└── .github/
    └── workflows/
        └── build.yml # CI/CD workflow
```

---

## Tech Stack

- Python 3.8+
- PySide6 (GUI framework)
- FFmpeg (Video processing)
- PyInstaller (Packaging tool)

---

## License

MIT License - See [LICENSE](./LICENSE) file for details

---

## Contributing

Issues and Pull Requests are welcome!

---

## Screenshots

![Main Interface](./docs/screenshot_main.png)
