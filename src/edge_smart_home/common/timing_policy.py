"""Explicitly unsupported timing phrases, without scheduling or clock parsing.

This conservative finite vocabulary does not understand every temporal request.
Shared entry points must not drop recognized timing and execute immediately.
"""
import re


DEFERRED_TIME_MESSAGE = "暂不支持定时、延时或周期控制，请发送即时指令。"
DEFERRED_TIME_REASON = "scheduling explicitly deferred; no commands scheduled or executed"
_MARKERS = (
    "定时", "延时", "倒计时", "稍后", "明天", "后天", "明早", "明晚",
    "今晚", "下周", "每天", "每晚", "每周", "每隔",
)
_DELAY = re.compile(
    r"[0-9零〇一二两三四五六七八九十百半]+(?:秒钟?|分钟|小时|天|周)(?:之)?后"
)


def is_deferred_time_request(text: str) -> bool:
    normalized = re.sub(r"[\s，。！？,.!?]", "", text)
    return any(marker in normalized for marker in _MARKERS) or bool(_DELAY.search(normalized))
