from pathlib import Path
import tempfile
import unittest

from edge_smart_home.audio.hotwords import validate_cjkchar_hotwords


class HotwordValidationTest(unittest.TestCase):
    def test_invalid_character_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tokens.txt").write_text("客 1\n厅 2\n灯 3\n", encoding="utf-8")
            (root / "hotwords.txt").write_text("客厅灯\n窗帘\n", encoding="utf-8")
            result = validate_cjkchar_hotwords(
                root / "tokens.txt", root / "hotwords.txt"
            )
        self.assertEqual(result.valid, ("客厅灯",))
        self.assertEqual(result.invalid, ("窗帘",))


if __name__ == "__main__":
    unittest.main()
