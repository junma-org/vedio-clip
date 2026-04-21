# Simple Video Clipper

[简体中文](./README.md) | English

A simple and user-friendly video clipping tool for Windows beginners.

---

## Features

- **Drag & Drop**: Simply drag and drop video files into the window
- **Trim Start**: Specify seconds to trim from the beginning (default 30s)
- **Resolution Options**: Support for multiple common resolutions
- **Real-time Progress**: Display processing progress and status
- **Single Executable**: Standalone exe file after packaging

---

## System Requirements

- Windows 7/10/11
- No Python installation required (for packaged version)
- FFmpeg required (the app will guide you on how to obtain it)

---

## Usage

### Option 1: Use Packaged Version (Recommended)

1. Download `VideoClipper.exe` from the [Releases](../../releases) page
2. (Optional) Place `ffmpeg.exe` and `ffprobe.exe` in the same directory
3. Double-click to run

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
build.bat
```

Or manually package:

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name "VideoClipper" main.py
```

---

## FFmpeg Download

If the app reports FFmpeg not found:

1. Visit https://ffmpeg.org/download.html
2. Download Windows builds (essentials version)
3. Extract and locate `ffmpeg.exe` and `ffprobe.exe`
4. Place them in the same directory as the app, or add to system PATH

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
├── build.bat        # One-click build script
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

*(Screenshots will be added in future versions)*
