"""
达人模式的轻量状态转换逻辑。
GUI 负责采集时间点，这里只负责把入点/出点转换成统一编辑模型可用的数据。
"""
from math import isfinite

from edit_model import DeleteRange, EditPlan, OutputOptions, PlanValidationError, normalize_delete_ranges


def _coerce_seconds(value, field_name):
    if value is None:
        raise PlanValidationError(f"请先设置{field_name}。")

    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise PlanValidationError(f"{field_name}必须是有效数字。")

    if not isfinite(seconds):
        raise PlanValidationError(f"{field_name}必须是有限数字。")

    return max(0, seconds)


def _clamp_to_duration(seconds, total_duration):
    if total_duration is None:
        return seconds

    try:
        duration = float(total_duration)
    except (TypeError, ValueError):
        return seconds

    if not isfinite(duration) or duration <= 0:
        return seconds

    return max(0, min(seconds, duration))


def build_delete_range_from_marks(in_point, out_point, total_duration=None):
    """根据入点/出点生成删除区间。"""
    start = _coerce_seconds(in_point, "入点")
    end = _coerce_seconds(out_point, "出点")
    start = _clamp_to_duration(start, total_duration)
    end = _clamp_to_duration(end, total_duration)

    if end <= start:
        raise PlanValidationError("出点必须大于入点。")

    return DeleteRange(start, end).validate()


def add_delete_range_from_marks(existing_ranges, in_point, out_point, total_duration=None):
    """把当前入点/出点加入已有删除区间，并返回归一化后的 DeleteRange 元组。"""
    new_range = build_delete_range_from_marks(in_point, out_point, total_duration)
    raw_ranges = []
    for item in existing_ranges or []:
        if isinstance(item, DeleteRange):
            raw_ranges.append(item.as_tuple())
        else:
            raw_ranges.append(item)

    raw_ranges.append(new_range.as_tuple())
    normalized = normalize_delete_ranges(raw_ranges, total_duration=total_duration)
    return tuple(DeleteRange(start, end) for start, end in normalized)


def build_expert_edit_plan(delete_ranges, output_options=None, subtitles=None):
    """根据达人模式删除区间生成 EditPlan。"""
    ranges = []
    for item in delete_ranges or []:
        if isinstance(item, DeleteRange):
            ranges.append(item)
        else:
            try:
                start, end = item
            except (TypeError, ValueError):
                raise PlanValidationError("删除区间必须包含开始秒数和结束秒数。")
            ranges.append(DeleteRange(start, end))

    return EditPlan(
        delete_ranges=tuple(ranges),
        output=output_options or OutputOptions(),
        subtitles=subtitles,
    ).validate()
