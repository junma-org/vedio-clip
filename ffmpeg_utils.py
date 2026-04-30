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

from edit_model import DeleteRange, EditPlan, OutputOptions, PlanValidationError
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


def _audible_audio_tracks(edit_plan):
    return tuple(track for track in edit_plan.normalized().audio_tracks if track.volume > 0)


def _audio_filter_chain(input_label, output_label, keep_expression=None, volume=None):
    filters = []
    if keep_expression:
        filters.extend([f"aselect='{keep_expression}'", "asetpts=N/SR/TB"])
    if volume is not None:
        filters.append(f"volume={_format_filter_number(volume)}")
    if not filters:
        filters.append("anull")
    return f"[{input_label}]{','.join(filters)}[{output_label}]"


def _build_audio_mix_filter(
    source_audio_enabled,
    audio_tracks,
    keep_expression=None,
    output_duration=None,
    first_audio_input_index=1,
):
    filter_parts = []
    labels = []

    if source_audio_enabled:
        output_label = f"a{len(labels)}"
        filter_parts.append(_audio_filter_chain("0:a:0", output_label, keep_expression=keep_expression))
        labels.append(output_label)

    for track_index, track in enumerate(audio_tracks, start=first_audio_input_index):
        output_label = f"a{len(labels)}"
        filter_parts.append(
            _audio_filter_chain(
                f"{track_index}:a:0",
                output_label,
                keep_expression=keep_expression,
                volume=track.volume,
            )
        )
        labels.append(output_label)

    if not labels:
        return None

    tail_filters = []
    if output_duration is not None and output_duration > 0:
        tail_filters.extend([f"atrim=0:{_format_filter_number(output_duration)}", "asetpts=N/SR/TB"])

    if len(labels) == 1:
        tail_filters = tail_filters or ["anull"]
        filter_parts.append(f"[{labels[0]}]{','.join(tail_filters)}[aout]")
    else:
        mix_filter = f"amix=inputs={len(labels)}:duration=longest:dropout_transition=0:normalize=0"
        if tail_filters:
            mix_filter = f"{mix_filter},{','.join(tail_filters)}"
        filter_parts.append("".join(f"[{label}]" for label in labels) + f"{mix_filter}[aout]")

    return ";".join(filter_parts)


def _removed_duration_before(seconds, delete_ranges):
    removed = 0.0
    for start, end in _normalize_ranges(delete_ranges):
        if seconds <= start:
            break
        removed += max(0.0, min(seconds, end) - start)
    return removed


def _source_time_to_output_time(seconds, delete_ranges):
    return max(0.0, float(seconds) - _removed_duration_before(float(seconds), delete_ranges))


def _kept_segments_for_range(start, end, delete_ranges):
    cursor = float(start)
    limit = float(end)
    segments = []
    for delete_start, delete_end in _normalize_ranges(delete_ranges):
        if delete_end <= cursor:
            continue
        if delete_start >= limit:
            break
        if delete_start > cursor:
            segments.append((cursor, min(delete_start, limit)))
        cursor = max(cursor, delete_end)
        if cursor >= limit:
            break
    if cursor < limit:
        segments.append((cursor, limit))
    return [(segment_start, segment_end) for segment_start, segment_end in segments if segment_end > segment_start]


def _overlay_segments_for_clip(clip, delete_ranges):
    segments = []
    for source_start, source_end in _kept_segments_for_range(clip.start, clip.end, delete_ranges):
        output_start = _source_time_to_output_time(source_start, delete_ranges)
        output_end = _source_time_to_output_time(source_end, delete_ranges)
        if output_end <= output_start:
            continue
        media_source_start = clip.source_start + (source_start - clip.start)
        media_source_end = media_source_start + (source_end - source_start)
        segments.append((output_start, output_end, media_source_start, media_source_end))
    return segments


def _build_overlay_video_filter_parts(edit_plan, subtitle_path=None, keep_expression=None):
    plan = edit_plan.normalized()
    filter_parts = []
    base_filters = []
    if keep_expression:
        base_filters.extend([f"select='{keep_expression}'", 'setpts=N/FRAME_RATE/TB'])
    if plan.output.resolution:
        width, height = plan.output.resolution
        base_filters.append(_build_resolution_filter(width, height))

    current_label = "vbase0"
    filter_parts.append(f"[0:v:0]{','.join(base_filters or ['null'])}[{current_label}]")

    delete_ranges = plan.delete_range_tuples()
    overlay_counter = 0
    for input_index, clip in enumerate(plan.media_overlays, start=1):
        for segment_index, (output_start, output_end, source_start, source_end) in enumerate(
            _overlay_segments_for_clip(clip, delete_ranges),
            start=1,
        ):
            duration = output_end - output_start
            raw_label = f"ovraw{input_index}_{segment_index}"
            fit_label = f"ovfit{input_index}_{segment_index}"
            ref_label = f"vref{input_index}_{segment_index}"
            next_label = f"vov{overlay_counter + 1}"
            if clip.media_kind == "video":
                input_filters = [
                    f"trim=start={_format_filter_number(source_start)}:end={_format_filter_number(source_end)}",
                    f"setpts=PTS-STARTPTS+{_format_filter_number(output_start)}/TB",
                    "format=rgba",
                ]
            else:
                input_filters = [
                    f"trim=duration={_format_filter_number(duration)}",
                    f"setpts=PTS-STARTPTS+{_format_filter_number(output_start)}/TB",
                    "format=rgba",
                ]

            filter_parts.append(f"[{input_index}:v:0]{','.join(input_filters)}[{raw_label}]")
            filter_parts.append(
                f"[{raw_label}][{current_label}]scale2ref=w=main_w:h=main_h[{fit_label}][{ref_label}]"
            )
            filter_parts.append(
                f"[{ref_label}][{fit_label}]overlay=x=0:y=0:"
                f"enable='between(t,{_format_filter_number(output_start)},{_format_filter_number(output_end)})':"
                f"eof_action=pass:shortest=0[{next_label}]"
            )
            current_label = next_label
            overlay_counter += 1

    if subtitle_path and plan.subtitles.has_entries():
        output_label = "vout"
        filter_parts.append(f"[{current_label}]{_build_subtitles_filter(subtitle_path)}[{output_label}]")
        return filter_parts, output_label

    return filter_parts, current_label


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


def build_ffmpeg_command_from_plan(
    ffmpeg_path,
    input_path,
    output_path,
    edit_plan,
    subtitle_path=None,
    output_duration=None,
):
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
    audio_tracks = _audible_audio_tracks(plan)
    media_overlays = plan.media_overlays
    cmd = [ffmpeg_path, '-y', '-i', str(input_path)]
    for clip in media_overlays:
        if clip.media_kind == "image":
            cmd.extend(['-loop', '1', '-t', _format_filter_number(clip.duration)])
        cmd.extend(['-i', clip.path])
    for track in audio_tracks:
        cmd.extend(['-i', track.path])

    has_subtitles = bool(subtitle_path and plan.subtitles.has_entries())
    uses_complex_video = bool(media_overlays)
    uses_complex_audio = bool(audio_tracks)
    keep_expression = _build_keep_expression_from_plan(
        plan,
        include_skip_without_delete=has_subtitles or uses_complex_audio or uses_complex_video,
    )

    # 没有中间删除和字幕时沿用 -ss，避免改变既有极简剪开头行为。
    if (
        plan.skip_seconds > 0
        and not plan.delete_ranges
        and not has_subtitles
        and not uses_complex_audio
        and not uses_complex_video
    ):
        cmd.extend(['-ss', str(plan.skip_seconds)])

    # 视频编码器
    cmd.extend(['-c:v', 'libx264', '-preset', 'medium'])

    filter_complex_parts = []
    video_output_label = None
    if uses_complex_video:
        video_parts, video_output_label = _build_overlay_video_filter_parts(
            plan,
            subtitle_path=subtitle_path if has_subtitles else None,
            keep_expression=keep_expression,
        )
        filter_complex_parts.extend(video_parts)
    else:
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
    audio_output_label = None
    if uses_complex_audio:
        filter_complex = _build_audio_mix_filter(
            plan.source_audio_enabled(),
            audio_tracks,
            keep_expression=keep_expression,
            output_duration=output_duration,
            first_audio_input_index=1 + len(media_overlays),
        )
        if filter_complex:
            filter_complex_parts.append(filter_complex)
            audio_output_label = "aout"
            cmd.extend(['-c:a', 'aac', '-b:a', plan.output.audio_bitrate])
        else:
            cmd.append('-an')
    elif plan.source_audio_enabled():
        if keep_expression:
            cmd.extend(['-af', f"aselect='{keep_expression}',asetpts=N/SR/TB"])
        cmd.extend(['-c:a', 'aac', '-b:a', plan.output.audio_bitrate])
    else:
        cmd.append('-an')

    if filter_complex_parts:
        cmd.extend(['-filter_complex', ';'.join(filter_complex_parts)])

    if video_output_label:
        cmd.extend(['-map', f'[{video_output_label}]'])
        if audio_output_label:
            cmd.extend(['-map', f'[{audio_output_label}]'])
        elif plan.source_audio_enabled():
            cmd.extend(['-map', '0:a:0?'])
    elif audio_output_label:
        cmd.extend(['-map', '0:v:0', '-map', f'[{audio_output_label}]'])
    
    # 输出
    cmd.append(str(output_path))
    
    return cmd


def build_ffmpeg_command(ffmpeg_path, input_path, output_path, skip_seconds=0,
                         resolution=None, video_bitrate=None, audio_bitrate='128k',
                         delete_ranges=None, has_audio=True, source_audio_muted=False,
                         audio_tracks=None, media_overlays=None, output_duration=None):
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
        source_audio_muted=source_audio_muted,
        audio_tracks=tuple(audio_tracks or ()),
        media_overlays=tuple(media_overlays or ()),
    )
    return build_ffmpeg_command_from_plan(
        ffmpeg_path,
        input_path,
        output_path,
        plan,
        output_duration=output_duration,
    )


def build_audio_mixdown_command(
    ffmpeg_path,
    input_path,
    output_path,
    edit_plan,
    duration=None,
    sample_rate=16000,
):
    """构建识别用音频混音命令，输出单声道 PCM WAV。"""
    plan = edit_plan.normalized()
    audio_tracks = _audible_audio_tracks(plan)
    source_audio_enabled = plan.source_audio_enabled()
    if not source_audio_enabled and not audio_tracks:
        raise PlanValidationError("当前没有可识别的音频。")

    cmd = [str(ffmpeg_path), '-y', '-i', str(input_path)]
    for track in audio_tracks:
        cmd.extend(['-i', track.path])

    if audio_tracks:
        filter_complex = _build_audio_mix_filter(
            source_audio_enabled,
            audio_tracks,
            output_duration=duration,
        )
        if not filter_complex:
            raise PlanValidationError("当前没有可识别的音频。")
        cmd.extend(['-filter_complex', filter_complex, '-map', '[aout]'])
    elif source_audio_enabled:
        cmd.extend(['-map', '0:a:0'])

    if duration is not None and duration > 0:
        cmd.extend(['-t', _format_filter_number(duration)])

    cmd.extend(['-vn', '-ac', '1', '-ar', str(int(sample_rate)), '-c:a', 'pcm_s16le', str(output_path)])
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
