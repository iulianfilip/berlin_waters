"""Structural tests for the reusable Foxglove layout."""

from __future__ import annotations

import json
from pathlib import Path
import unittest


LAYOUT_PATH = (
    Path(__file__).parents[1]
    / "tools"
    / "visualization"
    / "foxglove"
    / "berlin_waters.layout.json"
)


class FoxgloveLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.layout = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
        cls.config = cls.layout["configById"]

    def test_layout_references_only_configured_panels(self) -> None:
        def panel_ids(node: object) -> set[str]:
            if isinstance(node, str):
                return {node}
            self.assertIsInstance(node, dict)
            return panel_ids(node["first"]) | panel_ids(node["second"])

        self.assertEqual(panel_ids(self.layout["layout"]), set(self.config))

    def test_stereo_topics_and_calibration_are_configured(self) -> None:
        left = self.config["Image!bwleft1"]["imageMode"]
        right = self.config["Image!bwright"]["imageMode"]
        self.assertEqual(
            left["imageTopic"],
            "/zed/zed_node/left/image_rect_color/compressed",
        )
        self.assertEqual(left["calibrationTopic"], "/zed/zed_node/left/camera_info")
        self.assertEqual(
            right["imageTopic"],
            "/zed/zed_node/right/image_rect_color/compressed",
        )
        self.assertEqual(
            right["calibrationTopic"], "/zed/zed_node/right/camera_info"
        )

    def test_map_enables_all_gnss_topics(self) -> None:
        panel = self.config["Map!bwgnss01"]
        self.assertEqual(panel["layer"], "map")
        self.assertEqual(panel["followTopic"], "/xsens/gnss")
        self.assertEqual(panel["disabledTopics"], [])
        self.assertEqual(
            set(panel["topicColors"]),
            {"/ublox_1/fix", "/ublox_2/fix", "/ublox_3/fix", "/xsens/gnss"},
        )

    def test_imu_axes_use_rgb_and_header_time(self) -> None:
        expected_colors = ["#ff0000", "#00ff00", "#0066ff"]
        for panel_id in ("Plot!bwgyro1", "Plot!bwaccel"):
            paths = self.config[panel_id]["paths"]
            self.assertEqual([path["color"] for path in paths], expected_colors)
            self.assertTrue(all(path["timestampMethod"] == "headerStamp" for path in paths))

    def test_lidar_panels_are_independent_and_rgb_colored(self) -> None:
        expected = {
            "3D!bwlivox": ("/livox/lidar", "livox_frame", "#ff0000ff"),
            "3D!bwvelod": (
                "/VLP32/velodyne_points",
                "velodyne_VLP32",
                "#00ff00ff",
            ),
            "3D!bwoustr": ("/ouster/points", "os_sensor", "#0066ffff"),
        }
        for panel_id, (topic, frame, color) in expected.items():
            panel = self.config[panel_id]
            self.assertEqual(panel["followTf"], frame)
            self.assertEqual(set(panel["topics"]), {topic})
            self.assertEqual(panel["topics"][topic]["flatColor"], color)
            self.assertEqual(panel["topics"][topic]["decayTime"], 0)


if __name__ == "__main__":
    unittest.main()
