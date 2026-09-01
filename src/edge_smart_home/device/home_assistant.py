from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from edge_smart_home.common import DeviceCommand, DeviceState

from .base import DeviceBackend, DeviceBackendError


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
                f"Home Assistant HTTP {error.code}: {detail or error.reason}"
            ) from error
        except (URLError, TimeoutError) as error:
            raise DeviceBackendError(f"Home Assistant request failed: {error}") from error
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as error:
            raise DeviceBackendError("Home Assistant returned invalid JSON") from error


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
                f"Home Assistant token environment variable is not set: {token_env}"
            )
        client = UrllibHomeAssistantClient(
            str(config["base_url"]),
            token,
            float(config.get("request_timeout_seconds", 5.0)),
        )
        return cls(config.get("entities", {}), client)

    def execute(self, command: DeviceCommand) -> DeviceState:
        entity = self._require_entity(command.device_id)
        if command.action == "query_state":
            return self.get_state(command.device_id)
        action = entity.actions.get(command.action)
        if action is None:
            raise DeviceBackendError(
                f"{command.device_id} has no Home Assistant mapping for {command.action}"
            )
        domain = action.get("domain")
        service = action.get("service")
        if not domain or not service:
            raise DeviceBackendError("Home Assistant action requires domain and service")
        payload: dict[str, Any] = {"entity_id": entity.entity_id}
        payload.update(action.get("data", {}))
        for target, rule in action.get("arguments", {}).items():
            source = rule.get("source", target)
            if source not in command.arguments:
                raise DeviceBackendError(f"missing command argument: {source}")
            value = command.arguments[source]
            if "scale" in rule:
                value = float(value) * float(rule["scale"])
            payload[target] = value
        self.client.request("POST", f"/api/services/{domain}/{service}", payload)
        # Read-after-write is the first-stage state confirmation contract.
        return self.get_state(command.device_id)

    def get_state(self, device_id: str) -> DeviceState:
        entity = self._require_entity(device_id)
        raw = self.client.request("GET", f"/api/states/{entity.entity_id}")
        if not isinstance(raw, Mapping):
            raise DeviceBackendError("Home Assistant state response must be an object")
        raw_state = raw.get("state")
        raw_attributes = raw.get("attributes", {})
        if not isinstance(raw_attributes, Mapping):
            raise DeviceBackendError("Home Assistant state attributes must be an object")
        attributes = {
            name: self._map_state_field(rule, raw_state, raw_attributes)
            for name, rule in entity.state_fields.items()
        }
        attributes.setdefault("ha_state", raw_state)
        updated_at = _parse_ha_timestamp(raw.get("last_updated"))
        return DeviceState(
            device_id=device_id,
            available=raw_state not in {"unavailable", "unknown", None},
            attributes=attributes,
            updated_at=updated_at,
        )

    def list_states(self) -> tuple[DeviceState, ...]:
        return tuple(self.get_state(device_id) for device_id in self.entities)

    def _require_entity(self, device_id: str) -> HomeAssistantEntity:
        try:
            return self.entities[device_id]
        except KeyError as error:
            raise DeviceBackendError(
                f"device has no Home Assistant entity mapping: {device_id}"
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
            value = float(value) * float(rule["scale"])
        if value is not None and rule.get("round") is not None:
            value = round(float(value), int(rule["round"]))
        return value


def _parse_ha_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
