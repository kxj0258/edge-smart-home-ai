from __future__ import annotations

from pathlib import Path
import unittest

from edge_smart_home.app import AssistantOrchestrator
from edge_smart_home.common import EventSource, PerceptionEvent


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class AssistantOrchestratorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.orchestrator = AssistantOrchestrator.from_config_dir(
            PROJECT_ROOT / "configs"
        )

    def test_text_and_gesture_share_response_contract(self) -> None:
        text = self.orchestrator.handle_text("打开客厅灯")
        gesture = self.orchestrator.handle_event(
            PerceptionEvent(source=EventSource.GESTURE, label="电视音量调高")
        )
        self.assertEqual(text.status, "executed")
        self.assertEqual(gesture.status, "executed")
        self.assertEqual(text.event.source, EventSource.TEXT)
        self.assertEqual(gesture.event.source, EventSource.GESTURE)

    def test_unroutable_and_confirmation_have_distinct_status(self) -> None:
        unroutable = self.orchestrator.handle_text("今天天气怎么样")
        confirmation = self.orchestrator.handle_text("重启路由器")
        self.assertEqual(unroutable.status, "needs_intelligence")
        self.assertEqual(confirmation.status, "confirmation_required")
        self.assertIsNone(unroutable.outcome)
        self.assertIsNone(confirmation.outcome)
        self.assertIsNotNone(unroutable.intelligent_result)

    def test_common_dialogue_is_local_and_does_not_call_llm(self) -> None:
        response = self.orchestrator.handle_text("你好")
        self.assertEqual(response.status, "responded")
        self.assertEqual(response.path, "fast_path")
        self.assertEqual(response.llm_calls, 0)
        self.assertIn("小兴", response.intelligent_result.message)

    def test_capability_response_uses_complete_device_catalog(self) -> None:
        response = self.orchestrator.handle_text("你能控制哪些设备")
        message = response.intelligent_result.message
        self.assertIn("厨房烟雾传感器", message)
        self.assertIn("入户门锁", message)
        self.assertIn("客厅音箱", message)


if __name__ == "__main__":
    unittest.main()
