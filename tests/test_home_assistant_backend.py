from __future__ import annotations

import unittest

from edge_smart_home.common import DeviceCommand, EventSource
from edge_smart_home.device import HomeAssistantBackend


class FakeHomeAssistantClient:
    def __init__(self) -> None:
        self.requests = []
        self.state = {
            "entity_id": "light.living_room",
            "state": "off",
            "attributes": {"friendly_name": "Living room"},
            "last_updated": "2026-09-01T05:00:00Z",
        }

    def request(self, method, path, payload=None):
        self.requests.append((method, path, payload))
        if method == "POST":
            self.state["state"] = "on"
            return []
        return dict(self.state)


class HomeAssistantBackendTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = FakeHomeAssistantClient()
        self.backend = HomeAssistantBackend(
            {
                "living_room_light": {
                    "entity_id": "light.living_room",
                    "actions": {
                        "turn_on": {"domain": "light", "service": "turn_on"}
                    },
                    "state_fields": {
                        "power": {
                            "source": "state",
                            "values": {"on": True, "off": False},
                        }
                    },
                }
            },
            self.client,
        )

    def test_service_call_is_followed_by_state_confirmation(self) -> None:
        state = self.backend.execute(
            DeviceCommand("living_room_light", "turn_on", EventSource.TEXT)
        )
        self.assertEqual(
            self.client.requests[0],
            (
                "POST",
                "/api/services/light/turn_on",
                {"entity_id": "light.living_room"},
            ),
        )
        self.assertEqual(
            self.client.requests[1],
            ("GET", "/api/states/light.living_room", None),
        )
        self.assertTrue(state.attributes["power"])
        self.assertEqual(state.updated_at.isoformat(), "2026-09-01T05:00:00+00:00")

    def test_query_only_reads_state(self) -> None:
        state = self.backend.execute(
            DeviceCommand("living_room_light", "query_state", EventSource.TEXT)
        )
        self.assertFalse(state.attributes["power"])
        self.assertEqual(len(self.client.requests), 1)
        self.assertEqual(self.client.requests[0][0], "GET")


if __name__ == "__main__":
    unittest.main()
