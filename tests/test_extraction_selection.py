"""Tests for extraction topic selection and support classification."""

from __future__ import annotations

import unittest

from tools.extraction.extract import (
    build_topic_plans,
    classify_message_type,
    normalize_sensor,
    select_topic_names,
)


TOPICS = {
    "/VLP32/velodyne_points",
    "/livox/lidar",
    "/ouster/imu",
    "/ouster/points",
    "/ublox_1/fix",
    "/zed/zed_node/left/image_rect_color/compressed",
}


class TopicSelectionTests(unittest.TestCase):
    def test_no_selectors_selects_every_topic(self) -> None:
        result = select_topic_names(TOPICS)

        self.assertEqual(result.selected_topics, TOPICS)
        self.assertEqual(result.missing_topics, ())
        self.assertEqual(result.unmatched_sensors, ())

    def test_sensor_selects_its_namespace(self) -> None:
        result = select_topic_names(TOPICS, sensors=["ouster"])

        self.assertEqual(
            result.selected_topics,
            {"/ouster/imu", "/ouster/points"},
        )

    def test_dataset_sensor_aliases_are_supported(self) -> None:
        self.assertEqual(normalize_sensor("VLP32"), "velodyne")
        self.assertEqual(normalize_sensor("zed"), "zed2i")

        result = select_topic_names(TOPICS, sensors=["zed2i"])
        self.assertEqual(
            result.selected_topics,
            {"/zed/zed_node/left/image_rect_color/compressed"},
        )

    def test_sensor_and_exact_topic_form_a_union(self) -> None:
        result = select_topic_names(
            TOPICS,
            sensors=["ouster"],
            requested_topics=["ublox_1/fix"],
        )

        self.assertEqual(
            result.selected_topics,
            {"/ouster/imu", "/ouster/points", "/ublox_1/fix"},
        )

    def test_missing_selectors_are_reported(self) -> None:
        result = select_topic_names(
            TOPICS,
            sensors=["xsens"],
            requested_topics=["/missing/topic"],
        )

        self.assertEqual(result.selected_topics, set())
        self.assertEqual(result.missing_topics, ("/missing/topic",))
        self.assertEqual(result.unmatched_sensors, ("xsens",))

    def test_unknown_sensor_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown sensor"):
            select_topic_names(TOPICS, sensors=["camera_42"])


class MessageSupportTests(unittest.TestCase):
    def test_standard_pointcloud_is_supported(self) -> None:
        output_type, reason = classify_message_type("sensor_msgs/msg/PointCloud2")

        self.assertEqual(output_type, "PCD/PLY point clouds")
        self.assertIsNone(reason)

    def test_custom_livox_type_has_specific_reason(self) -> None:
        output_type, reason = classify_message_type("livox_ros_driver2/msg/CustomMsg")

        self.assertIsNone(output_type)
        self.assertIn("custom Livox", reason or "")

    def test_velocity_is_outside_current_scope(self) -> None:
        output_type, reason = classify_message_type(
            "geometry_msgs/msg/TwistWithCovarianceStamped"
        )

        self.assertIsNone(output_type)
        self.assertIn("not supported", reason or "")

    def test_topic_plans_are_sorted_and_mark_selection(self) -> None:
        plans = build_topic_plans(
            [
                ("/zed/image", "sensor_msgs/msg/CompressedImage", 12),
                ("/livox/lidar", "livox_ros_driver2/msg/CustomMsg", 7),
            ],
            frozenset({"/livox/lidar"}),
        )

        self.assertEqual([plan.topic for plan in plans], ["/livox/lidar", "/zed/image"])
        self.assertTrue(plans[0].selected)
        self.assertFalse(plans[0].supported)
        self.assertFalse(plans[1].selected)
        self.assertTrue(plans[1].supported)
        self.assertEqual(plans[1].message_count, 12)


if __name__ == "__main__":
    unittest.main()
