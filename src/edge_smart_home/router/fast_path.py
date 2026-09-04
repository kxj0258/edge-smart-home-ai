from __future__ import annotations

import re
from typing import Any, Mapping

from edge_smart_home.common import DeviceCommand, PerceptionEvent


class UnsupportedCommandError(ValueError):
    """The deterministic router cannot safely parse an event."""


class FastPathRouter:
    def __init__(
        self,
        devices: Mapping[str, Mapping[str, Any]],
        intents: Mapping[str, list[str]],
        default_devices: Mapping[str, str] | None = None,
    ) -> None:
        self._devices = devices
        self._intents = intents
        self._default_devices = dict(default_devices or {})

    def route(self, event: PerceptionEvent) -> DeviceCommand:
        text = self._normalize(event.label)
        if any(
            phrase in text for phrase in ("网络音乐", "在线音乐", "网络歌曲", "网络电台")
        ):
            raise UnsupportedCommandError(
                "external media capability is unavailable"
            )
        device_id = self._preferred_media_device(text) or self._match_device(text)
        capabilities = set(self._devices[device_id]["capabilities"])
        if "query_state" in capabilities and any(
            phrase in text for phrase in self._intents.get("query_state", [])
        ):
            return DeviceCommand(
                device_id=device_id,
                action="query_state",
                arguments={},
                source=event.source,
                confidence=event.confidence,
                request_id=event.event_id,
            )
        action_text = self._remove_device_aliases(text, device_id)
        action, arguments = self._match_action(action_text, device_id, capabilities)
        return DeviceCommand(
            device_id=device_id,
            action=action,
            arguments=arguments,
            source=event.source,
            confidence=event.confidence,
            request_id=event.event_id,
        )

    def _remove_device_aliases(self, text: str, device_id: str) -> str:
        aliases = sorted(self._devices[device_id]["aliases"], key=len, reverse=True)
        for alias in aliases:
            text = text.replace(self._normalize(alias), "")
        return text

    @staticmethod
    def _normalize(text: str) -> str:
        normalized = re.sub(r"[\s，。！？,.!?]", "", text)

        def replace_number(match: re.Match[str]) -> str:
            value = match.group()
            if _is_numeric_context(normalized, match.start(), match.end(), value):
                return str(_parse_chinese_number(value))
            # Do not turn lexical words such as 下一集/上一集 into 下1集/上1集.
            return value

        return re.sub(r"[零〇一二两三四五六七八九十百千万亿]+", replace_number, normalized)

    def _match_device(self, text: str) -> str:
        matched_aliases = {
            device_id: max(
                (len(alias) for alias in config["aliases"] if alias in text),
                default=0,
            )
            for device_id, config in self._devices.items()
        }
        longest = max(matched_aliases.values(), default=0)
        matches = {
            device_id
            for device_id, alias_length in matched_aliases.items()
            if alias_length == longest and alias_length > 0
        }
        if len(matches) == 1:
            return matches.pop()
        if len(matches) > 1:
            raise UnsupportedCommandError("command identifies more than one device")

        default_matches = {
            device_id
            for action, device_id in self._default_devices.items()
            if any(phrase in text for phrase in self._intents.get(action, []))
        }
        if len(default_matches) == 1:
            return default_matches.pop()
        raise UnsupportedCommandError("command must identify exactly one device")

    def _match_action(
        self, text: str, device_id: str, capabilities: set[str]
    ) -> tuple[str, dict[str, Any]]:
        if "set_temperature" in capabilities:
            match = re.search(r"(?:设置|调到|调至|空调)?(\d{1,3})(?:度|℃)", text)
            if match:
                return "set_temperature", {"temperature": int(match.group(1))}

        if "set_volume" in capabilities:
            match = re.search(
                r"(?:音量)?(?:设置(?:为)?|设为|调到|调至|调为)(\d{1,4})", text
            )
            if match:
                return "set_volume", {"volume": int(match.group(1))}

        if "set_channel" in capabilities:
            match = re.search(
                r"(?:切换到|换到|播放)(?:CCTV-?(\d+)|中央(\d+)台)",
                text,
                re.IGNORECASE,
            )
            if match:
                channel_number = match.group(1) or match.group(2)
                return "set_channel", {"channel": f"CCTV-{channel_number}"}

        if "set_mode" in capabilities:
            modes = {"自动模式": "auto", "睡眠模式": "sleep", "强力模式": "turbo"}
            for phrase, mode in modes.items():
                if phrase in text:
                    return "set_mode", {"mode": mode}

        action_order = [
            "enable_guest_wifi", "disable_guest_wifi", "restart",
            "enable_privacy", "disable_privacy", "start_recording", "stop_recording",
            "show_feed", "resume", "pause", "next", "previous", "home", "open_app",
            "volume_up", "volume_down", "speed_up", "speed_down",
            "query_state", "open", "close", "turn_on", "turn_off", "play",
        ]

        for action in action_order:
            if action in capabilities and any(
                phrase in text for phrase in self._intents.get(action, [])
            ):
                return action, {}
        raise UnsupportedCommandError("no deterministic action rule matched")

    def _preferred_media_device(self, text: str) -> str | None:
        """Route known playback controls to the set-top box before aliases.

        User phrasing such as “我想看下一集电视” contains the TV alias, but
        the requested action belongs to the set-top box.  Resolving this known
        capability deterministically also prevents a model from adding an
        unrelated TV power command.
        """
        media_actions = ("play", "pause", "resume", "next", "previous", "home", "open_app")
        matches = []
        for device_id, config in self._devices.items():
            capabilities = set(config.get("capabilities", []))
            if not capabilities.intersection(media_actions):
                continue
            if any(
                action in capabilities
                and any(phrase in text for phrase in self._intents.get(action, []))
                for action in media_actions
            ):
                matches.append(device_id)
        return matches[0] if len(matches) == 1 else None


def _parse_chinese_number(value: str) -> int:
    """Convert common Chinese integer forms without truncating unit words.

    This is intentionally a small parser for command arguments, not a general
    natural-language number parser.  Supporting 万/亿 as section units keeps
    malformed or out-of-range device parameters from being partially matched
    as a smaller valid prefix (for example, 一千 must never become 1).
    """
    digits = {
        "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
        "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    }
    units = {"十": 10, "百": 100, "千": 1000, "万": 10_000, "亿": 100_000_000}
    if not value:
        raise ValueError("Chinese number cannot be empty")

    total = 0
    section = 0
    number = 0
    for character in value:
        if character in digits:
            number = digits[character]
            continue

        unit = units[character]
        if unit < 10_000:
            section += (number or 1) * unit
            number = 0
            continue

        section += number
        total += (section or 1) * unit
        section = 0
        number = 0

    return total + section + number


def _is_numeric_context(text: str, start: int, end: int, value: str) -> bool:
    """Only normalize Chinese digits when they represent a command argument."""
    if any(unit in value for unit in ("十", "百", "千", "万", "亿")):
        return True
    numeric_prefixes = ("设置为", "设置", "设为", "调到", "调至", "调为", "音量")
    numeric_suffixes = ("度", "℃")
    return (
        any(text[:start].endswith(prefix) for prefix in numeric_prefixes)
        or any(text[end:].startswith(suffix) for suffix in numeric_suffixes)
    )
