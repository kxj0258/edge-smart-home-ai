from pathlib import Path
import unittest

from edge_smart_home.app.pipeline import TextControlPipeline
from edge_smart_home.common import DeviceState, EventSource, PerceptionEvent, SceneExecutionResult
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
        chinese_channel = self.pipeline.handle_text("机顶盒切换到中央五台")
        self.assertEqual(chinese_channel.attributes["channel"], "CCTV-5")

    def test_longest_camera_alias_selects_front_door(self) -> None:
        state = self.pipeline.handle_text("查看门口摄像头")
        self.assertEqual(state.device_id, "front_door_camera")

    def test_device_name_can_appear_inside_privacy_action(self) -> None:
        enabled = self.pipeline.handle_text("打开客厅摄像头隐私模式")
        self.assertEqual(enabled.device_id, "living_room_camera")
        self.assertTrue(enabled.attributes["privacy_mode"])
        disabled = self.pipeline.handle_text("关闭客厅摄像头隐私模式")
        self.assertFalse(disabled.attributes["privacy_mode"])

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

    def test_natural_sleep_phrase_triggers_sleep_scene(self) -> None:
        outcome = self.pipeline.handle_text("我要 睡 了")
        self.assertIsInstance(outcome, SceneExecutionResult)
        self.assertEqual(outcome.scene_id, "sleep")
        self.assertTrue(outcome.succeeded)

    def test_common_scene_synonyms_stay_on_fast_path(self) -> None:
        for phrase, scene_id in (
            ("我想睡觉", "sleep"),
            ("我想看电影", "movie"),
            ("我下班回来了", "home"),
        ):
            with self.subTest(phrase=phrase):
                outcome = self.pipeline.handle_text(phrase)
                self.assertIsInstance(outcome, SceneExecutionResult)
                self.assertEqual(outcome.scene_id, scene_id)

    def test_new_home_devices_support_safe_parameter_actions(self) -> None:
        light = self.pipeline.handle_text("卧室灯亮度设置为40")
        self.assertEqual(light.device_id, "bedroom_light")
        self.assertEqual(light.attributes["brightness"], 40)
        curtain = self.pipeline.handle_text("卧室窗帘半开")
        self.assertEqual(curtain.attributes["position"], "partial")
        fan = self.pipeline.handle_text("卫生间排气扇风速设置为2档")
        self.assertEqual(fan.attributes["speed"], 2)
        music = self.pipeline.handle_text("音箱播放音乐")
        self.assertEqual(music.device_id, "living_room_speaker")
        self.assertEqual(music.attributes["playback"], "playing")

    def test_curtain_state_stays_consistent_after_partial_open_and_close(self) -> None:
        partial = self.pipeline.handle_text("客厅窗帘半开")
        self.assertEqual(
            (partial.attributes["position"], partial.attributes["position_percent"]),
            ("partial", 50),
        )
        opened = self.pipeline.handle_text("打开客厅窗帘")
        self.assertEqual(
            (opened.attributes["position"], opened.attributes["position_percent"]),
            ("open", 100),
        )
        closed = self.pipeline.handle_text("关闭客厅窗帘")
        self.assertEqual(
            (closed.attributes["position"], closed.attributes["position_percent"]),
            ("closed", 0),
        )
        self.assertEqual(
            dict(self.pipeline.backend.get_state("living_room_curtain").attributes),
            dict(closed.attributes),
        )

    def test_generic_music_defaults_to_living_room_speaker(self) -> None:
        music = self.pipeline.handle_text("我想听歌")
        self.assertEqual(music.device_id, "living_room_speaker")
        self.assertTrue(music.attributes["power"])
        self.assertEqual(music.attributes["playback"], "playing")

    def test_generic_tv_media_controls_stay_on_set_top_box(self) -> None:
        for phrase, action in (
            ("暂停播放", "pause"),
            ("继续播放", "resume"),
            ("播放下一集", "next"),
        ):
            with self.subTest(phrase=phrase):
                command = self.pipeline.router.route(
                    PerceptionEvent(source=EventSource.TEXT, label=phrase)
                )
                self.assertEqual(command.device_id, "living_room_set_top_box")
                self.assertEqual(command.action, action)

    def test_security_scene_preflights_lock_before_side_effects(self) -> None:
        with self.assertRaises(ConfirmationRequiredError):
            self.pipeline.handle_text("安防模式")
        self.assertFalse(
            self.pipeline.backend.get_state("front_door_camera").attributes["recording"]
        )


if __name__ == "__main__":
    unittest.main()
