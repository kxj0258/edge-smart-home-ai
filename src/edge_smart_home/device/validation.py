"""The normalized device-action argument contract shared by all entry paths.

These ranges describe this project's logical device API, not arbitrary Home
Assistant entities or their dynamic supported features. Conversion to a real
integration's units and completion criteria belongs in its backend adapter.
"""
from __future__ import annotations

from typing import Any, Mapping

from .base import DeviceBackendError


INTEGER_ARGUMENT_RULES = {
    "set_volume": ("volume", 0, 100),
    "set_brightness": ("brightness", 0, 100),
    "set_position": ("position", 0, 100),
    "set_speed": ("speed", 1, 3),
    "set_temperature": ("temperature", 16, 30),
}
STRING_ARGUMENT_RULES = {
    "set_channel": "channel",
    "open_app": "app",
    "set_mode": "mode",
    "set_hvac_mode": "mode",
}
ENUM_ARGUMENT_RULES = {
    "set_mode": frozenset({"auto", "sleep", "turbo"}),
    "set_hvac_mode": frozenset({"cool", "heat", "auto", "dry"}),
}
NO_ARGUMENT_ACTIONS = frozenset({
    "turn_on", "turn_off", "volume_up", "volume_down", "open", "close",
    "enable_guest_wifi", "disable_guest_wifi", "restart", "play", "pause",
    "resume", "home", "next", "previous", "enable_privacy", "disable_privacy",
    "start_recording", "stop_recording", "lock", "unlock", "show_feed",
    "speed_up", "speed_down", "query_state",
})
KNOWN_ACTIONS = frozenset(INTEGER_ARGUMENT_RULES) | frozenset(STRING_ARGUMENT_RULES) | NO_ARGUMENT_ACTIONS


def validate_action_arguments(action: str, arguments: Mapping[str, Any]) -> None:
    """Reject missing, guessed, extra or invalid parameters without mutation."""
    if action not in KNOWN_ACTIONS:
        raise DeviceBackendError(f"unsupported action: {action}", error_code="UNSUPPORTED_ACTION")
    if not isinstance(arguments, Mapping):
        raise DeviceBackendError("command arguments must be a mapping", error_code="INVALID_ARGUMENTS")
    remaining = dict(arguments)
    if action in INTEGER_ARGUMENT_RULES:
        name, minimum, maximum = INTEGER_ARGUMENT_RULES[action]
        value = remaining.pop(name, None)
        # bool is an int subclass; accepting it would bypass the typed plan API.
        if type(value) is not int or not minimum <= value <= maximum:
            raise DeviceBackendError(
                f"{name} must be an integer from {minimum} to {maximum}",
                error_code="INVALID_ARGUMENTS",
            )
    elif action in STRING_ARGUMENT_RULES:
        name = STRING_ARGUMENT_RULES[action]
        value = remaining.pop(name, None)
        if not isinstance(value, str) or not value.strip():
            raise DeviceBackendError(f"{name} must be a non-empty string", error_code="INVALID_ARGUMENTS")
        if action in ENUM_ARGUMENT_RULES and value not in ENUM_ARGUMENT_RULES[action]:
            raise DeviceBackendError(f"unsupported {action} value: {value}", error_code="INVALID_ARGUMENTS")
    if remaining:
        names = sorted(str(name) for name in remaining)
        raise DeviceBackendError(f"unexpected arguments for {action}: {names}", error_code="INVALID_ARGUMENTS")
