"""Finite, read-only request/plan fidelity checks around the existing validator.

This module does not execute, schedule, repair numeric values, or consult an
evaluation oracle. Recognized absolute numeric setters and pure read requests
are checked; unrestricted natural-language understanding is outside its scope.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from edge_smart_home.common import PerceptionEvent
from edge_smart_home.router.fast_path import _parse_chinese_number

from .error_codes import IntentErrorCode
from .interfaces import IntelligentPathResult, IntelligentResultKind, PlannedDeviceCommand
from .validation import AgentPlanValidationError, AgentPlanValidator


@dataclass(frozen=True)
class ConsistencyDecision:
    result: IntelligentPathResult
    code: str | None = None
    applied: bool = False


@dataclass(frozen=True)
class _Setting:
    action: str
    argument: str
    value: int | None
    target: str | None
    error: str | None = None


_FIELDS = (
    ("set_volume", "volume", ("音量",)),
    ("set_brightness", "brightness", ("亮度",)),
    ("set_position", "position", ("位置", "开合度", "开度")),
    ("set_speed", "speed", ("风速", "速度", "档位")),
    ("set_temperature", "temperature", ("温度",)),
)
_SETTERS = ("调整到", "调整为", "设置成", "设置为", "设置到", "设置", "设为", "设成",
            "调到", "调至", "调为", "调成", "调整一下", "调一下")
_QUERY = ("查询", "查看", "读取", "检查")
_NEGATION = ("不要", "别", "禁止", "不许", "取消")
_META = ("为什么", "解释", "例如", "比如", "假设", "你能", "你会", "怎么")
_COMPOUND = ("然后", "并且", "同时", "以及", "或者", "或是", "再把", "再将")
_NUMBER = re.compile(r"[+\-负]?(?:[0-9]+(?:[.点][0-9]+)?(?:[eE][+\-]?[0-9]+)?|[零〇一二两三四五六七八九十百千万亿]+(?:点[零〇一二两三四五六七八九]+)?)(?:/[0-9]+)?")
_INTEGER = re.compile(r"[+\-]?[0-9]+")
_CHINESE = re.compile(r"[零〇一二两三四五六七八九十百千万亿]+")
_UNITS = {"temperature": r"(?:度|℃)?", "speed": r"档?",
          "brightness": r"(?:%|％|百分比)?", "position": r"(?:%|％|百分比)?",
          "volume": r"(?:%|％|百分比)?"}


def _normalize(text: str) -> str:
    # Unlike the historical router, do not remove decimal points or signs.
    # Separators inside a number must not join two tokens into another value.
    digits = "0-9零〇一二两三四五六七八九十百千万亿"
    protected = re.sub(rf"(?<=[{digits}])[\s，。！？,!?]+(?=[{digits}])", "\x00", text)
    return re.sub(r"[\s，。！？,!?]", "", protected)


def _integer(token: str) -> int:
    if _INTEGER.fullmatch(token):
        return int(token)
    negative = token.startswith("负")
    body = token[1:] if negative else token
    if not _CHINESE.fullmatch(body):
        raise ValueError("absolute setter requires a complete integer token")
    units = [c for c in body if c in "十百千万亿"]
    if (len(body) > 1 and not units) or len(units) != len(set(units)):
        raise ValueError("unsupported Chinese integer spelling")
    value = _parse_chinese_number(body)
    return -value if negative else value


class RequestPlanConsistency:
    """Apply only finite recognized input constraints, without device effects."""

    def __init__(self, devices: Mapping[str, Mapping[str, Any]]):
        self.devices = devices
        self.validator = AgentPlanValidator(devices)

    def _targets(self, text: str) -> set[str]:
        matches = []
        for device_id, config in self.devices.items():
            for alias in config.get("aliases", []):
                alias = _normalize(alias)
                if not alias:
                    continue
                start = text.find(alias)
                while start >= 0:
                    matches.append((start, start + len(alias), device_id))
                    start = text.find(alias, start + 1)
        # A generic alias inside a longer explicit alias is not another target.
        return {device for begin, end, device in matches
                if not any(other_begin <= begin and end <= other_end
                           and (other_begin < begin or end < other_end)
                           for other_begin, other_end, _ in matches)}

    @staticmethod
    def _is_query(text: str) -> bool:
        if (any(phrase in text for phrase in (*_COMPOUND, *_META))
                or any(phrase in text for phrase in ("画面", "视频", "录像", "回放"))):
            return False
        query_at = min((text.find(q) for q in _QUERY if q in text), default=-1)
        setter_at = min((text.find(s) for s in _SETTERS if s in text), default=-1)
        if query_at < 0 or not re.fullmatch(r"(?:请|帮我|麻烦你|麻烦|请你|帮忙|给我|不要|别|禁止|不许|取消)*", text[:query_at]):
            return False
        return setter_at < 0 or query_at < setter_at

    def _setting(self, text: str) -> _Setting | None:
        if self._is_query(text) or any(phrase in text for phrase in _META):
            return None
        fields = [(action, argument, keyword) for action, argument, keywords in _FIELDS
                  for keyword in keywords if keyword in text]
        setters = [(text.rfind(s), len(s)) for s in _SETTERS if s in text]
        if not setters:
            return None
        targets = self._targets(text)
        if fields:
            unique = {(action, argument) for action, argument, _ in fields}
            if len(unique) != 1 or any(s in text for s in _COMPOUND):
                return _Setting("", "", None, None, "REQUEST_TARGET_AMBIGUOUS")
            action, argument = next(iter(unique))
            keyword = max((keyword for a, arg, keyword in fields if (a, arg) == (action, argument)), key=len)
            tail = text[text.rfind(keyword) + len(keyword):]
        else:
            # E.g. 空调调到-26度 must not let the historical router discard
            # the sign. Infer an argument only from one explicit configured
            # target with exactly one numeric-setter capability, never defaults.
            target_text = text
            if len(targets) == 1:
                target = next(iter(targets))
                for alias in sorted(self.devices[target].get("aliases", []), key=len, reverse=True):
                    target_text = target_text.replace(_normalize(alias), "")
            remaining_setters = [(target_text.rfind(s), len(s)) for s in _SETTERS if s in target_text]
            if not remaining_setters:
                return None  # A setting word occurring only inside an alias.
            position, length = max(remaining_setters)
            tail = target_text[position + length:]
            if not _NUMBER.match(tail) and tail not in {"", "一下", "吧", "就行", "即可"}:
                return None  # Mode, relative-control and other unrecognized requests.
            if len(targets) != 1 or any(s in text for s in _COMPOUND):
                return _Setting("", "", None, None, "REQUEST_TARGET_AMBIGUOUS")
            target = next(iter(targets))
            choices = [(a, arg) for a, arg, _ in _FIELDS
                       if a in self.devices[target].get("capabilities", [])]
            if len(choices) != 1:
                return (_Setting("", "", None, None, "REQUEST_TARGET_AMBIGUOUS")
                        if choices else None)
            action, argument = choices[0]
        # An absolute-setting word can precede the parameter: 设置音量为35.
        for prefix in (*_SETTERS, "为", "到", "成", "是"):
            if tail.startswith(prefix):
                tail = tail[len(prefix):]
                break
        # “一下” is a request particle, not an explicit numeric value of one.
        if tail in {"", "一下", "吧", "就行", "即可"}:
            return _Setting(action, argument, None, None, "REQUEST_ARGUMENT_MISSING")
        match = _NUMBER.match(tail)
        if match is None:
            return _Setting(action, argument, None, None, "REQUEST_ARGUMENT_INVALID")
        if not re.fullmatch(_UNITS[argument] + r"(?:一下|吧|即可|就行|谢谢|请)?", tail[match.end():]):
            return _Setting(action, argument, None, None, "REQUEST_ARGUMENT_INVALID")
        try:
            value = _integer(match.group())
            # Reuse the actual output validator's type/range rules, no clamping
            # and no second table of numeric safety bounds.
            self.validator._validate_arguments(PlannedDeviceCommand("request", action, {argument: value}))
        except (ValueError, AgentPlanValidationError):
            return _Setting(action, argument, None, None, "REQUEST_ARGUMENT_INVALID")
        if len(targets) != 1:
            return _Setting(action, argument, value, None, "REQUEST_TARGET_AMBIGUOUS")
        target = next(iter(targets))
        if action not in self.devices[target].get("capabilities", []):
            return _Setting(action, argument, value, target, "REQUEST_PLAN_MISMATCH")
        return _Setting(action, argument, value, target)

    @staticmethod
    def _failure(code: str) -> ConsistencyDecision:
        clarify = code in {"REQUEST_ARGUMENT_MISSING", "REQUEST_TARGET_AMBIGUOUS"}
        message = ("请明确目标设备和完整参数后再控制。" if clarify
                   else "请求参数或计划与原始指令不一致，已拒绝。")
        return ConsistencyDecision(IntelligentPathResult(
            kind=IntelligentResultKind.CLARIFICATION if clarify else IntelligentResultKind.REJECTED,
            message=message, reason="request_consistency:" + code,
        ), IntentErrorCode(code).value, True)

    def preflight(self, event: PerceptionEvent) -> ConsistencyDecision | None:
        text = _normalize(event.label)
        setting = self._setting(text)
        if setting is None:
            return None
        if any(phrase in text for phrase in _NEGATION):
            return self._failure("REQUEST_PLAN_MISMATCH")
        return self._failure(setting.error) if setting.error else None

    def check(self, event: PerceptionEvent, result: IntelligentPathResult) -> ConsistencyDecision:
        preflight = self.preflight(event)
        if preflight is not None:
            return preflight
        text = _normalize(event.label)
        if self._is_query(text):
            if any(phrase in text for phrase in _NEGATION):
                return self._failure("REQUEST_PLAN_MISMATCH")
            targets = self._targets(text)
            if len(targets) != 1:
                return self._failure("REQUEST_TARGET_AMBIGUOUS")
            target = next(iter(targets))
            if "query_state" not in self.devices[target].get("capabilities", []):
                return self._failure("REQUEST_PLAN_MISMATCH")
            commands = (PlannedDeviceCommand(target, "query_state", {}),)
            if result.kind == IntelligentResultKind.PLAN and result.commands == commands:
                return ConsistencyDecision(result)
            return ConsistencyDecision(IntelligentPathResult(
                kind=IntelligentResultKind.PLAN, commands=commands,
                message="准备查询设备状态，尚未执行。",
            ), IntentErrorCode.QUERY_PLAN_REQUIRED.value, True)
        setting = self._setting(text)
        if setting and not setting.error and result.kind == IntelligentResultKind.PLAN:
            expected = (PlannedDeviceCommand(setting.target, setting.action, {setting.argument: setting.value}),)
            if result.commands != expected:
                return self._failure("REQUEST_PLAN_MISMATCH")
        return ConsistencyDecision(result)

    def apply(self, event: PerceptionEvent, result: IntelligentPathResult) -> IntelligentPathResult:
        return self.check(event, result).result
