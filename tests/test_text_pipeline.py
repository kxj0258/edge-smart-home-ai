from pathlib import Path
import unittest

from edge_smart_home.app.pipeline import TextControlPipeline
from edge_smart_home.router import UnsupportedCommandError


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TextControlPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.pipeline = TextControlPipeline.from_config_dir(PROJECT_ROOT / "configs")

    def test_light_on_and_query(self) -> None:
        self.assertTrue(self.pipeline.handle_text("打开客厅灯").attributes["power"])
        state = self.pipeline.handle_text("查询客厅灯状态")
        self.assertTrue(state.attributes["power"])

    def test_air_conditioner_temperature(self) -> None:
        state = self.pipeline.handle_text("空调设置26度")
        self.assertEqual(state.attributes["temperature"], 26)
        self.assertTrue(state.attributes["power"])

    def test_chinese_temperature_and_absolute_volume(self) -> None:
        air_conditioner = self.pipeline.handle_text("空调设置二十六度")
        self.assertEqual(air_conditioner.attributes["temperature"], 26)
        television = self.pipeline.handle_text("电视音量调到三十")
        self.assertEqual(television.attributes["volume"], 30)

    def test_volume_command_uses_configured_default_device(self) -> None:
        state = self.pipeline.handle_text("音量调高")
        self.assertEqual(state.device_id, "living_room_tv")
        self.assertEqual(state.attributes["volume"], 25)

    def test_query_preserves_last_state_change_time(self) -> None:
        changed = self.pipeline.handle_text("打开客厅灯")
        queried = self.pipeline.handle_text("查询客厅灯状态")
        self.assertEqual(queried.updated_at, changed.updated_at)

    def test_unknown_command_does_not_fall_through(self) -> None:
        with self.assertRaises(UnsupportedCommandError):
            self.pipeline.handle_text("帮我营造一个适合看电影的氛围")


if __name__ == "__main__":
    unittest.main()
