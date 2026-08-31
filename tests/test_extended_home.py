from pathlib import Path
import unittest

from edge_smart_home.app.pipeline import TextControlPipeline
from edge_smart_home.common import DeviceState, SceneExecutionResult
from edge_smart_home.device import ConfirmationRequiredError


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ExtendedHomeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.pipeline = TextControlPipeline.from_config_dir(PROJECT_ROOT / "configs")

    def test_gateway_guest_wifi_and_confirmation(self) -> None:
        state = self.pipeline.handle_text("打开访客Wi-Fi")
        self.assertTrue(state.attributes["guest_wifi_enabled"])
        with self.assertRaises(ConfirmationRequiredError):
            self.pipeline.handle_text("重启路由器")
        restarted = self.pipeline.handle_text("重启路由器", confirmed=True)
        self.assertEqual(restarted.attributes["restart_count"], 1)

    def test_set_top_box_media_controls(self) -> None:
        state = self.pipeline.handle_text("打开机顶盒")
        self.assertTrue(state.attributes["power"])
        playing = self.pipeline.handle_text("播放下一集")
        self.assertEqual(playing.attributes["playback"], "playing")
        channel = self.pipeline.handle_text("机顶盒切换到CCTV-5")
        self.assertEqual(channel.attributes["channel"], "CCTV-5")

    def test_longest_camera_alias_selects_front_door(self) -> None:
        state = self.pipeline.handle_text("查看门口摄像头")
        self.assertEqual(state.device_id, "front_door_camera")

    def test_environment_sensor_query(self) -> None:
        state = self.pipeline.handle_text("查询客厅温度")
        self.assertEqual(state.device_id, "living_room_sensor")
        self.assertIn("temperature", state.attributes)

    def test_fan_speed_command_uses_default_fan(self) -> None:
        state = self.pipeline.handle_text("风速调高")
        self.assertEqual(state.device_id, "living_room_fan")
        self.assertTrue(state.attributes["power"])
        self.assertEqual(state.attributes["speed"], 2)

    def test_movie_scene_expands_to_device_commands(self) -> None:
        outcome = self.pipeline.handle_text("开启观影模式")
        self.assertIsInstance(outcome, SceneExecutionResult)
        self.assertTrue(outcome.succeeded)
        self.assertEqual(len(outcome.results), 5)
        self.assertTrue(self.pipeline.backend.get_state("living_room_tv").attributes["power"])
        self.assertEqual(
            self.pipeline.backend.get_state("living_room_set_top_box").attributes["app"],
            "IPTV",
        )


if __name__ == "__main__":
    unittest.main()
