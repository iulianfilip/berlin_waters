"""Tests for the extraction manifest contract."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.extraction.extract import (
    BagInspection,
    SelectionResult,
    TopicExtractionResult,
    build_manifest,
    build_topic_plans,
    sanitize_topic_name,
    write_manifest,
)


class ManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        selection = SelectionResult(
            selected_topics=frozenset({"/livox/lidar", "/ouster/points"}),
            missing_topics=("/missing/topic",),
            unmatched_sensors=(),
            sensors=("ouster",),
            requested_topics=("/livox/lidar", "/missing/topic"),
        )
        plans = build_topic_plans(
            [
                ("/livox/lidar", "livox_ros_driver2/msg/CustomMsg", 4),
                ("/ouster/imu", "sensor_msgs/msg/Imu", 20),
                ("/ouster/points", "sensor_msgs/msg/PointCloud2", 10),
            ],
            selection.selected_topics,
        )
        self.inspection = BagInspection(
            bag_path=Path("/data/berlin_06"),
            topics=plans,
            selection=selection,
            message_count=34,
            start_timestamp_ns=1_000,
            end_timestamp_ns_exclusive=2_001,
        )

    def test_manifest_records_source_and_selection(self) -> None:
        manifest = build_manifest(self.inspection)

        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["mode"], "inspection")
        self.assertEqual(manifest["source"]["bag"], "/data/berlin_06")
        self.assertEqual(manifest["source"]["message_count"], 34)
        self.assertEqual(manifest["source"]["start_timestamp_ns"], 1_000)
        self.assertEqual(
            manifest["selection"]["topics"],
            ["/livox/lidar", "/missing/topic"],
        )
        self.assertEqual(manifest["selection"]["missing_topics"], ["/missing/topic"])

    def test_manifest_distinguishes_topic_outcomes(self) -> None:
        manifest = build_manifest(self.inspection)
        topics = {entry["topic"]: entry for entry in manifest["topics"]}

        livox = topics["/livox/lidar"]
        self.assertEqual(livox["status"], "skipped")
        self.assertIn("custom Livox", livox["reason"])
        self.assertIsNone(livox["output"])

        points = topics["/ouster/points"]
        self.assertEqual(points["status"], "planned")
        self.assertEqual(points["source_message_count"], 10)
        self.assertEqual(points["extracted_message_count"], 0)
        self.assertEqual(points["failed_message_count"], 0)
        self.assertEqual(points["output"]["format"], "pointcloud")
        self.assertEqual(points["output"]["path"], "pointclouds/ouster_points")
        self.assertIsNone(points["timestamp_range_ns"])

        imu = topics["/ouster/imu"]
        self.assertEqual(imu["status"], "filtered")
        self.assertEqual(imu["reason"], "topic was not selected")
        self.assertIsNone(imu["output"])

    def test_completed_camera_result_updates_manifest(self) -> None:
        camera_plan = build_topic_plans(
            [
                (
                    "/zed/image/compressed",
                    "sensor_msgs/msg/CompressedImage",
                    2,
                )
            ],
            frozenset({"/zed/image/compressed"}),
        )
        inspection = BagInspection(
            bag_path=Path("/data/berlin_06"),
            topics=camera_plan,
            selection=SelectionResult(
                selected_topics=frozenset({"/zed/image/compressed"}),
                missing_topics=(),
                unmatched_sensors=(),
                sensors=("zed2i",),
                requested_topics=(),
            ),
            message_count=2,
            start_timestamp_ns=100,
            end_timestamp_ns_exclusive=201,
        )
        result = TopicExtractionResult(
            status="completed",
            reason=None,
            extracted_message_count=2,
            failed_message_count=0,
            start_timestamp_ns=100,
            end_timestamp_ns=200,
            index_path="images/zed_image_compressed/index.csv",
        )

        manifest = build_manifest(
            inspection,
            {"/zed/image/compressed": result},
        )

        topic = manifest["topics"][0]
        self.assertEqual(manifest["mode"], "extraction")
        self.assertEqual(topic["status"], "completed")
        self.assertIsNone(topic["reason"])
        self.assertEqual(topic["extracted_message_count"], 2)
        self.assertEqual(topic["failed_message_count"], 0)
        self.assertEqual(topic["timestamp_range_ns"], {"start": 100, "end": 200})
        self.assertEqual(
            topic["output"]["index_path"],
            "images/zed_image_compressed/index.csv",
        )

    def test_manifest_is_written_as_valid_json(self) -> None:
        manifest = build_manifest(self.inspection)
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_directory = Path(temporary_directory) / "nested" / "output"

            manifest_path = write_manifest(manifest, output_directory)

            self.assertEqual(manifest_path, output_directory / "manifest.json")
            self.assertEqual(
                json.loads(manifest_path.read_text(encoding="utf-8")),
                manifest,
            )
            self.assertFalse((output_directory / ".manifest.json.tmp").exists())

    def test_topic_name_is_sanitized_for_relative_paths(self) -> None:
        self.assertEqual(
            sanitize_topic_name("/zed/left image/compressed"),
            "zed_left_image_compressed",
        )


if __name__ == "__main__":
    unittest.main()
