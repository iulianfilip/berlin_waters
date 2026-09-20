"""Tests for direct ROS 2 bag visualization with Rerun."""

from __future__ import annotations

import importlib.util
from types import SimpleNamespace
import unittest

from tools.visualization.rerun.view_bag import (
    BagViewPlan,
    PlaybackState,
    build_blueprint,
    build_view_plan,
    camera_entity,
    gnss_name,
    lidar_color,
    lidar_entity,
    lidar_sort_key,
    log_imu_series_styles,
    should_log_imu,
)


def connection(topic: str, message_type: str, count: int = 1) -> SimpleNamespace:
    return SimpleNamespace(topic=topic, msgtype=message_type, msgcount=count)


class DirectBagSelectionTests(unittest.TestCase):
    def test_selects_requested_modalities_and_only_xsens_imu(self) -> None:
        reader = SimpleNamespace(
            start_time=100,
            end_time=200,
            connections=[
                connection(
                    "/zed/zed_node/left/image_rect_color/compressed",
                    "sensor_msgs/msg/CompressedImage",
                    10,
                ),
                connection(
                    "/zed/zed_node/left/camera_info",
                    "sensor_msgs/msg/CameraInfo",
                    10,
                ),
                connection(
                    "/livox/lidar", "sensor_msgs/msg/PointCloud2", 8
                ),
                connection("/xsens/imu/data", "sensor_msgs/msg/Imu", 40),
                connection("/zed/zed_node/imu/data", "sensor_msgs/msg/Imu", 40),
                connection("/ublox_1/fix", "sensor_msgs/msg/NavSatFix", 5),
                connection("/water_depth", "geometry_msgs/msg/Vector3Stamped", 1),
            ],
        )

        plan = build_view_plan(reader)

        selected_topics = {item.topic for item in plan.connections}
        self.assertIn("/xsens/imu/data", selected_topics)
        self.assertNotIn("/zed/zed_node/imu/data", selected_topics)
        self.assertNotIn("/water_depth", selected_topics)
        self.assertEqual(plan.message_count, 73)
        self.assertEqual(plan.camera_entities, ("world/cameras/zed_left",))
        self.assertEqual(plan.lidar_entities, ("world/sensors/livox",))

    def test_entity_names_are_stable(self) -> None:
        self.assertEqual(
            camera_entity("/zed/zed_node/right/camera_info"),
            "world/cameras/zed_right",
        )
        self.assertEqual(lidar_entity("/VLP32/velodyne_points"), "world/sensors/velodyne")
        self.assertEqual(gnss_name("/ublox_3/fix"), "ublox_3")
        self.assertEqual(gnss_name("/xsens/gnss"), "xsens")

    def test_lidar_rgb_colors_and_panel_order_are_stable(self) -> None:
        entities = [
            "world/sensors/ouster",
            "world/sensors/livox",
            "world/sensors/velodyne",
        ]
        self.assertEqual(
            sorted(entities, key=lidar_sort_key),
            [
                "world/sensors/livox",
                "world/sensors/velodyne",
                "world/sensors/ouster",
            ],
        )
        self.assertEqual(lidar_color("/livox/lidar"), [255, 0, 0])
        self.assertEqual(lidar_color("/VLP32/velodyne_points"), [0, 255, 0])
        self.assertEqual(lidar_color("/ouster/points"), [0, 0, 255])


class ImuThrottlingTests(unittest.TestCase):
    def test_limits_visualized_imu_rate(self) -> None:
        state = PlaybackState(set(), {})
        self.assertTrue(should_log_imu("/xsens/imu/data", 0, 50.0, state))
        self.assertFalse(
            should_log_imu("/xsens/imu/data", 10_000_000, 50.0, state)
        )
        self.assertTrue(
            should_log_imu("/xsens/imu/data", 20_000_000, 50.0, state)
        )

    def test_zero_limit_keeps_every_sample(self) -> None:
        state = PlaybackState(set(), {})
        self.assertTrue(should_log_imu("/xsens/imu/data", 0, 0, state))
        self.assertTrue(should_log_imu("/xsens/imu/data", 1, 0, state))

    def test_series_styles_use_rgb_for_xyz(self) -> None:
        class FakeSeriesLines:
            def __init__(self, **kwargs: object) -> None:
                self.kwargs = kwargs

        class FakeRerun:
            SeriesLines = FakeSeriesLines

        class FakeRecording:
            def __init__(self) -> None:
                self.calls: list[tuple[str, FakeSeriesLines, bool]] = []

            def log(
                self, entity: str, value: FakeSeriesLines, *, static: bool
            ) -> None:
                self.calls.append((entity, value, static))

        recording = FakeRecording()

        log_imu_series_styles(FakeRerun, recording)

        self.assertEqual(len(recording.calls), 6)
        colors = {
            entity.rsplit("/", 1)[-1]: value.kwargs["colors"]
            for entity, value, static in recording.calls[:3]
            if static
        }
        self.assertEqual(
            colors,
            {"x": [255, 0, 0], "y": [0, 255, 0], "z": [0, 0, 255]},
        )


@unittest.skipUnless(
    importlib.util.find_spec("rerun") is not None,
    "rerun-sdk is not installed in this interpreter",
)
class DirectBagBlueprintTests(unittest.TestCase):
    def test_builds_all_sensors_dashboard(self) -> None:
        import rerun.blueprint as rrb

        plan = BagViewPlan(
            connections=(),
            camera_entities=("world/cameras/zed_left", "world/cameras/zed_right"),
            lidar_entities=("world/sensors/livox", "world/sensors/velodyne"),
            message_count=0,
            bag_start_ns=0,
            bag_end_ns=1,
        )

        blueprint = build_blueprint(rrb, plan)

        self.assertIsNotNone(blueprint)


if __name__ == "__main__":
    unittest.main()
