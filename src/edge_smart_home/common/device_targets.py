"""Finite protection against a qualified unknown device becoming a default.

This recognizes explicit device-kind noun phrases, not unrestricted Chinese
intent or contextual references. Configured full names/aliases and bare generic
nouns remain for the existing router/clarification logic to decide.
"""
from __future__ import annotations

import re
from typing import Any, Mapping


_KINDS = (
    "人体存在传感器", "环境传感器", "烟雾传感器", "漏水传感器", "人体传感器", "动作传感器",
    "空气净化器", "智能插座", "传感器", "摄像头", "机顶盒", "排气扇", "净化器", "路由器",
    "电视", "空调", "窗帘", "风扇", "音箱", "音响", "插座", "门锁", "门磁", "网关", "灯",
)
_NOUN = re.compile("|".join(re.escape(value) for value in sorted(_KINDS, key=len, reverse=True)))
_PREFIXES = tuple(sorted((
    "请问", "麻烦你", "麻烦", "请你", "帮我", "帮助我", "帮忙", "给我", "告诉我", "我想知道",
    "不知道", "我觉得", "我感觉", "我想", "我要", "能不能", "可不可以", "能", "可以", "请", "先",
    "我的", "我家的", "我家", "我们家的", "我们家", "家里的", "家里", "我", "这个", "那个",
    "这盏", "那盏", "这台", "那台", "这部", "那部", "一下", "下一集", "上一集", "下一首",
    "上一首", "下一曲", "上一曲", "下一个", "上一个", "并且", "并", "然后", "再", "和", "与",
    "现在", "立即", "马上", "执行", "规划",
    "打开", "开启", "关掉", "关闭", "启动", "停止", "重启", "查询", "查看", "看看", "看一下",
    "检查", "读取", "显示", "调出", "播放", "暂停", "继续", "恢复", "控制", "调整", "调高", "调低",
    "提高", "降低", "设置", "设为", "调到", "调至", "切换到", "换到", "感觉", "觉得", "知道",
    "把", "将", "让", "给", "对", "看", "开", "关",
), key=len, reverse=True))
_ALTERNATIVES = re.compile(r"或者|或是|以及|还有|和|与|或")


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _strip_leading_syntax(prefix: str) -> str:
    # Strip only anchored, finite request syntax. Searching for a final verb
    # inside the target could erase an unknown qualifier such as 书房打开的灯.
    while prefix:
        matched = next((item for item in _PREFIXES if prefix.startswith(item)), None)
        if matched is None:
            break
        prefix = prefix[len(matched):]
    return prefix


def unknown_explicit_target(text: str, devices: Mapping[str, Mapping[str, Any]]) -> bool:
    """Return true for a recognized noun phrase with an unconfigured qualifier.

    For example, 书房灯 must not match the configured generic alias 灯. This
    does not resolve ambiguous bare devices, implicit rooms or arbitrary NLU.
    Alternative full known targets are left to existing ambiguity handling.
    """
    if not isinstance(text, str):
        return False  # The existing public input validator owns invalid types.
    text = _compact(text)
    names = {_compact(alias) for config in devices.values()
             for alias in [*config.get("aliases", []), config.get("display_name", "")]
             if isinstance(alias, str) and alias.strip()}
    previous_end = 0
    for match in _NOUN.finditer(text):
        prefix = text[previous_end:match.start()]
        previous_end = match.end()
        # Independent clauses need not be a device qualifier. This is not a
        # contextual room resolver; explicit qualifiers remain in this clause.
        prefix = re.split(r"[，。！？,!?；;]", prefix)[-1]
        prefix = _strip_leading_syntax(prefix)
        noun = match.group()
        if not prefix:
            continue
        for part in _ALTERNATIVES.split(prefix):
            qualifier = _strip_leading_syntax(part)
            if qualifier and qualifier + noun not in names:
                return True
    return False
