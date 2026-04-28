# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.building.build_main import Analysis, PYZ, EXE
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

block_cipher = None
# build_spec.bat 会先切到项目目录，因此这里直接使用当前工作目录。
project_dir = Path.cwd().resolve()


def required_binary(filename):
    binary_path = project_dir / filename
    if not binary_path.exists():
        raise FileNotFoundError(f"Required binary not found: {binary_path}")
    return [(str(binary_path), ".")]


def optional_data_dir(dirname):
    data_path = project_dir / dirname
    if not data_path.exists():
        return []
    return [(str(data_path), dirname)]

whisper_datas = (
    collect_data_files('faster_whisper')
    + copy_metadata('faster-whisper')
    + copy_metadata('ctranslate2')
    + copy_metadata('av')
)
whisper_hiddenimports = (
    collect_submodules('faster_whisper')
    + collect_submodules('ctranslate2')
    + collect_submodules('av')
)

a = Analysis(
    ['main.py'],
    pathex=[str(project_dir)],
    binaries=required_binary('ffmpeg.exe') + required_binary('ffprobe.exe'),
    datas=optional_data_dir('fonts') + whisper_datas,
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets',
        'gui',
        'ffmpeg_utils',
        'whisper_utils',
        'pysubs2',
        'subtitle_model',
        'timeline_state',
        'timeline_widget',
    ] + whisper_hiddenimports,
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
