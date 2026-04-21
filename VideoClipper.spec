# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.building.build_main import Analysis, PYZ, EXE

block_cipher = None
# build_spec.bat 会先切到项目目录，因此这里直接使用当前工作目录。
project_dir = Path.cwd().resolve()


def required_binary(filename):
    binary_path = project_dir / filename
    if not binary_path.exists():
        raise FileNotFoundError(f"Required binary not found: {binary_path}")
    return [(str(binary_path), ".")]

a = Analysis(
    ['main.py'],
    pathex=[str(project_dir)],
    binaries=required_binary('ffmpeg.exe') + required_binary('ffprobe.exe'),
    datas=[],
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'gui',
        'ffmpeg_utils',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='VideoClipper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
