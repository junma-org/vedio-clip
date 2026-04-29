"""
字幕模型与 ASS/SRT 读写。
内部统一使用接近 ASS 的工程模型，GUI 与 FFmpeg 只依赖这里提供的纯数据接口。
"""
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
import re

try:
    import pysubs2
except ImportError:  # pragma: no cover - exercised in minimal local environments
    pysubs2 = None


class SubtitleValidationError(ValueError):
    """字幕参数不合法。"""


_SRT_TIME_RE = re.compile(
    r"(?P<start>\d{1,3}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*"
    r"(?P<end>\d{1,3}:\d{2}:\d{2}[,.]\d{1,3})"
)
_ASS_SECTION_RE = re.compile(r"^\[(?P<section>[^\]]+)\]\s*$")
_ASS_TAG_RE = re.compile(r"\{[^}]*\}")
_ASS_LEADING_TAG_RE = re.compile(r"^(?P<tags>(?:\{[^}]*\})+)")
_ASS_TAG_BODY_RE = re.compile(r"\{(?P<body>[^}]*)\}")
_ASS_FADE_COMMAND_RE = re.compile(r"\\fad\s*\(\s*(?P<in>\d+)\s*,\s*(?P<out>\d+)\s*\)")
_ASS_TIME_RE = re.compile(r"^\d+:\d{2}:\d{2}\.\d{2}$")

_ASS_STYLE_FIELDS = [
    "Name",
    "Fontname",
    "Fontsize",
    "PrimaryColour",
    "SecondaryColour",
    "OutlineColour",
    "BackColour",
    "Bold",
    "Italic",
    "Underline",
    "StrikeOut",
    "ScaleX",
    "ScaleY",
    "Spacing",
    "Angle",
    "BorderStyle",
    "Outline",
    "Shadow",
    "Alignment",
    "MarginL",
    "MarginR",
    "MarginV",
    "Encoding",
]
_ASS_EVENT_FIELDS = [
    "Layer",
    "Start",
    "End",
    "Style",
    "Name",
    "MarginL",
    "MarginR",
    "MarginV",
    "Effect",
    "Text",
]


def _as_float(value, field_name):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise SubtitleValidationError(f"{field_name}必须是有效数字。")

    if not isfinite(number):
        raise SubtitleValidationError(f"{field_name}必须是有限数字。")

    return number


def _as_int(value, field_name):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise SubtitleValidationError(f"{field_name}必须是整数。")
    return number


def _validate_seconds(value, field_name):
    number = _as_float(value, field_name)
    if number < 0:
        raise SubtitleValidationError(f"{field_name}不能小于 0。")
    return number


def _normalize_text(text):
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def _coerce_script_info(script_info):
    normalized = []
    for item in script_info or ():
        try:
            key, value = item
        except (TypeError, ValueError):
            continue
        normalized.append((str(key).strip(), str(value).strip()))
    return tuple(normalized)


def _clip_to_duration(seconds, total_duration):
    if total_duration is None:
        return seconds

    try:
        duration = float(total_duration)
    except (TypeError, ValueError):
        return seconds

    if not isfinite(duration) or duration <= 0:
        return seconds

    return max(0, min(seconds, duration))


def detect_subtitle_format(text, source_hint=None):
    hint = str(source_hint or "").lower().strip()
    if hint.endswith(".ass") or hint == "ass":
        return "ass"
    if hint.endswith(".srt") or hint == "srt":
        return "srt"

    content = str(text or "").lstrip("\ufeff").strip()
    if not content:
        raise SubtitleValidationError("字幕内容为空。")

    if "[Script Info]" in content or "Dialogue:" in content or "Style:" in content:
        return "ass"
    if _SRT_TIME_RE.search(content):
        return "srt"

    raise SubtitleValidationError("无法识别字幕格式，请提供 ASS 或 SRT。")


def _decode_subtitle_bytes(data, source_hint=None):
    encodings = ["utf-8-sig", "utf-8", "utf-16", "utf-16-le", "utf-16-be", "gbk"]
    if str(source_hint or "").lower().endswith(".ass"):
        encodings = ["utf-8-sig", "utf-8", "utf-16", "utf-16-le", "utf-16-be", "gbk"]

    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue

    raise SubtitleValidationError("字幕文件编码不支持，请使用 UTF-8、UTF-16 或 GBK。")


def parse_srt_timestamp(text):
    value = str(text).strip().replace(",", ".")
    try:
        hours, minutes, seconds = value.split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (TypeError, ValueError):
        raise SubtitleValidationError(f"无法解析字幕时间: {text}")


def format_srt_timestamp(seconds):
    seconds = _validate_seconds(seconds, "字幕秒数")
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_seconds = total_ms // 1000
    secs = total_seconds % 60
    total_minutes = total_seconds // 60
    minutes = total_minutes % 60
    hours = total_minutes // 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def parse_ass_timestamp(text):
    value = str(text).strip()
    if not _ASS_TIME_RE.match(value):
        raise SubtitleValidationError(f"无法解析 ASS 时间: {text}")
    hours, minutes, seconds = value.split(":")
    secs, centiseconds = seconds.split(".")
    return int(hours) * 3600 + int(minutes) * 60 + int(secs) + int(centiseconds) / 100


def format_ass_timestamp(seconds):
    seconds = _validate_seconds(seconds, "字幕秒数")
    total_centiseconds = int(round(seconds * 100))
    centiseconds = total_centiseconds % 100
    total_seconds = total_centiseconds // 100
    secs = total_seconds % 60
    total_minutes = total_seconds // 60
    minutes = total_minutes % 60
    hours = total_minutes // 60
    return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"


def ass_text_to_plain_text(text):
    content = str(text or "")
    content = content.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
    return _normalize_text(_ASS_TAG_RE.sub("", content))


def extract_leading_ass_tags(text):
    content = str(text or "")
    match = _ASS_LEADING_TAG_RE.match(content)
    return match.group("tags") if match else ""


def extract_fade_from_tags(raw_tags):
    tags = extract_leading_ass_tags(raw_tags)
    match = _ASS_FADE_COMMAND_RE.search(tags)
    if not match:
        return None
    return int(match.group("in")), int(match.group("out"))


def strip_fade_from_tags(raw_tags):
    tags = extract_leading_ass_tags(raw_tags)
    if not tags:
        return ""

    cleaned_tags = []
    for match in _ASS_TAG_BODY_RE.finditer(tags):
        body = _ASS_FADE_COMMAND_RE.sub("", match.group("body"))
        if body:
            cleaned_tags.append(f"{{{body}}}")
    return "".join(cleaned_tags)


def set_fade_on_tags(raw_tags, fade_in_ms=None, fade_out_ms=None):
    tags = strip_fade_from_tags(raw_tags)
    if fade_in_ms is None or fade_out_ms is None:
        return tags

    fade_in = _as_int(fade_in_ms, "字幕渐显时长")
    fade_out = _as_int(fade_out_ms, "字幕渐隐时长")
    if fade_in < 0 or fade_out < 0:
        raise SubtitleValidationError("字幕渐隐渐显时长不能小于 0。")
    return f"{{\\fad({fade_in},{fade_out})}}{tags}"


def plain_text_to_ass_text(text, raw_tags=""):
    content = _normalize_text(text)
    if not content:
        raise SubtitleValidationError("字幕文本不能为空。")
    prefix = str(raw_tags or "")
    escaped = content.replace("\n", "\\N")
    return f"{prefix}{escaped}"


def _normalize_ass_color(value, fallback):
    text = str(value or "").strip()
    if not text:
        return fallback

    if text.startswith("&H"):
        digits = text[2:]
    else:
        digits = text

    digits = digits.upper()
    if not re.fullmatch(r"[0-9A-F]{6,8}", digits):
        return fallback
    digits = digits.rjust(8, "0")
    return f"&H{digits}"


def _ass_color_to_rgba(value, fallback="&H00FFFFFF"):
    text = _normalize_ass_color(value, fallback)
    digits = text[2:]
    alpha = int(digits[0:2], 16)
    blue = int(digits[2:4], 16)
    green = int(digits[4:6], 16)
    red = int(digits[6:8], 16)
    return red, green, blue, alpha


def _rgba_to_ass_color(color, fallback="&H00FFFFFF"):
    if color is None:
        return fallback
    try:
        red = int(color.r)
        green = int(color.g)
        blue = int(color.b)
        alpha = int(color.a)
    except (AttributeError, TypeError, ValueError):
        return fallback
    return f"&H{alpha:02X}{blue:02X}{green:02X}{red:02X}"


def _style_from_pysubs2(name, style):
    return SubtitleStyleDef(
        name=name,
        font_name=style.fontname,
        font_size=int(round(style.fontsize)),
        primary_color=_rgba_to_ass_color(style.primarycolor, "&H00FFFFFF"),
        secondary_color=_rgba_to_ass_color(style.secondarycolor, "&H000000FF"),
        outline_color=_rgba_to_ass_color(style.outlinecolor, "&H00000000"),
        back_color=_rgba_to_ass_color(style.backcolor, "&H64000000"),
        bold=bool(style.bold),
        italic=bool(style.italic),
        underline=bool(style.underline),
        strike_out=bool(style.strikeout),
        scale_x=int(round(style.scalex)),
        scale_y=int(round(style.scaley)),
        spacing=int(round(style.spacing)),
        angle=int(round(style.angle)),
        border_style=int(style.borderstyle),
        outline=float(style.outline),
        shadow=float(style.shadow),
        alignment=int(style.alignment),
        margin_l=int(style.marginl),
        margin_r=int(style.marginr),
        margin_v=int(style.marginv),
        encoding=int(style.encoding),
    ).normalized()


def _style_to_pysubs2(style):
    if pysubs2 is None:
        return None

    normalized = style.normalized()
    primary = _ass_color_to_rgba(normalized.primary_color, "&H00FFFFFF")
    secondary = _ass_color_to_rgba(normalized.secondary_color, "&H000000FF")
    outline = _ass_color_to_rgba(normalized.outline_color, "&H00000000")
    back = _ass_color_to_rgba(normalized.back_color, "&H64000000")
    return pysubs2.SSAStyle(
        fontname=normalized.font_name,
        fontsize=float(normalized.font_size),
        primarycolor=pysubs2.Color(*primary),
        secondarycolor=pysubs2.Color(*secondary),
        outlinecolor=pysubs2.Color(*outline),
        backcolor=pysubs2.Color(*back),
        bold=normalized.bold,
        italic=normalized.italic,
        underline=normalized.underline,
        strikeout=normalized.strike_out,
        scalex=float(normalized.scale_x),
        scaley=float(normalized.scale_y),
        spacing=float(normalized.spacing),
        angle=float(normalized.angle),
        borderstyle=normalized.border_style,
        outline=normalized.outline,
        shadow=normalized.shadow,
        alignment=normalized.alignment,
        marginl=normalized.margin_l,
        marginr=normalized.margin_r,
        marginv=normalized.margin_v,
        encoding=normalized.encoding,
    )


@dataclass(frozen=True)
class SubtitleCue:
    start: float
    end: float
    text: str
    style_name: str = "short_speech_bottom"
    source_kind: str = "manual"
    raw_tags: str = ""
    raw_text: str = ""
    layer: int = 0

    def normalized(self):
        start = _validate_seconds(self.start, "字幕开始秒数")
        end = _validate_seconds(self.end, "字幕结束秒数")
        text = _normalize_text(self.text)
        if end <= start:
            raise SubtitleValidationError("字幕结束秒数必须大于开始秒数。")
        if not text:
            raise SubtitleValidationError("字幕文本不能为空。")

        style_name = str(self.style_name or "short_speech_bottom").strip() or "short_speech_bottom"
        source_kind = str(self.source_kind or "manual").strip() or "manual"
        raw_tags = extract_leading_ass_tags(self.raw_tags)
        raw_text = str(self.raw_text or "").replace("\r\n", "\n").replace("\r", "\n")

        return SubtitleCue(
            start=start,
            end=end,
            text=text,
            style_name=style_name,
            source_kind=source_kind,
            raw_tags=raw_tags,
            raw_text=raw_text,
            layer=_as_int(self.layer, "字幕图层"),
        )

    def as_tuple(self):
        cue = self.normalized()
        return (cue.start, cue.end, cue.text)

    def to_ass_text(self):
        cue = self.normalized()
        if cue.raw_text:
            raw_plain = ass_text_to_plain_text(cue.raw_text)
            if raw_plain == cue.text:
                return cue.raw_text.replace("\n", "\\N")
        return plain_text_to_ass_text(cue.text, raw_tags=cue.raw_tags)


@dataclass(frozen=True)
class SubtitleStyleDef:
    name: str = "short_speech_bottom"
    font_name: str = "Microsoft YaHei"
    font_size: int = 38
    primary_color: str = "&H00FFFFFF"
    secondary_color: str = "&H000000FF"
    outline_color: str = "&H00000000"
    back_color: str = "&H64000000"
    bold: bool = True
    italic: bool = False
    underline: bool = False
    strike_out: bool = False
    scale_x: int = 100
    scale_y: int = 100
    spacing: int = 0
    angle: int = 0
    border_style: int = 1
    outline: float = 2.0
    shadow: float = 0.0
    alignment: int = 2
    margin_l: int = 60
    margin_r: int = 60
    margin_v: int = 72
    encoding: int = 1

    def normalized(self):
        name = str(self.name or "").strip()
        if not name:
            raise SubtitleValidationError("字幕样式名不能为空。")

        font_name = str(self.font_name or "").strip() or "Microsoft YaHei"
        font_size = _as_int(self.font_size, "字幕字号")
        margin_l = _as_int(self.margin_l, "字幕左边距")
        margin_r = _as_int(self.margin_r, "字幕右边距")
        margin_v = _as_int(self.margin_v, "字幕垂直边距")
        alignment = _as_int(self.alignment, "字幕对齐方式")
        scale_x = _as_int(self.scale_x, "字幕横向缩放")
        scale_y = _as_int(self.scale_y, "字幕纵向缩放")
        spacing = _as_int(self.spacing, "字幕字距")
        angle = _as_int(self.angle, "字幕角度")
        border_style = _as_int(self.border_style, "字幕边框样式")
        encoding = _as_int(self.encoding, "字幕编码")

        if font_size <= 0:
            raise SubtitleValidationError("字幕字号必须大于 0。")
        if margin_l < 0 or margin_r < 0 or margin_v < 0:
            raise SubtitleValidationError("字幕边距不能小于 0。")
        if alignment not in range(1, 10):
            raise SubtitleValidationError("字幕对齐方式必须在 1 到 9 之间。")

        return SubtitleStyleDef(
            name=name,
            font_name=font_name,
            font_size=font_size,
            primary_color=_normalize_ass_color(self.primary_color, "&H00FFFFFF"),
            secondary_color=_normalize_ass_color(self.secondary_color, "&H000000FF"),
            outline_color=_normalize_ass_color(self.outline_color, "&H00000000"),
            back_color=_normalize_ass_color(self.back_color, "&H64000000"),
            bold=bool(self.bold),
            italic=bool(self.italic),
            underline=bool(self.underline),
            strike_out=bool(self.strike_out),
            scale_x=scale_x,
            scale_y=scale_y,
            spacing=spacing,
            angle=angle,
            border_style=border_style,
            outline=float(_as_float(self.outline, "字幕描边")),
            shadow=float(_as_float(self.shadow, "字幕阴影")),
            alignment=alignment,
            margin_l=margin_l,
            margin_r=margin_r,
            margin_v=margin_v,
            encoding=encoding,
        )

    @property
    def bottom_margin(self):
        return self.margin_v


@dataclass(frozen=True)
class SubtitleProject:
    cues: tuple = field(default_factory=tuple)
    styles: tuple = field(default_factory=tuple)
    script_info: tuple = field(default_factory=tuple)
    enabled: bool = True
    play_res_x: int = 1920
    play_res_y: int = 1080
    default_style_name: str = "short_speech_bottom"

    def normalized(self):
        play_res_x = _as_int(self.play_res_x, "字幕脚本宽度")
        play_res_y = _as_int(self.play_res_y, "字幕脚本高度")
        if play_res_x <= 0 or play_res_y <= 0:
            raise SubtitleValidationError("字幕脚本分辨率必须大于 0。")

        styles = []
        for style in self.styles or ():
            styles.append(_coerce_style(style).normalized())
        if not styles:
            styles.append(build_style_preset(self.default_style_name, (play_res_x, play_res_y)))

        deduped_styles = []
        seen = set()
        for style in styles:
            if style.name in seen:
                continue
            deduped_styles.append(style)
            seen.add(style.name)

        default_style_name = str(self.default_style_name or deduped_styles[0].name).strip()
        if default_style_name not in seen:
            default_style_name = deduped_styles[0].name

        if not self.enabled:
            return SubtitleProject(
                cues=tuple(),
                styles=tuple(deduped_styles),
                script_info=_coerce_script_info(self.script_info),
                enabled=False,
                play_res_x=play_res_x,
                play_res_y=play_res_y,
                default_style_name=default_style_name,
            )

        cues = []
        for cue in self.cues or ():
            normalized = _coerce_cue(cue).normalized()
            if normalized.style_name not in seen:
                normalized = SubtitleCue(
                    start=normalized.start,
                    end=normalized.end,
                    text=normalized.text,
                    style_name=default_style_name,
                    source_kind=normalized.source_kind,
                    raw_tags=normalized.raw_tags,
                    raw_text=normalized.raw_text,
                    layer=normalized.layer,
                )
            cues.append(normalized)

        cues.sort(key=lambda item: (item.start, item.end, item.layer, item.text))
        return SubtitleProject(
            cues=tuple(cues),
            styles=tuple(deduped_styles),
            script_info=_coerce_script_info(self.script_info),
            enabled=True,
            play_res_x=play_res_x,
            play_res_y=play_res_y,
            default_style_name=default_style_name,
        )

    def has_entries(self):
        return bool(self.enabled and self.cues)

    @property
    def entries(self):
        return self.cues

    @property
    def style(self):
        project = self.normalized()
        style_map = project.style_map()
        return style_map.get(project.default_style_name, project.styles[0])

    def style_map(self):
        project = self.normalized()
        return {style.name: style for style in project.styles}

    def script_info_dict(self):
        return dict(self.normalized().script_info)

    def active_cues_at(self, seconds):
        seconds = _validate_seconds(seconds, "预览秒数")
        project = self.normalized()
        return tuple(cue for cue in project.cues if cue.start <= seconds < cue.end)

    def cue_tuples(self):
        return [cue.as_tuple() for cue in self.normalized().cues]


SubtitleEntry = SubtitleCue


class SubtitleStyle(SubtitleStyleDef):
    def __init__(self, *args, bottom_margin=None, margin_v=None, **kwargs):
        if bottom_margin is not None and margin_v is None:
            margin_v = bottom_margin
        super().__init__(*args, margin_v=72 if margin_v is None else margin_v, **kwargs)


class SubtitleTrack(SubtitleProject):
    def __init__(
        self,
        entries=(),
        style=None,
        cues=None,
        styles=None,
        script_info=(),
        enabled=True,
        play_res_x=1920,
        play_res_y=1080,
        default_style_name="short_speech_bottom",
    ):
        cue_items = cues if cues is not None else entries
        style_items = styles
        if style_items is None:
            style_items = (style,) if style is not None else ()
        super().__init__(
            cues=tuple(cue_items or ()),
            styles=tuple(style_items or ()),
            script_info=tuple(script_info or ()),
            enabled=enabled,
            play_res_x=play_res_x,
            play_res_y=play_res_y,
            default_style_name=default_style_name,
        )


def _coerce_cue(item):
    if isinstance(item, SubtitleCue):
        return item
    try:
        start, end, text = item
    except (TypeError, ValueError):
        raise SubtitleValidationError("字幕必须包含开始秒数、结束秒数和文本。")
    return SubtitleCue(start=start, end=end, text=text)


def build_style_preset(preset_id="short_speech_bottom", video_size=None):
    width, height = (1920, 1080)
    if video_size:
        try:
            width, height = video_size
        except (TypeError, ValueError):
            width, height = (1920, 1080)
    scale = max(0.75, min(float(height) / 1080, 1.5))

    presets = {
        "short_speech_bottom": SubtitleStyleDef(
            name="short_speech_bottom",
            font_name="Microsoft YaHei",
            font_size=max(30, int(round(40 * scale))),
            primary_color="&H00FFFFFF",
            outline_color="&H00000000",
            back_color="&H64000000",
            bold=True,
            outline=3.2,
            shadow=1.0,
            alignment=2,
            margin_l=max(40, int(round(width * 0.04))),
            margin_r=max(40, int(round(width * 0.04))),
            margin_v=max(40, int(round(height * 0.29))),
        ),
        "center_emphasis": SubtitleStyleDef(
            name="center_emphasis",
            font_name="Microsoft YaHei",
            font_size=max(34, int(round(54 * scale))),
            primary_color="&H0000E8FF",
            outline_color="&H00201A00",
            back_color="&H00000000",
            bold=True,
            outline=3.8,
            shadow=1.4,
            alignment=5,
            margin_l=max(40, int(round(width * 0.04))),
            margin_r=max(40, int(round(width * 0.04))),
            margin_v=max(40, int(round(height * 0.04))),
        ),
        "top_note": SubtitleStyleDef(
            name="top_note",
            font_name="Microsoft JhengHei",
            font_size=max(22, int(round(32 * scale))),
            primary_color="&H00EAF7FF",
            outline_color="&H00403321",
            back_color="&H50000000",
            bold=False,
            outline=1.8,
            shadow=0.6,
            alignment=8,
            margin_l=max(40, int(round(width * 0.04))),
            margin_r=max(40, int(round(width * 0.04))),
            margin_v=max(26, int(round(height * 0.035))),
        ),
    }
    if preset_id not in presets:
        raise SubtitleValidationError(f"未知字幕样式模板: {preset_id}")
    return presets[preset_id].normalized()


def build_default_subtitle_project(video_size=None, preset_id="short_speech_bottom"):
    style = build_style_preset(preset_id, video_size=video_size)
    width, height = video_size or (1920, 1080)
    return SubtitleProject(
        cues=tuple(),
        styles=(style,),
        script_info=(
            ("ScriptType", "v4.00+"),
            ("WrapStyle", "2"),
            ("ScaledBorderAndShadow", "yes"),
        ),
        enabled=True,
        play_res_x=int(width),
        play_res_y=int(height),
        default_style_name=style.name,
    ).normalized()


def with_style_preset(project, preset_id, cue_indexes=None):
    base = project.normalized()
    style = build_style_preset(preset_id, video_size=(base.play_res_x, base.play_res_y))
    style_map = base.style_map()
    style_map[style.name] = style

    updated_cues = []
    target_indexes = None if cue_indexes is None else set(int(index) for index in cue_indexes)
    for index, cue in enumerate(base.cues):
        if target_indexes is None or index in target_indexes:
            updated_cues.append(
                SubtitleCue(
                    start=cue.start,
                    end=cue.end,
                    text=cue.text,
                    style_name=style.name,
                    source_kind=cue.source_kind,
                    raw_tags=cue.raw_tags,
                    raw_text="",
                    layer=cue.layer,
                )
            )
        else:
            updated_cues.append(cue)

    return SubtitleProject(
        cues=tuple(updated_cues),
        styles=tuple(style_map.values()),
        script_info=base.script_info,
        enabled=base.enabled,
        play_res_x=base.play_res_x,
        play_res_y=base.play_res_y,
        default_style_name=style.name if cue_indexes is None else base.default_style_name,
    ).normalized()


def _cues_from_pysubs2(subs, source_kind, style_name=None):
    cues = []
    for event in subs.events:
        if getattr(event, "type", "Dialogue") != "Dialogue":
            continue
        raw_text = str(event.text or "")
        cue_style = style_name or str(event.style or "short_speech_bottom")
        cues.append(
            SubtitleCue(
                start=float(event.start) / 1000,
                end=float(event.end) / 1000,
                text=ass_text_to_plain_text(raw_text),
                style_name=cue_style,
                source_kind=source_kind,
                raw_tags=extract_leading_ass_tags(raw_text),
                raw_text=raw_text if source_kind == "ass" else "",
                layer=int(event.layer or 0),
            ).normalized()
        )
    return sorted(cues, key=lambda item: (item.start, item.end, item.text))


def _project_from_pysubs2(subs, source_kind, video_size=None, default_preset_id="short_speech_bottom"):
    width, height = video_size or (1920, 1080)
    try:
        width = int(subs.info.get("PlayResX", width))
        height = int(subs.info.get("PlayResY", height))
    except (TypeError, ValueError):
        width, height = video_size or (1920, 1080)

    if source_kind == "srt":
        base = build_default_subtitle_project((width, height), default_preset_id)
        cues = _cues_from_pysubs2(subs, "srt", style_name=base.default_style_name)
        if not cues:
            raise SubtitleValidationError("未找到有效字幕。")
        return SubtitleProject(
            cues=tuple(cues),
            styles=base.styles,
            script_info=base.script_info,
            enabled=True,
            play_res_x=base.play_res_x,
            play_res_y=base.play_res_y,
            default_style_name=base.default_style_name,
        ).normalized()

    styles = []
    for name, style in subs.styles.items():
        styles.append(_style_from_pysubs2(name, style))
    if not styles:
        styles.append(build_style_preset(default_preset_id, (width, height)))

    cues = _cues_from_pysubs2(subs, "ass")
    if not cues:
        raise SubtitleValidationError("未找到有效字幕。")

    return SubtitleProject(
        cues=tuple(cues),
        styles=tuple(styles),
        script_info=tuple((str(key), str(value)) for key, value in subs.info.items()),
        enabled=True,
        play_res_x=width,
        play_res_y=height,
        default_style_name=styles[0].name,
    ).normalized()


def _project_to_pysubs2(project):
    if pysubs2 is None:
        return None

    normalized = project.normalized()
    subs = pysubs2.SSAFile()
    subs.info.update(dict(normalized.script_info))
    subs.info["ScriptType"] = subs.info.get("ScriptType", "v4.00+")
    subs.info["PlayResX"] = str(normalized.play_res_x)
    subs.info["PlayResY"] = str(normalized.play_res_y)
    subs.info["WrapStyle"] = subs.info.get("WrapStyle", "2")
    subs.info["ScaledBorderAndShadow"] = subs.info.get("ScaledBorderAndShadow", "yes")

    subs.styles.clear()
    for style in normalized.styles:
        subs.styles[style.name] = _style_to_pysubs2(style)

    subs.events = [
        pysubs2.SSAEvent(
            start=int(round(cue.start * 1000)),
            end=int(round(cue.end * 1000)),
            text=cue.to_ass_text(),
            layer=cue.layer,
            style=cue.style_name,
        )
        for cue in normalized.cues
    ]
    return subs


def parse_srt_text(text):
    if pysubs2 is not None:
        try:
            cues = _cues_from_pysubs2(pysubs2.SSAFile.from_string(str(text or ""), format_="srt"), "srt")
        except Exception as exc:
            raise SubtitleValidationError(f"SRT 字幕解析失败: {exc}")
        if not cues:
            raise SubtitleValidationError("未找到有效字幕。")
        return tuple(cues)

    content = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not content:
        raise SubtitleValidationError("字幕文件为空。")

    cues = []
    blocks = re.split(r"\n\s*\n", content)
    for block in blocks:
        lines = [line.strip("\ufeff") for line in block.split("\n")]
        time_index = None
        time_match = None
        for index, line in enumerate(lines):
            time_match = _SRT_TIME_RE.search(line)
            if time_match:
                time_index = index
                break

        if time_index is None or time_match is None:
            continue

        subtitle_text = "\n".join(lines[time_index + 1 :])
        cues.append(
            SubtitleCue(
                start=parse_srt_timestamp(time_match.group("start")),
                end=parse_srt_timestamp(time_match.group("end")),
                text=subtitle_text,
                source_kind="srt",
            ).normalized()
        )

    if not cues:
        raise SubtitleValidationError("未找到有效字幕。")
    return tuple(sorted(cues, key=lambda item: (item.start, item.end, item.text)))


def serialize_srt_entries(entries):
    if pysubs2 is not None:
        subs = pysubs2.SSAFile()
        events = []
        for item in entries or ():
            cue = _coerce_cue(item).normalized()
            events.append(
                pysubs2.SSAEvent(
                    start=int(round(cue.start * 1000)),
                    end=int(round(cue.end * 1000)),
                    text=cue.text.replace("\n", "\\N"),
                )
            )
        subs.events = events
        return subs.to_string("srt")

    cues = [_coerce_cue(item).normalized() for item in entries or ()]
    lines = []
    for index, cue in enumerate(sorted(cues, key=lambda item: (item.start, item.end, item.text)), start=1):
        lines.append(str(index))
        lines.append(f"{format_srt_timestamp(cue.start)} --> {format_srt_timestamp(cue.end)}")
        lines.extend(cue.text.split("\n"))
        lines.append("")
    return "\n".join(lines).rstrip() + ("\n" if lines else "")


def read_srt_file(path):
    data = Path(path).read_bytes()
    return parse_srt_text(_decode_subtitle_bytes(data, source_hint=path))


def write_srt_file(entries, path):
    Path(path).write_text(serialize_srt_entries(entries), encoding="utf-8")


def _parse_ass_key_value(line):
    if ":" not in line:
        return None, None
    key, value = line.split(":", 1)
    return key.strip(), value.strip()


def _parse_ass_style_line(line, format_fields):
    values = [item.strip() for item in line.split(":", 1)[1].split(",", len(format_fields) - 1)]
    payload = {field: values[index] if index < len(values) else "" for index, field in enumerate(format_fields)}
    return _coerce_style(payload).normalized()


def _bool_from_ass(value):
    text = str(value or "").strip()
    return text in {"-1", "1", "true", "True"}


def _coerce_style(item):
    if isinstance(item, SubtitleStyleDef):
        return item
    if isinstance(item, dict):
        return SubtitleStyleDef(
            name=item.get("Name", item.get("name", "short_speech_bottom")),
            font_name=item.get("Fontname", item.get("font_name", "Microsoft YaHei")),
            font_size=item.get("Fontsize", item.get("font_size", 38)),
            primary_color=item.get("PrimaryColour", item.get("primary_color", "&H00FFFFFF")),
            secondary_color=item.get("SecondaryColour", item.get("secondary_color", "&H000000FF")),
            outline_color=item.get("OutlineColour", item.get("outline_color", "&H00000000")),
            back_color=item.get("BackColour", item.get("back_color", "&H64000000")),
            bold=_bool_from_ass(item.get("Bold", item.get("bold", True))),
            italic=_bool_from_ass(item.get("Italic", item.get("italic", False))),
            underline=_bool_from_ass(item.get("Underline", item.get("underline", False))),
            strike_out=_bool_from_ass(item.get("StrikeOut", item.get("strike_out", False))),
            scale_x=item.get("ScaleX", item.get("scale_x", 100)),
            scale_y=item.get("ScaleY", item.get("scale_y", 100)),
            spacing=item.get("Spacing", item.get("spacing", 0)),
            angle=item.get("Angle", item.get("angle", 0)),
            border_style=item.get("BorderStyle", item.get("border_style", 1)),
            outline=item.get("Outline", item.get("outline", 2)),
            shadow=item.get("Shadow", item.get("shadow", 0)),
            alignment=item.get("Alignment", item.get("alignment", 2)),
            margin_l=item.get("MarginL", item.get("margin_l", 60)),
            margin_r=item.get("MarginR", item.get("margin_r", 60)),
            margin_v=item.get("MarginV", item.get("margin_v", 72)),
            encoding=item.get("Encoding", item.get("encoding", 1)),
        )
    raise SubtitleValidationError("字幕样式格式不支持。")


def _parse_ass_dialogue_line(line, format_fields):
    values = [item.strip() for item in line.split(":", 1)[1].split(",", len(format_fields) - 1)]
    payload = {field: values[index] if index < len(values) else "" for index, field in enumerate(format_fields)}
    raw_text = payload.get("Text", "")
    return SubtitleCue(
        start=parse_ass_timestamp(payload.get("Start", "0:00:00.00")),
        end=parse_ass_timestamp(payload.get("End", "0:00:00.00")),
        text=ass_text_to_plain_text(raw_text),
        style_name=payload.get("Style", "short_speech_bottom"),
        source_kind="ass",
        raw_tags=extract_leading_ass_tags(raw_text),
        raw_text=raw_text,
        layer=payload.get("Layer", 0),
    ).normalized()


def load_ass_text(text):
    if pysubs2 is not None:
        try:
            return _project_from_pysubs2(
                pysubs2.SSAFile.from_string(str(text or ""), format_="ass"),
                "ass",
            )
        except Exception as exc:
            raise SubtitleValidationError(f"ASS 字幕解析失败: {exc}")

    content = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not content.strip():
        raise SubtitleValidationError("字幕内容为空。")

    section = None
    style_format = list(_ASS_STYLE_FIELDS)
    event_format = list(_ASS_EVENT_FIELDS)
    script_info = []
    styles = []
    cues = []
    play_res_x = 1920
    play_res_y = 1080

    for raw_line in content.split("\n"):
        line = raw_line.strip()
        if not line or line.startswith(";"):
            continue

        match = _ASS_SECTION_RE.match(line)
        if match:
            section = match.group("section")
            continue

        if section == "Script Info":
            key, value = _parse_ass_key_value(line)
            if key is None:
                continue
            if key == "PlayResX":
                try:
                    play_res_x = int(value)
                except ValueError:
                    play_res_x = 1920
            elif key == "PlayResY":
                try:
                    play_res_y = int(value)
                except ValueError:
                    play_res_y = 1080
            script_info.append((key, value))
            continue

        if section == "V4+ Styles":
            if line.startswith("Format:"):
                style_format = [item.strip() for item in line.split(":", 1)[1].split(",")]
                continue
            if line.startswith("Style:"):
                styles.append(_parse_ass_style_line(line, style_format))
            continue

        if section == "Events":
            if line.startswith("Format:"):
                event_format = [item.strip() for item in line.split(":", 1)[1].split(",")]
                continue
            if line.startswith("Dialogue:"):
                cues.append(_parse_ass_dialogue_line(line, event_format))

    if not styles:
        styles.append(build_style_preset("short_speech_bottom", (play_res_x, play_res_y)))

    return SubtitleProject(
        cues=tuple(cues),
        styles=tuple(styles),
        script_info=tuple(script_info),
        enabled=True,
        play_res_x=play_res_x,
        play_res_y=play_res_y,
        default_style_name=styles[0].name,
    ).normalized()


def load_subtitle_text(text, source_hint=None, video_size=None, default_preset_id="short_speech_bottom"):
    format_name = detect_subtitle_format(text, source_hint=source_hint)
    if pysubs2 is not None:
        try:
            subs = pysubs2.SSAFile.from_string(str(text or ""), format_=format_name)
            return _project_from_pysubs2(
                subs,
                format_name,
                video_size=video_size,
                default_preset_id=default_preset_id,
            )
        except Exception as exc:
            raise SubtitleValidationError(f"字幕解析失败: {exc}")

    if format_name == "ass":
        return load_ass_text(text)

    cues = parse_srt_text(text)
    project = build_default_subtitle_project(video_size=video_size, preset_id=default_preset_id)
    return SubtitleProject(
        cues=tuple(
            SubtitleCue(
                start=cue.start,
                end=cue.end,
                text=cue.text,
                style_name=project.default_style_name,
                source_kind="srt",
                raw_tags="",
                raw_text="",
                layer=cue.layer,
            )
            for cue in cues
        ),
        styles=project.styles,
        script_info=project.script_info,
        enabled=True,
        play_res_x=project.play_res_x,
        play_res_y=project.play_res_y,
        default_style_name=project.default_style_name,
    ).normalized()


def load_subtitle_file(path, video_size=None, default_preset_id="short_speech_bottom"):
    data = Path(path).read_bytes()
    text = _decode_subtitle_bytes(data, source_hint=path)
    return load_subtitle_text(
        text,
        source_hint=str(path),
        video_size=video_size,
        default_preset_id=default_preset_id,
    )


def _style_to_ass_line(style):
    normalized = style.normalized()
    values = [
        normalized.name,
        normalized.font_name,
        str(normalized.font_size),
        normalized.primary_color,
        normalized.secondary_color,
        normalized.outline_color,
        normalized.back_color,
        "-1" if normalized.bold else "0",
        "-1" if normalized.italic else "0",
        "-1" if normalized.underline else "0",
        "-1" if normalized.strike_out else "0",
        str(normalized.scale_x),
        str(normalized.scale_y),
        str(normalized.spacing),
        str(normalized.angle),
        str(normalized.border_style),
        f"{normalized.outline:.1f}",
        f"{normalized.shadow:.1f}",
        str(normalized.alignment),
        str(normalized.margin_l),
        str(normalized.margin_r),
        str(normalized.margin_v),
        str(normalized.encoding),
    ]
    return f"Style: {','.join(values)}"


def serialize_ass_project(project):
    if pysubs2 is not None:
        return _project_to_pysubs2(project).to_string("ass")

    normalized = project.normalized()
    script_info = dict(normalized.script_info)
    script_info.setdefault("ScriptType", "v4.00+")
    script_info.setdefault("PlayResX", str(normalized.play_res_x))
    script_info.setdefault("PlayResY", str(normalized.play_res_y))
    script_info.setdefault("WrapStyle", "2")
    script_info.setdefault("ScaledBorderAndShadow", "yes")

    lines = ["[Script Info]"]
    for key, value in script_info.items():
        lines.append(f"{key}: {value}")

    lines.append("")
    lines.append("[V4+ Styles]")
    lines.append(f"Format: {','.join(_ASS_STYLE_FIELDS)}")
    for style in normalized.styles:
        lines.append(_style_to_ass_line(style))

    lines.append("")
    lines.append("[Events]")
    lines.append(f"Format: {','.join(_ASS_EVENT_FIELDS)}")
    for cue in normalized.cues:
        values = [
            str(cue.layer),
            format_ass_timestamp(cue.start),
            format_ass_timestamp(cue.end),
            cue.style_name,
            "",
            "0000",
            "0000",
            "0000",
            "",
            cue.to_ass_text(),
        ]
        lines.append(f"Dialogue: {','.join(values)}")

    return "\n".join(lines).rstrip() + "\n"


def export_subtitle_project_to_ass(project, path):
    Path(path).write_text(serialize_ass_project(project), encoding="utf-8")


def write_ass_file(project, path):
    export_subtitle_project_to_ass(project, path)


def add_subtitle_from_marks(
    existing_entries,
    in_point,
    out_point,
    text,
    total_duration=None,
    style_name="short_speech_bottom",
):
    if in_point is None:
        raise SubtitleValidationError("请先设置入点。")
    if out_point is None:
        raise SubtitleValidationError("请先设置出点。")

    start = _clip_to_duration(_validate_seconds(in_point, "入点"), total_duration)
    end = _clip_to_duration(_validate_seconds(out_point, "出点"), total_duration)
    cue = SubtitleCue(start=start, end=end, text=text, style_name=style_name).normalized()
    cues = [_coerce_cue(item).normalized() for item in existing_entries or ()]
    cues.append(cue)
    return tuple(sorted(cues, key=lambda item: (item.start, item.end, item.text)))
