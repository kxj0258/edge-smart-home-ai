from __future__ import annotations

import json
import os
from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime, timezone
import math
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from edge_smart_home.common import DeviceCommand, DeviceState

from .base import DeviceBackend, DeviceBackendError
from .validation import validate_action_arguments


class HomeAssistantHttpClient(Protocol):
    def request(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None
    ) -> Any: ...


class UrllibHomeAssistantClient:
    def __init__(self, base_url: str, token: str, timeout_seconds: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def request(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None
    ) -> Any:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise DeviceBackendError(
                f"Home Assistant HTTP {error.code}: {detail or error.reason}", error_code="DEVICE_HTTP_ERROR", request_accepted=None
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise DeviceBackendError(f"Home Assistant request failed: {error}", error_code="DEVICE_TRANSPORT_ERROR", request_accepted=None) from error
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise DeviceBackendError("Home Assistant returned invalid JSON", error_code="DEVICE_RESPONSE_INVALID",
                                     request_accepted=True if method == "POST" else None) from error


@dataclass(frozen=True, slots=True)
class HomeAssistantEntity:
    entity_id: str
    actions: Mapping[str, Mapping[str, Any]]
    state_fields: Mapping[str, Mapping[str, Any]]


class HomeAssistantBackend(DeviceBackend):
    """Home Assistant REST adapter isolated from routing and perception code."""

    def __init__(
        self,
        entities: Mapping[str, Mapping[str, Any]],
        client: HomeAssistantHttpClient,
    ) -> None:
        self.client = client
        self.entities = {
            device_id: HomeAssistantEntity(
                entity_id=str(config["entity_id"]),
                actions=dict(config.get("actions", {})),
                state_fields=dict(config.get("state_fields", {})),
            )
            for device_id, config in entities.items()
        }

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "HomeAssistantBackend":
        token_env = str(config.get("token_env", "HOME_ASSISTANT_TOKEN"))
        token = os.environ.get(token_env)
        if not token:
            raise DeviceBackendError(
                f"Home Assistant token environment variable is not set: {token_env}",
                error_code="DEVICE_CONFIG_INVALID",
            )
        client = UrllibHomeAssistantClient(
            str(config["base_url"]),
            token,
            float(config.get("request_timeout_seconds", 5.0)),
        )
        return cls(config.get("entities", {}), client)

    def execute(self, command: DeviceCommand) -> DeviceState:
        entity = self._require_entity(command.device_id)
        validate_action_arguments(command.action, command.arguments)
        if command.action == "query_state":
            state = self.get_state(command.device_id)
            if not state.available:
                raise DeviceBackendError("Home Assistant entity is unavailable", error_code="DEVICE_OFFLINE", observed_state=state)
            return state
        action = entity.actions.get(command.action)
        if action is None:
            raise DeviceBackendError(
                f"{command.device_id} has no Home Assistant mapping for {command.action}", error_code="UNSUPPORTED_ACTION"
            )
        domain = action.get("domain")
        service = action.get("service")
        if not domain or not service:
            raise DeviceBackendError("Home Assistant action requires domain and service", error_code="DEVICE_CONFIG_INVALID")
        payload: dict[str, Any] = {"entity_id": entity.entity_id}
        payload.update(action.get("data", {}))
        for target, rule in action.get("arguments", {}).items():
            source = rule.get("source", target)
            if source not in command.arguments:
                raise DeviceBackendError(f"missing command argument: {source}", error_code="INVALID_ARGUMENTS")
            value = command.arguments[source]
            if "scale" in rule:
                value = float(value) * float(rule["scale"])
            payload[target] = value
        try:
            self.client.request("POST", f"/api/services/{domain}/{service}", payload)
        except DeviceBackendError as error:
            # An attempted POST with no successful acknowledgement is unknown,
            # not proof of rejection or absence of remote side effects.
            if error.request_accepted is not True:
                error.request_accepted = None
            raise
        # A completed service handler/HTTP response does not certify its target.
        # This stage makes one observation, not a polling/physical confirmation
        # protocol. Preserve acceptance if readback subsequently fails.
        try:
            state = self.get_state(command.device_id)
        except DeviceBackendError as error:
            error.request_accepted = True
            raise
        if not state.available:
            raise DeviceBackendError("service accepted but entity is unavailable", error_code="DEVICE_OFFLINE",
                                     request_accepted=True, observed_state=state)
        expected = _target_attributes(command)
        if not expected or not all(_target_value_matches(state.attributes.get(k), v) for k, v in expected.items()):
            raise DeviceBackendError("service accepted; requested target state is not confirmed by readback",
                                     error_code="DEVICE_STATE_UNCONFIRMED", request_accepted=True, observed_state=state)
        return replace(state, completion_scope="ha_target_observed")

    def get_state(self, device_id: str) -> DeviceState:
        entity = self._require_entity(device_id)
        raw = self.client.request("GET", f"/api/states/{entity.entity_id}")
        if not isinstance(raw, Mapping):
            raise DeviceBackendError("Home Assistant state response must be an object", error_code="DEVICE_RESPONSE_INVALID")
        raw_state = raw.get("state")
        if not isinstance(raw_state, str) or ("entity_id" in raw and raw["entity_id"] != entity.entity_id):
            raise DeviceBackendError("Home Assistant state/entity identity is invalid", error_code="DEVICE_RESPONSE_INVALID")
        raw_attributes = raw.get("attributes", {})
        if not isinstance(raw_attributes, Mapping):
            raise DeviceBackendError("Home Assistant state attributes must be an object", error_code="DEVICE_RESPONSE_INVALID")
        attributes = {
            name: self._map_state_field(rule, raw_state, raw_attributes)
            for name, rule in entity.state_fields.items()
        }
        attributes.setdefault("ha_state", raw_state)
        numeric_fields = {"temperature", "humidity", "ambient_light_lux", "volume", "brightness", "position_percent",
                          "speed", "power_watts", "pm25", "connected_devices", "downlink_mbps", "uplink_mbps",
                          "mesh_nodes_online", "restart_count", "position_seconds"}
        for name in numeric_fields & attributes.keys():
            value = attributes[name]
            if value is not None and not _finite_number(value):
                raise DeviceBackendError(f"Home Assistant numeric state field is invalid: {name}", error_code="DEVICE_RESPONSE_INVALID")
        if entity.entity_id.startswith("light.") and attributes.get("power") is False and "brightness" in attributes:
            attributes["brightness"] = 0  # normalized active output; not remembered turn-on level
        position = attributes.get("position_percent")
        if (entity.entity_id.startswith("cover.") and raw_state in {"open", "closed"}
                and type(position) in (int, float) and math.isfinite(position) and 0 <= position <= 100):
            attributes["position"] = "closed" if position == 0 else "open" if position == 100 else "partial"
        updated_at = _parse_ha_timestamp(raw.get("last_updated"))
        return DeviceState(
            device_id=device_id,
            available=raw_state not in {"unavailable", "unknown", None},
            attributes=attributes,
            updated_at=updated_at,
            backend_kind="home_assistant",
            completion_scope="ha_state_observation",
        )

    def list_states(self) -> tuple[DeviceState, ...]:
        return tuple(self.get_state(device_id) for device_id in self.entities)

    def _require_entity(self, device_id: str) -> HomeAssistantEntity:
        try:
            return self.entities[device_id]
        except KeyError as error:
            raise DeviceBackendError(
                f"device has no Home Assistant entity mapping: {device_id}", error_code="UNKNOWN_DEVICE"
            ) from error

    @staticmethod
    def _map_state_field(
        rule: Mapping[str, Any], raw_state: Any, raw_attributes: Mapping[str, Any]
    ) -> Any:
        source = rule.get("source", "state")
        value = raw_state if source == "state" else raw_attributes.get(source)
        values = rule.get("values")
        if isinstance(values, Mapping):
            value = values.get(str(value), value)
        if value is not None and "scale" in rule:
            try:
                if isinstance(value, bool):
                    raise ValueError("boolean is not a numeric state field")
                value = float(value) * float(rule["scale"])
            except (TypeError, ValueError, OverflowError) as error:
                raise DeviceBackendError("Home Assistant numeric state field is invalid", error_code="DEVICE_RESPONSE_INVALID") from error
            if not math.isfinite(value):
                raise DeviceBackendError("Home Assistant numeric state field is nonfinite", error_code="DEVICE_RESPONSE_INVALID")
        if value is not None and rule.get("round") is not None:
            try:
                if isinstance(value, bool) or not math.isfinite(float(value)):
                    raise ValueError("numeric state must be finite and not boolean")
                value = round(float(value), int(rule["round"]))
            except (TypeError, ValueError, OverflowError) as error:
                raise DeviceBackendError("Home Assistant rounded state field is invalid", error_code="DEVICE_RESPONSE_INVALID") from error
        return value


def _parse_ha_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise DeviceBackendError("Home Assistant last_updated is missing", error_code="DEVICE_RESPONSE_INVALID")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise DeviceBackendError("Home Assistant last_updated is invalid", error_code="DEVICE_RESPONSE_INVALID") from error
    if parsed.tzinfo is None:
        raise DeviceBackendError("Home Assistant last_updated must have a timezone", error_code="DEVICE_RESPONSE_INVALID")
    return parsed


def _target_value_matches(observed: Any, expected: Any) -> bool:
    if isinstance(expected, bool):
        return type(observed) is bool and observed is expected
    if type(expected) in (int, float):
        return _finite_number(observed) and math.isclose(observed, expected, abs_tol=1e-6, rel_tol=0)
    return observed == expected


def _finite_number(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (TypeError, ValueError, OverflowError):
        return False


def _target_attributes(command: DeviceCommand) -> dict[str, Any] | None:
    """Conservative, normalized target predicates, never a hardware guarantee.

    Relative navigation/restarts lack a target observation contract here and
    remain explicitly unconfirmed after acceptance. No state is fabricated.
    """
    action, args = command.action, command.arguments
    if action in {"turn_on", "turn_off"}:
        return {"power": action == "turn_on"}
    if action == "set_brightness":
        return {"brightness": args["brightness"], "power": args["brightness"] > 0}
    if action in {"set_volume", "set_temperature", "set_speed"}:
        field = {"set_volume": "volume", "set_temperature": "temperature", "set_speed": "speed"}[action]
        return {field: args[field]}
    if action in {"set_mode", "set_hvac_mode"}:
        return {"mode" if action == "set_mode" else "hvac_mode": args["mode"]}
    if action in {"open", "close", "set_position"}:
        position = 100 if action == "open" else 0 if action == "close" else args["position"]
        return {"position_percent": position, "position": "closed" if position == 0 else "open" if position == 100 else "partial",
                "ha_state": "closed" if position == 0 else "open"}
    if action in {"play", "resume", "pause"}:
        return {"playback": "paused" if action == "pause" else "playing"}
    if action in {"lock", "unlock"}:
        return {"locked": action == "lock"}
    if action == "open_app":
        return {"app": args["app"]}
    if action == "set_channel":
        return {"channel": args["channel"]}
    return None
