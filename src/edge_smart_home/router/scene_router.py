from __future__ import annotations

import re
from typing import Any, Mapping

from edge_smart_home.common import DeviceCommand, PerceptionEvent


class SceneRouter:
    def __init__(self, scenes: Mapping[str, Mapping[str, Any]]) -> None:
        self._scenes = scenes

    def match(self, event: PerceptionEvent) -> tuple[str, tuple[DeviceCommand, ...]] | None:
        text = re.sub(r"[\s，。！？,.!?]", "", event.label)
        for scene_id, scene in self._scenes.items():
            if text not in scene["aliases"]:
                continue
            commands = tuple(
                DeviceCommand(
                    device_id=item["device_id"],
                    action=item["action"],
                    arguments=item.get("arguments", {}),
                    source=event.source,
                    confidence=event.confidence,
                    request_id=event.event_id,
                )
                for item in scene["commands"]
            )
            return scene_id, commands
        return None
