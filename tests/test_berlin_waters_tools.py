from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "berlin_waters.py"
spec = importlib.util.spec_from_file_location("berlin_waters_tools", MODULE_PATH)
bw = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(bw)


class BerlinWatersToolsTest(unittest.TestCase):
    def test_scene_catalog_has_22_unique_scenes(self):
        cfg = bw.load_yaml(ROOT / "metadata" / "scenes.yaml")
        ids = [scene["id"] for scene in cfg["scenes"]]
        self.assertEqual(len(ids), 22)
        self.assertEqual(len(set(ids)), 22)
        self.assertEqual(ids[0], "berlin_00")
        self.assertEqual(ids[-1], "berlin_21")

    def test_documented_ouster_availability(self):
        cfg = bw.load_yaml(ROOT / "metadata" / "scenes.yaml")
        expected = {
            "berlin_06", "berlin_07", "berlin_08", "berlin_09", "berlin_11",
            "berlin_18", "berlin_19", "berlin_20", "berlin_21",
        }
        actual = {
            scene["id"] for scene in cfg["scenes"]
            if scene["sensors"]["ouster"]
        }
        self.assertEqual(actual, expected)

    def test_validator_accepts_expected_topics(self):
        sensors = bw.load_yaml(ROOT / "metadata" / "sensors.yaml")
        scenes = bw.load_yaml(ROOT / "metadata" / "scenes.yaml")
        scene = bw.find_scene(scenes, "berlin_06")
        expected = bw.expected_topics(scene, sensors)
        entries = [
            {
                "topic_metadata": {
                    "name": topic,
                    "type": "example_msgs/msg/Example",
                    "serialization_format": "cdr",
                },
                "message_count": 10,
            }
            for topic in sorted(expected)
        ]
        metadata = {
            "rosbag2_bagfile_information": {
                "storage_identifier": "sqlite3",
                "duration": {"nanoseconds": 1_000_000_000},
                "message_count": 10 * len(entries),
                "topics_with_message_count": entries,
                "relative_file_paths": ["sample.db3"],
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            bag = Path(tmp) / "berlin_06"
            bag.mkdir()
            (bag / "metadata.yaml").write_text(yaml.safe_dump(metadata), encoding="utf-8")
            present = {t["name"] for t in bw.topics(bw.load_bag(bag))}
        self.assertEqual(expected, present)


if __name__ == "__main__":
    unittest.main()
