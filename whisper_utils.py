"""
faster-whisper 字幕识别工具。
这里负责模型目录、识别音频准备、转写结果到字幕工程的转换。
"""
import subprocess
import sys
import tempfile
from pathlib import Path

from edit_model import PlanValidationError
from ffmpeg_utils import build_audio_mixdown_command, decode_process_output
from subtitle_model import SubtitleCue, SubtitleProject, build_default_subtitle_project


DEFAULT_WHISPER_MODEL_NAME = "medium"
DEFAULT_WHISPER_MODEL_DIRNAME = "faster-whisper-medium"
DEFAULT_SUBTITLE_STYLE = "short_speech_bottom"


class WhisperError(RuntimeError):
    """语音识别流程失败。"""


def _creationflags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def app_base_dir():
    if getattr(sys, "frozen", False) and getattr(sys, "executable", None):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def default_whisper_model_dir():
    return app_base_dir() / "models" / DEFAULT_WHISPER_MODEL_DIRNAME


def _looks_like_whisper_model(path):
    model_dir = Path(path)
    return (model_dir / "model.bin").exists() and (model_dir / "config.json").exists()


def ensure_whisper_model(model_dir=None, model_name=DEFAULT_WHISPER_MODEL_NAME, download_fn=None, status_callback=None):
    target_dir = Path(model_dir) if model_dir is not None else default_whisper_model_dir()
    if _looks_like_whisper_model(target_dir):
        return target_dir

    target_dir.mkdir(parents=True, exist_ok=True)
    if status_callback:
        status_callback("正在下载字幕识别模型...")

    if download_fn is None:
        try:
            from faster_whisper.utils import download_model
        except ImportError as exc:
            raise WhisperError("缺少 faster-whisper，请先安装依赖。") from exc
        download_fn = download_model

    try:
        downloaded_path = download_fn(model_name, output_dir=str(target_dir))
    except TypeError:
        downloaded_path = download_fn(model_name, cache_dir=str(target_dir))
    except Exception as exc:
        raise WhisperError(f"字幕识别模型下载失败: {exc}") from exc

    candidate = Path(downloaded_path) if downloaded_path else target_dir
    if _looks_like_whisper_model(target_dir):
        return target_dir
    if _looks_like_whisper_model(candidate):
        return candidate

    raise WhisperError(f"字幕识别模型不完整: {target_dir}")


def load_whisper_model(model_dir=None, model_factory=None, status_callback=None):
    model_path = ensure_whisper_model(model_dir=model_dir, status_callback=status_callback)
    if status_callback:
        status_callback("正在加载字幕识别模型...")

    if model_factory is None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise WhisperError("缺少 faster-whisper，请先安装依赖。") from exc
        model_factory = WhisperModel

    try:
        return model_factory(str(model_path), device="cpu", compute_type="int8")
    except Exception as exc:
        raise WhisperError(f"字幕识别模型加载失败: {exc}") from exc


def _segment_value(segment, name, default=None):
    if isinstance(segment, dict):
        return segment.get(name, default)
    return getattr(segment, name, default)


def _normalize_segment(segment):
    try:
        start = max(0.0, float(_segment_value(segment, "start", 0)))
        end = max(0.0, float(_segment_value(segment, "end", 0)))
    except (TypeError, ValueError):
        return None

    text = str(_segment_value(segment, "text", "") or "").strip()
    if not text or end <= start:
        return None
    return {"start": start, "end": end, "text": text}


def _normalized_segments(segments):
    normalized = []
    for segment in segments or ():
        item = _normalize_segment(segment)
        if item is not None:
            normalized.append(item)
    return sorted(normalized, key=lambda item: (item["start"], item["end"], item["text"]))


def _overlap_seconds(left, right):
    return max(0.0, min(left["end"], right["end"]) - max(left["start"], right["start"]))


def _matching_translation_text(segment, translated_segments):
    best = None
    best_overlap = 0.0
    midpoint = (segment["start"] + segment["end"]) / 2

    for translated in translated_segments:
        overlap = _overlap_seconds(segment, translated)
        if overlap > best_overlap:
            best = translated
            best_overlap = overlap

    if best is None:
        for translated in translated_segments:
            if translated["start"] <= midpoint <= translated["end"]:
                best = translated
                break

    return best["text"] if best else ""


def segments_to_subtitle_project(
    segments,
    translated_segments=None,
    source_language=None,
    bilingual=True,
    video_size=None,
    default_preset_id=DEFAULT_SUBTITLE_STYLE,
):
    base = build_default_subtitle_project(video_size=video_size, preset_id=default_preset_id)
    original_segments = _normalized_segments(segments)
    translations = _normalized_segments(translated_segments)
    source_language = str(source_language or "").lower()
    use_bilingual = bool(bilingual and translations and not source_language.startswith("en"))

    cues = []
    for segment in original_segments:
        text = segment["text"]
        if use_bilingual:
            translated_text = _matching_translation_text(segment, translations)
            if translated_text and translated_text.lower() != text.lower():
                text = f"{text}\n{translated_text}"
        cues.append(
            SubtitleCue(
                start=segment["start"],
                end=segment["end"],
                text=text,
                style_name=base.default_style_name,
                source_kind="whisper",
            ).normalized()
        )

    if not cues:
        raise WhisperError("没有识别到可用字幕。")

    return SubtitleProject(
        cues=tuple(cues),
        styles=base.styles,
        script_info=base.script_info,
        enabled=True,
        play_res_x=base.play_res_x,
        play_res_y=base.play_res_y,
        default_style_name=base.default_style_name,
    ).normalized()


def _run_audio_mixdown(cmd):
    result = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=_creationflags(),
    )
    if result.returncode != 0:
        stderr_text = decode_process_output(result.stderr).strip()
        detail = stderr_text.splitlines()[0] if stderr_text else f"返回码 {result.returncode}"
        raise WhisperError(f"识别音频准备失败: {detail}")


def _run_transcribe(model, audio_path, task):
    segments, info = model.transcribe(
        str(audio_path),
        task=task,
        beam_size=5,
        vad_filter=True,
        word_timestamps=False,
        condition_on_previous_text=False,
    )
    return list(segments), info


def transcribe_video_to_project(
    ffmpeg_path,
    input_path,
    edit_plan,
    video_size=None,
    duration=None,
    bilingual=True,
    model_dir=None,
    model_factory=None,
    status_callback=None,
    progress_callback=None,
    stop_requested=None,
):
    def ensure_not_stopped():
        if stop_requested and stop_requested():
            raise WhisperError("字幕识别已取消。")

    try:
        with tempfile.TemporaryDirectory(prefix="videoclipper_whisper_") as work_dir:
            ensure_not_stopped()
            audio_path = Path(work_dir) / "speech.wav"
            if status_callback:
                status_callback("正在准备识别音频...")
            cmd = build_audio_mixdown_command(ffmpeg_path, input_path, audio_path, edit_plan, duration=duration)
            _run_audio_mixdown(cmd)
            if progress_callback:
                progress_callback(15)

            ensure_not_stopped()
            model = load_whisper_model(model_dir=model_dir, model_factory=model_factory, status_callback=status_callback)
            if progress_callback:
                progress_callback(35)

            ensure_not_stopped()
            if status_callback:
                status_callback("正在识别字幕...")
            original_segments, info = _run_transcribe(model, audio_path, task="transcribe")
            source_language = str(getattr(info, "language", "") or "")
            if progress_callback:
                progress_callback(75)

            translated_segments = []
            if bilingual and not source_language.lower().startswith("en"):
                ensure_not_stopped()
                if status_callback:
                    status_callback("正在生成双语字幕...")
                translated_segments, _translation_info = _run_transcribe(model, audio_path, task="translate")

            project = segments_to_subtitle_project(
                original_segments,
                translated_segments=translated_segments,
                source_language=source_language,
                bilingual=bilingual,
                video_size=video_size,
            )
            if progress_callback:
                progress_callback(100)
            return project
    except PlanValidationError:
        raise
    except WhisperError:
        raise
    except Exception as exc:
        raise WhisperError(f"字幕识别失败: {exc}") from exc
