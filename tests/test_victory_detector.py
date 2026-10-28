"""Visual regressions; real demo frames are optional local audit fixtures.

The source template comes ONLY from the first fight at 206 s. The second fight
at 1045 s is an independent encounter, never part of the template asset.
"""
from pathlib import Path
import unittest

import cv2
import numpy as np

from app.victory_detector import VictoryDetector


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "data" / "demo-audit"


def read_image(path):
    return cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)


class VictoryDetectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.detector = VictoryDetector()

    def test_uniform_flashes_and_dark_frames_are_not_victory(self):
        for value in (0, 80, 210, 255):
            with self.subTest(value=value):
                self.assertIsNone(self.detector.detect(np.full((540, 960, 3), value, np.uint8)))

    def test_random_bright_sparks_are_not_glyph(self):
        rng = np.random.default_rng(2026)
        frame = np.zeros((540, 960, 3), np.uint8)
        for x, y, r in zip(rng.integers(0, 960, 60), rng.integers(0, 540, 60), rng.integers(3, 30, 60)):
            cv2.circle(frame, (int(x), int(y)), int(r), (255, 255, 255), -1)
        self.assertIsNone(self.detector.detect(frame))

    def test_unsupported_frame_is_ignored(self):
        for frame in (None, np.zeros((100, 100, 3), np.uint8), np.zeros((960, 540, 3), np.uint8),
                      np.zeros((540, 960, 3), np.float32), np.zeros((540, 960, 1), np.uint8)):
            self.assertIsNone(self.detector.detect(frame))

    def test_held_out_second_boss_victory_and_resized_frames(self):
        path = AUDIT / "second-half" / "exact_01045.00.jpg"
        if not path.exists():
            self.skipTest("User's local gameplay audit fixture is not installed")
        frame = read_image(path)
        for size in ((1920, 1080), (1280, 720), (960, 540), (640, 360)):
            with self.subTest(size=size):
                candidate = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
                result = self.detector.detect(candidate)
                self.assertIsNotNone(result)
                self.assertEqual(result["method"], "template")
                self.assertEqual(result["text"], "斩")
                self.assertGreater(result["confidence"], .80)
                self.assertNotIn("enemy_name", result)

    def test_no_victory_from_combat_weapon_menu_or_bright_scenery(self):
        fixtures = [
            "second-half/exact_01040.00.jpg", "second-half/exact_01044.00.jpg",
            "second-half/exact_01050.00.jpg", "second-half/exact_01090.00.jpg",
            "second-half/exact_00920.00.jpg", "second-half/exact_01120.00.jpg",
            "first-half/frame_0106.00.jpg", "first-half/frame_0108.00.jpg",
            "first-half/frame_0400.00.jpg", "first-half/frame_0438.00.jpg",
        ]
        if not all((AUDIT / p).exists() for p in fixtures):
            self.skipTest("User's local gameplay audit fixtures are not installed")
        for relative in fixtures:
            with self.subTest(frame=relative):
                self.assertIsNone(self.detector.detect(read_image(AUDIT / relative)))

    def test_clear_animation_window_not_just_single_lucky_frame(self):
        times = (1044.8, 1045, 1045.2, 1045.4)
        paths = [AUDIT / "second-half" / f"exact_{t:08.2f}.jpg" for t in times]
        if not all(p.exists() for p in paths):
            self.skipTest("Dense local gameplay audit fixtures are not installed")
        for path in paths:
            with self.subTest(frame=path.name):
                self.assertIsNotNone(self.detector.detect(read_image(path)))


if __name__ == "__main__":
    unittest.main()
