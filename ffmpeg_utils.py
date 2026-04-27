"""
FFmpeg工具模块
负责FFmpeg路径检测和命令执行
"""
import json
import os
import sys
import subprocess
import shutil
import tempfile
from pathlib import Path

from edit_model import DeleteRange, EditPlan, OutputOptions
from edit_model import normalize_delete_ranges as normalize_plan_delete_ranges
from subtitle_model import export_subtitle_project_to_ass


_PROCESS_OUTPUT_ENCODINGS = ("utf-8", "utf-8-sig", "gb18030", "gbk")


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


def decode_process_output(data):
    """把子进程输出安全解码成文本，避免 Windows 默认编码导致异常。"""
    if data is None:
        return ""
    if isinstance(data, str):
        return data

    for encoding in _PROCESS_OUTPUT_ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


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
            timeout=10,
            creationflags=_creationflags(),
        )
        if result.returncode == 0:
            # 第一行通常是版本信息
            first_line = decode_process_output(result.stdout).strip().split('\n')[0]
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
            timeout=10,
            creationflags=_creationflags(),
        )
        if result.returncode == 0:
            return decode_process_output(result.stdout).strip().split('\n')[0]
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
        'has_audio': True,
    }

    try:
        result = subprocess.run(
            [ffmpeg_path, '-i', str(video_path)],
            capture_output=True,
            timeout=30,
            creationflags=_creationflags(),
        )
        stderr_text = decode_process_output(result.stderr)
        info['duration'] = _parse_ffmpeg_duration(stderr_text)
        info['has_audio'] = 'Audio:' in stderr_text
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
        'bitrate': 0,
        'has_audio': True,
    }
    
    try:
        ffprobe_path = find_ffprobe(ffmpeg_path)

        if ffprobe_path and Path(ffprobe_path).exists():
            cmd = [
                str(ffprobe_path),
                '-v', 'error',
                '-show_entries', 'format=duration,bit_rate:stream=codec_type,width,height,r_frame_rate',
                '-of', 'json',
                str(video_path)
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=30,
                creationflags=_creationflags(),
            )

            stdout_text = decode_process_output(result.stdout)
            if result.returncode == 0 and stdout_text.strip():
                payload = json.loads(stdout_text)

                format_info = payload.get('format', {}) or {}
                stream_info = {}
                streams = payload.get('streams', []) or []
                video_streams = [stream for stream in streams if stream.get('codec_type') == 'video']
                info['has_audio'] = any(stream.get('codec_type') == 'audio' for stream in streams)
                if video_streams:
                    stream_info = video_streams[0] or {}

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


def normalize_delete_ranges(ranges, total_duration=None):
    """兼容导出：裁剪、排序并合并删除区间。"""
    return normalize_plan_delete_ranges(ranges, total_duration=total_duration)


def _normalize_ranges(ranges, total_duration=None):
    """兼容内部旧调用，统一委托给公开的删除区间归一化函数。"""
    return normalize_delete_ranges(ranges, total_duration)


def calculate_output_duration(total_duration, skip_seconds=0, delete_ranges=None):
    """根据跳过开头和删除区间估算输出时长。"""
    plan = EditPlan(
        skip_seconds=skip_seconds,
        delete_ranges=tuple(DeleteRange(start, end) for start, end in (delete_ranges or [])),
    )
    return plan.output_duration(total_duration)


def _format_filter_number(value):
    """把秒数格式化成 FFmpeg 表达式中稳定的数字字符串。"""
    text = f"{float(value):.3f}".rstrip('0').rstrip('.')
    return text if text else '0'


def _build_keep_expression(skip_seconds=0, delete_ranges=None):
    """构建 FFmpeg select/aselect 使用的保留表达式。"""
    conditions = []
    if skip_seconds > 0:
        conditions.append(f"gte(t,{_format_filter_number(skip_seconds)})")

    for start, end in _normalize_ranges(delete_ranges):
        conditions.append(
            f"not(between(t,{_format_filter_number(start)},{_format_filter_number(end)}))"
        )

    if not conditions:
        return None

    return '*'.join(conditions)


def _build_keep_expression_from_plan(edit_plan, include_skip_without_delete=False):
    """根据规范化后的编辑计划构建 FFmpeg select/aselect 保留表达式。"""
    plan = edit_plan.normalized()
    return _build_keep_expression(
        skip_seconds=plan.skip_seconds if plan.delete_ranges or include_skip_without_delete else 0,
        delete_ranges=plan.delete_range_tuples(),
    )


def _escape_filter_value(value):
    """转义 FFmpeg filter 参数中的路径或样式值。"""
    text = str(value).replace("\\", "/")
    return (
        text.replace("'", "\\'")
        .replace(":", "\\:")
        .replace(",", "\\,")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def _build_subtitles_filter(subtitle_path):
    escaped_path = _escape_filter_value(Path(subtitle_path).resolve())
    return f"subtitles=filename='{escaped_path}'"


def _build_resolution_filter(width, height):
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
    )


def prepare_subtitle_file_for_plan(edit_plan):
    """把 EditPlan 内的字幕写到临时 ASS 文件，返回文件路径；无字幕则返回 None。"""
    track = edit_plan.normalized().subtitles
    if not track.has_entries():
        return None

    fd, subtitle_path = tempfile.mkstemp(prefix="videoclipper_subtitles_", suffix=".ass")
    os.close(fd)
    try:
        export_subtitle_project_to_ass(track, subtitle_path)
        return subtitle_path
    except Exception:
        Path(subtitle_path).unlink(missing_ok=True)
        raise


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
            timeout=30,
            creationflags=_creationflags(),
        )
        return result.returncode == 0 and Path(output_path).exists() and Path(output_path).stat().st_size > 0
    except Exception as e:
        print(f"提取视频预览图失败: {e}")
        return False


def build_ffmpeg_progress_command(cmd):
    """为 FFmpeg 命令补充机器可读进度输出。"""
    if not cmd:
        return []
    return [cmd[0], '-hide_banner', '-loglevel', 'error', '-nostats', '-progress', 'pipe:1', *cmd[1:]]


def _parse_clock_time_seconds(text):
    try:
        hours, minutes, seconds = str(text).strip().split(":")
        return float(hours) * 3600 + float(minutes) * 60 + float(seconds)
    except (TypeError, ValueError):
        return None


def _parse_progress_time_seconds(key, value):
    text = str(value or "").strip()
    if not text:
        return None

    if key in {"out_time_ms", "out_time_us"}:
        try:
            return int(text) / 1_000_000
        except ValueError:
            return None

    if ":" in text:
        return _parse_clock_time_seconds(text)

    try:
        return float(text)
    except ValueError:
        return None


def run_ffmpeg_with_progress(cmd, expected_duration=None, stop_requested=None, progress_callback=None):
    """运行 FFmpeg 并通过 `-progress pipe:1` 回调进度。"""
    progress_cmd = build_ffmpeg_progress_command(cmd)
    process = subprocess.Popen(
        progress_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=_creationflags(),
    )

    cancelled = False
    stderr_text = ""
    try:
        while True:
            if stop_requested and stop_requested():
                cancelled = True
                if process.poll() is None:
                    process.terminate()
                break

            line = process.stdout.readline() if process.stdout is not None else b""
            if not line:
                if process.poll() is not None:
                    break
                continue

            raw_line = decode_process_output(line).strip()
            if "=" not in raw_line:
                continue

            key, value = raw_line.split("=", 1)
            if key not in {"out_time_ms", "out_time_us", "out_time"}:
                continue

            current_seconds = _parse_progress_time_seconds(key, value)
            if current_seconds is None or not expected_duration or expected_duration <= 0:
                continue

            progress = min(int((current_seconds / expected_duration) * 100), 100)
            if progress_callback:
                progress_callback(progress)
    finally:
        if process.stderr is not None:
            stderr_text = decode_process_output(process.stderr.read())
        returncode = process.wait()

    return {
        "returncode": returncode,
        "stderr": stderr_text.strip(),
        "cancelled": cancelled,
    }


def build_ffmpeg_command_from_plan(ffmpeg_path, input_path, output_path, edit_plan, subtitle_path=None):
    """
    根据统一编辑模型构建 FFmpeg 剪辑命令。

    Args:
        ffmpeg_path: FFmpeg路径
        input_path: 输入视频路径
        output_path: 输出视频路径
        edit_plan: EditPlan 编辑计划

    Returns:
        list: FFmpeg命令参数列表
    """
    plan = edit_plan.normalized()
    cmd = [ffmpeg_path, '-y', '-i', str(input_path)]

    has_subtitles = bool(subtitle_path and plan.subtitles.has_entries())
    keep_expression = _build_keep_expression_from_plan(
        plan,
        include_skip_without_delete=has_subtitles,
    )

    # 没有中间删除和字幕时沿用 -ss，避免改变既有极简剪开头行为。
    if plan.skip_seconds > 0 and not plan.delete_ranges and not has_subtitles:
        cmd.extend(['-ss', str(plan.skip_seconds)])

    # 视频编码器
    cmd.extend(['-c:v', 'libx264', '-preset', 'medium'])

    video_filters = []
    if has_subtitles:
        video_filters.append(_build_subtitles_filter(subtitle_path))

    if keep_expression:
        video_filters.extend([f"select='{keep_expression}'", 'setpts=N/FRAME_RATE/TB'])

    if plan.output.resolution:
        width, height = plan.output.resolution
        video_filters.append(_build_resolution_filter(width, height))

    if video_filters:
        cmd.extend(['-vf', ','.join(video_filters)])

    # 视频比特率
    if plan.output.video_bitrate:
        cmd.extend(['-b:v', plan.output.video_bitrate])

    # 音频
    if plan.has_audio:
        if keep_expression:
            cmd.extend(['-af', f"aselect='{keep_expression}',asetpts=N/SR/TB"])
        cmd.extend(['-c:a', 'aac', '-b:a', plan.output.audio_bitrate])
    else:
        cmd.append('-an')
    
    # 输出
    cmd.append(str(output_path))
    
    return cmd


def build_ffmpeg_command(ffmpeg_path, input_path, output_path, skip_seconds=0,
                         resolution=None, video_bitrate=None, audio_bitrate='128k',
                         delete_ranges=None, has_audio=True):
    """
    构建FFmpeg剪辑命令。
    兼容旧调用；新代码优先使用 build_ffmpeg_command_from_plan。
    """
    plan = EditPlan(
        skip_seconds=skip_seconds,
        delete_ranges=tuple(DeleteRange(start, end) for start, end in (delete_ranges or [])),
        output=OutputOptions(
            resolution=resolution,
            video_bitrate=video_bitrate,
            audio_bitrate=audio_bitrate,
        ),
        has_audio=has_audio,
    )
    return build_ffmpeg_command_from_plan(ffmpeg_path, input_path, output_path, plan)


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
