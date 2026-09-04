from __future__ import annotations

import json
from typing import Any, Mapping

from .interfaces import IntelligentPathResult, IntelligentResultKind, PlannedDeviceCommand


MAX_AGENT_MESSAGE_LENGTH = 512
MAX_AGENT_COMMANDS = 8
MAX_AGENT_STRING_LENGTH = 128
AGENT_RESPONSE_KINDS = frozenset(
    {kind.value for kind in IntelligentResultKind}
)


class AgentResponseContractError(ValueError):
    """A model response violates the fixed JSON wire contract."""


def default_agent_response_schema() -> dict[str, Any]:
    """Return the schema sent to a structured-output capable local model."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://edge-smart-home-ai.local/schemas/agent-response.schema.json",
        "title": "Edge Smart Home Agent Response",
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "message", "commands"],
        "properties": {
            "kind": {
                "type": "string",
                "enum": sorted(AGENT_RESPONSE_KINDS),
            },
            "message": {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_AGENT_MESSAGE_LENGTH,
            },
            "commands": {
                "type": "array",
                "maxItems": MAX_AGENT_COMMANDS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["device_id", "action", "arguments"],
                    "properties": {
                        "device_id": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": MAX_AGENT_STRING_LENGTH,
                        },
                        "action": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": MAX_AGENT_STRING_LENGTH,
                        },
                        "arguments": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "volume": {
                                    "type": "integer",
                                    "minimum": 0,
                                    "maximum": 100,
                                },
                                "temperature": {
                                    "type": "integer",
                                    "minimum": 16,
                                    "maximum": 30,
                                },
                                "channel": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": MAX_AGENT_STRING_LENGTH,
                                },
                                "app": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": MAX_AGENT_STRING_LENGTH,
                                },
                                "mode": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 64,
                                },
                            },
                        },
                    },
                },
            },
        },
        "allOf": [
            {
                "if": {
                    "properties": {"kind": {"const": "plan"}},
                    "required": ["kind"],
                },
                "then": {"properties": {"commands": {"minItems": 1}}},
                "else": {"properties": {"commands": {"maxItems": 0}}},
            }
        ],
    }


def parse_agent_response(
    raw_json: str | bytes,
    *,
    max_commands: int = MAX_AGENT_COMMANDS,
) -> IntelligentPathResult:
    """Parse only the fixed wire shape; device safety is validated later."""
    if max_commands <= 0 or max_commands > MAX_AGENT_COMMANDS:
        raise ValueError(f"max_commands must be between 1 and {MAX_AGENT_COMMANDS}")
    try:
        payload = json.loads(
            raw_json,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {value}")
            ),
        )
    except (TypeError, json.JSONDecodeError, ValueError) as error:
        raise AgentResponseContractError("agent response is not valid JSON") from error

    if not isinstance(payload, dict):
        raise AgentResponseContractError("agent response root must be an object")
    _require_exact_keys(payload, {"kind", "message", "commands"}, "response")

    kind_value = payload["kind"]
    if not isinstance(kind_value, str) or kind_value not in AGENT_RESPONSE_KINDS:
        raise AgentResponseContractError("agent response has an unsupported kind")
    message = payload["message"]
    if (
        not isinstance(message, str)
        or not message.strip()
        or len(message) > MAX_AGENT_MESSAGE_LENGTH
    ):
        raise AgentResponseContractError("agent response message must be a non-empty string")

    raw_commands = payload["commands"]
    if not isinstance(raw_commands, list):
        raise AgentResponseContractError("agent response commands must be an array")
    if len(raw_commands) > max_commands:
        raise AgentResponseContractError(
            f"agent response must contain at most {max_commands} commands"
        )
    if kind_value == IntelligentResultKind.PLAN.value and not raw_commands:
        raise AgentResponseContractError("plan must contain at least one command")
    if kind_value != IntelligentResultKind.PLAN.value and raw_commands:
        raise AgentResponseContractError("only a plan may contain commands")

    commands = tuple(_parse_command(item) for item in raw_commands)
    return IntelligentPathResult(
        kind=IntelligentResultKind(kind_value),
        commands=commands,
        message=message,
    )


def _parse_command(value: Any) -> PlannedDeviceCommand:
    if not isinstance(value, dict):
        raise AgentResponseContractError("each agent command must be an object")
    _require_exact_keys(value, {"device_id", "action", "arguments"}, "command")
    device_id = _bounded_string(value["device_id"], "device_id", MAX_AGENT_STRING_LENGTH)
    action = _bounded_string(value["action"], "action", MAX_AGENT_STRING_LENGTH)
    arguments = value["arguments"]
    if not isinstance(arguments, dict):
        raise AgentResponseContractError("command arguments must be an object")
    _validate_arguments_shape(arguments)
    return PlannedDeviceCommand(device_id, action, arguments)


def _validate_arguments_shape(arguments: Mapping[str, Any]) -> None:
    allowed = {"volume", "temperature", "channel", "app", "mode"}
    unexpected = set(arguments) - allowed
    if unexpected:
        raise AgentResponseContractError(
            f"unexpected fields in command arguments: {sorted(unexpected)}"
        )
    for name in ("volume", "temperature"):
        if name not in arguments:
            continue
        value = arguments[name]
        if type(value) is not int:
            raise AgentResponseContractError(f"{name} must be an integer")
        minimum, maximum = (0, 100) if name == "volume" else (16, 30)
        if not minimum <= value <= maximum:
            raise AgentResponseContractError(
                f"{name} must be between {minimum} and {maximum}"
            )
    for name in ("channel", "app", "mode"):
        if name not in arguments:
            continue
        _bounded_string(arguments[name], name, 64 if name == "mode" else MAX_AGENT_STRING_LENGTH)


def _bounded_string(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise AgentResponseContractError(
            f"{name} must be a non-empty string of at most {maximum} characters"
        )
    return value


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    missing = expected - actual
    extra = actual - expected
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing {sorted(missing)}")
        if extra:
            details.append(f"unexpected {sorted(extra)}")
        raise AgentResponseContractError(f"invalid {label} fields: {', '.join(details)}")
