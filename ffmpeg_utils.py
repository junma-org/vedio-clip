"""
FFmpeg工具模块
负责FFmpeg路径检测和命令执行
"""
import json
import sys
import subprocess
import shutil
from pathlib import Path


def _runtime_search_dirs():
    """
    返回运行时可能存在资源文件的目录。
    覆盖源码运行、PyInstaller onefile 临时目录，以及 exe 所在目录。
    """
    candidates = []

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass))

    if getattr(sys, "frozen", False) and getattr(sys, "executable", None):
        candidates.append(Path(sys.executable).resolve().parent)

    if sys.argv and sys.argv[0]:
        candidates.append(Path(sys.argv[0]).resolve().parent)

    candidates.append(Path(__file__).resolve().parent)
    candidates.append(Path.cwd())

    unique_dirs = []
    seen = set()
    for candidate in candidates:
        try:
            key = str(candidate.resolve())
        except OSError:
            key = str(candidate)
        if key not in seen:
            unique_dirs.append(candidate)
            seen.add(key)

    return unique_dirs


def _find_local_binary(binary_names):
    """在应用目录和打包目录中查找可执行文件。"""
    for base_dir in _runtime_search_dirs():
        for name in binary_names:
            candidate = base_dir / name
            if candidate.exists():
                return str(candidate)
    return None


def _creationflags():
    """Windows下隐藏ffmpeg/ffprobe控制台窗口。"""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def find_ffmpeg():
    """
    查找FFmpeg可执行文件
    优先级：当前目录 > 系统PATH
    
    Returns:
        str: FFmpeg完整路径，未找到返回None
    """
    # Windows下优先找ffmpeg.exe
    ffmpeg_names = ['ffmpeg.exe', 'ffmpeg'] if sys.platform == 'win32' else ['ffmpeg']

    local_ffmpeg = _find_local_binary(ffmpeg_names)
    if local_ffmpeg:
        return local_ffmpeg
    
    # 从系统PATH查找
    ffmpeg_path = shutil.which('ffmpeg')
    if ffmpeg_path:
        return ffmpeg_path
    
    return None


def find_ffprobe(ffmpeg_path=None):
    """
    查找ffprobe可执行文件

    Args:
        ffmpeg_path: 已找到的ffmpeg路径，可用于推断同目录的ffprobe

    Returns:
        str: ffprobe完整路径，未找到返回None
    """
    ffprobe_names = ['ffprobe.exe', 'ffprobe'] if sys.platform == 'win32' else ['ffprobe']

    if ffmpeg_path:
        ffmpeg_file = Path(ffmpeg_path)
        sibling_candidates = [
            ffmpeg_file.with_name('ffprobe.exe'),
            ffmpeg_file.with_name('ffprobe'),
        ]
        for candidate in sibling_candidates:
            if candidate.exists():
                return str(candidate)

    local_ffprobe = _find_local_binary(ffprobe_names)
    if local_ffprobe:
        return local_ffprobe

    ffprobe_path = shutil.which('ffprobe')
    if ffprobe_path:
        return ffprobe_path

    return None


def check_ffmpeg_version(ffmpeg_path):
    """
    检查FFmpeg版本
    
    Args:
        ffmpeg_path: FFmpeg路径
        
    Returns:
        str: 版本信息，失败返回None
    """
    try:
        result = subprocess.run(
            [ffmpeg_path, '-version'],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=_creationflags(),
        )
        if result.returncode == 0:
            # 第一行通常是版本信息
            first_line = result.stdout.strip().split('\n')[0]
            return first_line
    except Exception as e:
        print(f"检查FFmpeg版本失败: {e}")
    return None


def check_ffprobe_version(ffprobe_path):
    """
    检查FFprobe版本

    Args:
        ffprobe_path: FFprobe路径

    Returns:
        str: 版本信息，失败返回None
    """
    try:
        result = subprocess.run(
            [ffprobe_path, '-version'],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=_creationflags(),
        )
        if result.returncode == 0:
            return result.stdout.strip().split('\n')[0]
    except Exception as e:
        print(f"检查FFprobe版本失败: {e}")
    return None


def _parse_ffmpeg_duration(stderr_text):
    """从 ffmpeg -i 的 stderr 中提取时长，作为 ffprobe 失败时的兜底方案。"""
    for raw_line in stderr_text.splitlines():
        line = raw_line.strip()
        if "Duration:" not in line:
            continue

        try:
            duration_part = line.split("Duration:", 1)[1].split(",", 1)[0].strip()
            hours, minutes, seconds = duration_part.split(":")
            return float(hours) * 3600 + float(minutes) * 60 + float(seconds)
        except Exception:
            return 0

    return 0


def _fallback_video_info_from_ffmpeg(ffmpeg_path, video_path):
    """使用 ffmpeg 输出作为兜底来源，至少拿到视频时长。"""
    info = {
        'duration': 0,
        'width': 0,
        'height': 0,
        'fps': 0,
        'bitrate': 0,
    }

    try:
        result = subprocess.run(
            [ffmpeg_path, '-i', str(video_path)],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=_creationflags(),
        )
        info['duration'] = _parse_ffmpeg_duration(result.stderr)
    except Exception as e:
        print(f"使用FFmpeg兜底获取视频信息失败: {e}")

    return info


def get_video_info(ffmpeg_path, video_path):
    """
    获取视频信息（时长、分辨率等）
    
    Args:
        ffmpeg_path: FFmpeg路径
        video_path: 视频文件路径
        
    Returns:
        dict: 视频信息字典
    """
    info = {
        'duration': 0,
        'width': 0,
        'height': 0,
        'fps': 0,
        'bitrate': 0
    }
    
    try:
        ffprobe_path = find_ffprobe(ffmpeg_path)

        if ffprobe_path and Path(ffprobe_path).exists():
            cmd = [
                str(ffprobe_path),
                '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'format=duration,bit_rate:stream=width,height,r_frame_rate',
                '-of', 'json',
                str(video_path)
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=_creationflags(),
            )

            if result.returncode == 0 and result.stdout.strip():
                payload = json.loads(result.stdout)

                format_info = payload.get('format', {}) or {}
                stream_info = {}
                streams = payload.get('streams', []) or []
                if streams:
                    stream_info = streams[0] or {}

                duration = format_info.get('duration')
                if duration not in (None, '', 'N/A'):
                    info['duration'] = float(duration)

                width = stream_info.get('width')
                if width not in (None, '', 'N/A'):
                    info['width'] = int(width)

                height = stream_info.get('height')
                if height not in (None, '', 'N/A'):
                    info['height'] = int(height)

                frame_rate = stream_info.get('r_frame_rate')
                if frame_rate not in (None, '', 'N/A'):
                    if '/' in frame_rate:
                        num, den = frame_rate.split('/', 1)
                        if int(den) != 0:
                            info['fps'] = round(int(num) / int(den), 2)
                    else:
                        info['fps'] = float(frame_rate)

                bitrate = format_info.get('bit_rate')
                if bitrate not in (None, '', 'N/A'):
                    info['bitrate'] = int(float(bitrate))

                if info['duration'] > 0:
                    return info

    except Exception as e:
        print(f"获取视频信息失败: {e}")

    return _fallback_video_info_from_ffmpeg(ffmpeg_path, video_path)


def build_thumbnail_command(ffmpeg_path, video_path, output_path):
    """构建从视频第一帧提取预览图的 FFmpeg 命令。"""
    return [
        str(ffmpeg_path),
        '-y',
        '-hide_banner',
        '-loglevel', 'error',
        '-i', str(video_path),
        '-map', '0:v:0',
        '-frames:v', '1',
        '-vf', 'scale=640:-2',
        '-q:v', '3',
        str(output_path),
    ]


def extract_video_thumbnail(ffmpeg_path, video_path, output_path):
    """
    从视频第一帧提取预览图。

    Args:
        ffmpeg_path: FFmpeg路径
        video_path: 视频文件路径
        output_path: 预览图输出路径

    Returns:
        bool: 成功返回 True，失败返回 False
    """
    try:
        cmd = build_thumbnail_command(ffmpeg_path, video_path, output_path)
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            creationflags=_creationflags(),
        )
        return result.returncode == 0 and Path(output_path).exists() and Path(output_path).stat().st_size > 0
    except Exception as e:
        print(f"提取视频预览图失败: {e}")
        return False


def build_ffmpeg_command(ffmpeg_path, input_path, output_path, skip_seconds=0, 
                         resolution=None, video_bitrate=None, audio_bitrate='128k'):
    """
    构建FFmpeg剪辑命令
    
    Args:
        ffmpeg_path: FFmpeg路径
        input_path: 输入视频路径
        output_path: 输出视频路径
        skip_seconds: 跳过的开头秒数
        resolution: 目标分辨率元组 (宽, 高)，None表示保持原分辨率
        video_bitrate: 视频比特率，None表示自动
        audio_bitrate: 音频比特率
        
    Returns:
        list: FFmpeg命令参数列表
    """
    cmd = [ffmpeg_path, '-y', '-i', str(input_path)]
    
    # 跳过开头
    if skip_seconds > 0:
        cmd.extend(['-ss', str(skip_seconds)])
    
    # 视频编码器
    cmd.extend(['-c:v', 'libx264', '-preset', 'medium'])
    
    # 分辨率
    if resolution:
        width, height = resolution
        cmd.extend(['-vf', f'scale={width}:{height}'])
    
    # 视频比特率
    if video_bitrate:
        cmd.extend(['-b:v', video_bitrate])
    
    # 音频
    cmd.extend(['-c:a', 'aac', '-b:a', audio_bitrate])
    
    # 输出
    cmd.append(str(output_path))
    
    return cmd


def format_time(seconds):
    """
    将秒数格式化为时:分:秒
    
    Args:
        seconds: 秒数
        
    Returns:
        str: 格式化的时间字符串
    """
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes}:{secs:02d}"


def format_file_size(size_bytes):
    """
    格式化文件大小
    
    Args:
        size_bytes: 字节数
        
    Returns:
        str: 格式化的大小字符串
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
