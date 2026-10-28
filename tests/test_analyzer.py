import tempfile
import unittest
from pathlib import Path
import importlib.util
import os
import subprocess

from app.rules import OCRLine, classify_lines
from app.analyzer import EventAccumulator, _validate_options, _sampled_frames, probe_video, analyze


def build_fixture(root: Path) -> Path:
    """Create plainly labeled OCR test footage; it is not footage from the game."""
    import random
    import subprocess
    from PIL import Image, ImageDraw, ImageFont
    root.mkdir(parents=True, exist_ok=True)
    rng = random.Random(18)
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 38)
    small = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 24)
    panels = [None, ("获得：测试玉佩 × 1",), None, ("已击败：测试守卫",), None,
              ("成就已解锁", "初试锋芒"), None, ("任务目标", "击败测试守卫", "获得方式：完成任务"),
              None, ("获得：测试玉佩 × 1",), None, None]
    entries = []
    for index, lines in enumerate(panels):
        image = Image.new("RGB", (1280, 720), (30, 35, 38))
        draw = ImageDraw.Draw(image)
        for _ in range(140):
            x, y = rng.randrange(1280), rng.randrange(720)
            shade = rng.randrange(35, 65)
            draw.line((x, y, x + rng.randrange(20, 120), y + rng.randrange(5, 60)), fill=(shade, shade, shade), width=2)
        draw.text((36, 28), "自动识别测试素材", font=small, fill=(160, 165, 170))
        draw.text((36, 670), "生命 100/100", font=small, fill=(160, 165, 170))
        if lines:
            box_height = 70 + len(lines) * 64
            draw.rectangle((310, 255, 970, 255 + box_height), fill=(18, 20, 22), outline=(120, 124, 128), width=2)
            for line_index, line in enumerate(lines):
                draw.text((350, 288 + line_index * 64), line, font=font, fill=(245, 242, 228))
        path = root / f"panel-{index:02d}.png"
        image.save(path)
        entries.extend([f"file '{path.name}'", "duration 3"])
    entries.append(f"file 'panel-{len(panels)-1:02d}.png'")
    manifest = root / "fixture-input.txt"
    manifest.write_text("\n".join(entries), encoding="utf-8")
    video = root / "识别测试素材_36秒.mp4"
    subprocess.run([r"D:\ffmpeg-7.1-full_build\bin\ffmpeg.exe", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(manifest), "-t", "36", "-r", "30", "-c:v", "libx264", "-crf", "25", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-y", str(video)], check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return video


class RuleTests(unittest.TestCase):
    def kinds(self, *lines):
        return [(r["kind"], r["name"]) for r in classify_lines(lines)]

    def test_collect_notification_and_quantity(self):
        self.assertIn(("item", "青铜钥匙"), self.kinds("获得：青铜钥匙 × 1"))

    def test_achievement_split_lines(self):
        lines = [OCRLine("成就已解锁", .97, [[200, 200], [400, 200], [400, 240], [200, 240]]),
                 OCRLine("初露锋芒", .93, [[220, 250], [360, 250], [360, 285], [220, 285]])]
        self.assertEqual(classify_lines(lines)[0]["name"], "初露锋芒")
        self.assertEqual(classify_lines(lines)[0]["kind"], "achievement")

    def test_enemy_success(self):
        self.assertIn(("enemy", "守门人"), self.kinds("已击败：守门人"))
        self.assertIn(("enemy", "守门人"), self.kinds("守门人 已击败"))

    def test_menu_objective_and_names_are_not_events(self):
        for line in ["青铜钥匙", "守门人", "任务目标", "击败守门人", "获得方式：击败守门人", "尚未获得青铜钥匙", "获得 3/10", "需要击败守门人", "成就列表"]:
            self.assertTrue(all(kind == "text" for kind, _ in self.kinds(line)), line)

    def test_distant_line_cannot_supply_name(self):
        lines = [OCRLine("成就已解锁", .97, [[200, 200], [400, 200], [400, 240], [200, 240]]),
                 OCRLine("任务目标", .93, [[20, 700], [160, 700], [160, 735], [20, 735]])]
        self.assertIn("名称未识别", classify_lines(lines)[0]["name"])

    def test_custom_keywords_are_literals(self):
        result = classify_lines(["入手：青铜钥匙"], {"item": ["入手"]})
        self.assertEqual((result[0]["kind"], result[0]["name"]), ("item", "青铜钥匙"))

    def test_ocr_spaced_chinese(self):
        self.assertIn(("item", "青铜钥匙"), self.kinds("获 得：青 铜 钥 匙"))


class AccumulatorTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.accumulator = EventAccumulator("same-video", 1, self.events.append)
        self.candidate = {"kind": "item", "name": "青铜钥匙", "text": "获得 青铜钥匙", "confidence": .98}

    def test_persistent_notification_updates_same_id(self):
        for time in [10, 11, 12]:
            self.accumulator.add([self.candidate], time, lambda: "frame.jpg")
        self.accumulator.flush()
        self.assertEqual(len({r["id"] for r in self.events}), 1)
        self.assertEqual(self.events[-1]["end_time"], 13)
        self.assertEqual(self.events[-1]["verified"], 0)

    def test_second_acquisition_not_swallowed(self):
        self.accumulator.add([self.candidate], 10, lambda: "frame.jpg")
        self.accumulator.add([self.candidate], 11, lambda: "frame.jpg")
        self.accumulator.add([], 12, lambda: "frame.jpg")
        self.accumulator.add([self.candidate], 13, lambda: "frame2.jpg")
        self.assertEqual(len({r["id"] for r in self.events}), 2)

    def test_no_screenshot_for_empty_frame(self):
        def fail():
            raise AssertionError("No candidate should create a screenshot")
        self.accumulator.add([], 1, fail)

    def test_validation(self):
        for options in [{"sample_interval": 0}, {"resume_from": -1}, {"regions": [{"x": .8, "y": 0, "w": .5, "h": 1}]}, {"sample_interval": float("nan")}]:
            with self.assertRaises(ValueError):
                _validate_options(options)


@unittest.skipUnless(Path(r"D:\ffmpeg-7.1-full_build\bin\ffmpeg.exe").exists() and importlib.util.find_spec("numpy"), "FFmpeg and numpy required")
class DecoderTimingTests(unittest.TestCase):
    def test_nonzero_start_and_resumed_sample_grid(self):
        target = Path(__file__).resolve().parent / "fixtures" / "timing"
        target.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=target) as temporary:
            video = Path(temporary) / "offset.mp4"
            subprocess.run([r"D:\ffmpeg-7.1-full_build\bin\ffmpeg.exe", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=10:duration=5", "-vf", "setpts=PTS+5/TB", "-c:v", "libx264", "-y", str(video)], check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            info = probe_video(str(video))
            self.assertAlmostEqual(info["start_time"], 5.0, places=2)
            full = [t for t, _ in _sampled_frames(str(video), info, _validate_options({}), lambda: False)]
            resumed = [t for t, _ in _sampled_frames(str(video), info, _validate_options({"resume_from": 2.5}), lambda: False)]
            self.assertEqual(resumed, [t for t in full if t >= 2])
            self.assertAlmostEqual(resumed[0], 2.0, places=2)


@unittest.skipUnless(os.environ.get("RUN_OCR_INTEGRATION") == "1", "Set RUN_OCR_INTEGRATION=1 for real OCR test")
class RealOCRTests(unittest.TestCase):
    def test_full_video_and_cancel_resume(self):
        root = Path(__file__).resolve().parent / "fixtures"
        video = root / "识别测试素材_36秒.mp4"
        if not video.exists():
            build_fixture(root)
        records = {}
        progress = []
        result = analyze(str(video), root / "integration-screenshots", {}, lambda e: records.update({e["id"]: e}), progress.append, lambda: False)
        expected = [("item", "测试玉佩", 3), ("enemy", "测试守卫", 9), ("achievement", "初试锋芒", 15), ("item", "测试玉佩", 27)]
        actual = sorted((e["kind"], e["name"], e["time"]) for e in records.values() if e["kind"] != "text")
        self.assertEqual(actual, sorted(expected))
        self.assertEqual(result["status"], "completed")
        self.assertTrue(all(Path(e["screenshot"]).is_file() for e in records.values()))
        self.assertTrue(all(e["verified"] == 0 for e in records.values()))
        self.assertEqual(progress[-1]["progress"], 1.0)
        cancel_progress = []
        stopped = analyze(str(video), root / "integration-screenshots", {}, lambda e: None, cancel_progress.append, lambda: any(p["frames"] >= 4 for p in cancel_progress))
        self.assertEqual(stopped["status"], "cancelled")
        self.assertLess(stopped["checkpoint"], result["duration"])
        resumed_records = {}
        resumed = analyze(str(video), root / "integration-screenshots", {"resume_from": stopped["checkpoint"]}, lambda e: resumed_records.update({e["id"]: e}), lambda p: None, lambda: False)
        self.assertEqual(resumed["status"], "completed")
        # Absolute sample buckets must keep IDs stable across resume.
        for key, event in resumed_records.items():
            if event["kind"] != "text":
                self.assertIn(key, records)


if __name__ == "__main__":
    unittest.main()
