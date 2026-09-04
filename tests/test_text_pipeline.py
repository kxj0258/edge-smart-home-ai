from pathlib import Path
import unittest

from edge_smart_home.app.pipeline import TextControlPipeline
from edge_smart_home.common import EventSource, PerceptionEvent
from edge_smart_home.device import DeviceBackendError
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

    def test_chinese_hundreds_are_not_truncated(self) -> None:
        television = self.pipeline.handle_text("电视音量设置为一百")
        self.assertEqual(television.attributes["volume"], 100)

    def test_volume_command_uses_configured_default_device(self) -> None:
        state = self.pipeline.handle_text("音量调高")
        self.assertEqual(state.device_id, "living_room_tv")
        self.assertEqual(state.attributes["volume"], 25)

    def test_query_preserves_last_state_change_time(self) -> None:
        changed = self.pipeline.handle_text("打开客厅灯")
        queried = self.pipeline.handle_text("查询客厅灯状态")
        self.assertEqual(queried.updated_at, changed.updated_at)

    def test_current_occupancy_query_stays_on_fast_path(self) -> None:
        state = self.pipeline.handle_text("卧室现在有人吗")
        self.assertEqual(state.device_id, "bedroom_sensor")
        self.assertFalse(state.attributes["occupancy"])

    def test_unknown_command_does_not_fall_through(self) -> None:
        with self.assertRaises(UnsupportedCommandError):
            self.pipeline.handle_text("帮我营造一个适合看电影的氛围")

    def test_out_of_range_volume_stays_on_fast_path_and_backend_rejects(self) -> None:
        with self.assertRaises(DeviceBackendError):
            self.pipeline.handle_text("把电视音量设置为1000")

        with self.assertRaises(DeviceBackendError):
            self.pipeline.handle_text("把电视音量设置为一千")

    def test_external_music_does_not_use_default_playback(self) -> None:
        with self.assertRaises(UnsupportedCommandError):
            self.pipeline.handle_text("播放网络音乐")

    def test_unrecognized_prefix_does_not_trigger_generic_music_playback(self) -> None:
        with self.assertRaises(UnsupportedCommandError):
            self.pipeline.handle_text("电瓶播放")

    def test_media_control_prefers_set_top_box_over_tv_alias(self) -> None:
        outcome = self.pipeline.handle_text("我想看下一集电视")
        self.assertEqual(outcome.device_id, "living_room_set_top_box")
        self.assertEqual(outcome.attributes["playback"], "playing")
        command = self.pipeline.router.route(
            PerceptionEvent(source=EventSource.TEXT, label="我想看下一集电视")
        )
        self.assertEqual(command.action, "next")

        previous = self.pipeline.handle_text("上一集")
        previous_command = self.pipeline.router.route(
            PerceptionEvent(source=EventSource.TEXT, label="上一集")
        )
        self.assertEqual(previous.device_id, "living_room_set_top_box")
        self.assertEqual(previous_command.action, "previous")


if __name__ == "__main__":
    unittest.main()
