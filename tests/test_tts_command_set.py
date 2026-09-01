from __future__ import annotations

import tempfile
from pathlib import Path
import unittest
import wave

import numpy as np

from scripts.generate_tts_command_set import (
    build_merged_lexicon,
    parse_csv_numbers,
    write_pcm16_wav,
)


class TtsCommandSetTest(unittest.TestCase):
    def test_parse_csv_numbers(self):
        self.assertEqual(parse_csv_numbers("0, 2,4", int, "speakers"), [0, 2, 4])
        self.assertEqual(parse_csv_numbers("0.9,1.1", float, "speeds"), [0.9, 1.1])

    def test_write_pcm16_wav(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.wav"
            write_pcm16_wav(path, np.array([-1.0, 0.0, 1.0]), 16000)
            with wave.open(str(path), "rb") as wav_file:
                self.assertEqual(wav_file.getnchannels(), 1)
                self.assertEqual(wav_file.getsampwidth(), 2)
                self.assertEqual(wav_file.getframerate(), 16000)
                self.assertEqual(wav_file.getnframes(), 3)

    def test_build_merged_lexicon_ignores_comments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.txt"
            overrides = root / "overrides.txt"
            destination = root / "merged.txt"
            base.write_text("调 ㄉ ㄧ ㄠ ˋ\n", encoding="utf-8")
            overrides.write_text(
                "# reason\n调高 ㄊ ㄧ ㄠ ˊ ㄍ ㄠ ˉ\n", encoding="utf-8"
            )

            build_merged_lexicon(base, overrides, destination)

            self.assertEqual(
                destination.read_text(encoding="utf-8"),
                "调 ㄉ ㄧ ㄠ ˋ\n调高 ㄊ ㄧ ㄠ ˊ ㄍ ㄠ ˉ\n",
            )


if __name__ == "__main__":
    unittest.main()
