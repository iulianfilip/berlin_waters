"""Tests for the non-ROS Rerun visualization data adapters."""

from __future__ import annotations

import base64
import json
import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

import numpy as np

from tools.extraction.extract import DecodedPointCloud, write_pcd, write_ply
from tools.visualization.rerun.view import (
    ViewerError,
    downsample_points,
    intensity_colors,
    load_manifest,
    parse_unix_timestamp_ns,
    read_pcd,
    read_ply,
    resolve_relative,
    rerun_viewer_on_path,
    topic_entity_path,
    main,
)


class ManifestAndTimelineTests(unittest.TestCase):
    def test_manifest_schema_is_validated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "manifest.json").write_text(
                json.dumps({"schema_version": 1, "topics": []}),
                encoding="utf-8",
            )
            self.assertEqual(load_manifest(root)["topics"], [])

    def test_timestamp_parser_preserves_nanoseconds(self) -> None:
        self.assertEqual(
            parse_unix_timestamp_ns("1747902065.008561527"),
            1_747_902_065_008_561_527,
        )
        self.assertEqual(parse_unix_timestamp_ns("12.34"), 12_340_000_000)

    def test_relative_paths_cannot_escape_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaisesRegex(ViewerError, "escapes"):
                resolve_relative(Path(temporary_directory), "../outside.pcd")

    def test_known_topics_have_readable_entity_paths(self) -> None:
        self.assertEqual(
            topic_entity_path(
                "/zed/zed_node/left/image_rect_color/compressed",
                "sensor_msgs/msg/CompressedImage",
            ),
            "world/cameras/zed_left/image",
        )
        self.assertEqual(
            topic_entity_path(
                "/livox/lidar",
                "sensor_msgs/msg/PointCloud2",
            ),
            "world/sensors/livox/points",
        )


class PointCloudLoadingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.points = np.array(
            [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]],
            dtype=np.float32,
        )
        self.intensity = np.array([10.0, 20.0, 30.0], dtype=np.float32)
        self.cloud = DecodedPointCloud(self.points, self.intensity)

    def test_reads_binary_pcd_from_extractor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "scan.pcd"
            write_pcd(path, self.cloud)
            points, intensity = read_pcd(path)
        np.testing.assert_allclose(points, self.points)
        np.testing.assert_allclose(intensity, self.intensity)

    def test_reads_binary_ply_from_extractor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "scan.ply"
            write_ply(path, self.cloud)
            points, intensity = read_ply(path)
        np.testing.assert_allclose(points, self.points)
        np.testing.assert_allclose(intensity, self.intensity)

    def test_downsampling_keeps_matching_intensity(self) -> None:
        points, intensity = downsample_points(self.points, self.intensity, 2)
        np.testing.assert_allclose(points, self.points[[0, 2]])
        np.testing.assert_allclose(intensity, self.intensity[[0, 2]])

    def test_intensity_is_mapped_to_grayscale(self) -> None:
        colors = intensity_colors(self.intensity)
        self.assertEqual(colors.shape, (3, 3))
        np.testing.assert_array_equal(colors[:, 0], colors[:, 1])
        np.testing.assert_array_equal(colors[:, 1], colors[:, 2])
        self.assertEqual(int(colors[0, 0]), 0)
        self.assertEqual(int(colors[-1, 0]), 255)


class RerunSdkIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(
        (Path(sys.executable).parent / "rerun").is_file(),
        "Rerun executable is not installed beside this Python interpreter",
    )
    def test_venv_viewer_is_found_without_activating_venv(self) -> None:
        original_path = os.environ.get("PATH")
        os.environ["PATH"] = ""
        try:
            self.assertIsNone(shutil.which("rerun"))
            with rerun_viewer_on_path():
                self.assertEqual(
                    shutil.which("rerun"),
                    str(Path(sys.executable).parent / "rerun"),
                )
            self.assertEqual(os.environ["PATH"], "")
        finally:
            if original_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = original_path

    @unittest.skipUnless(
        importlib.util.find_spec("rerun") is not None,
        "rerun-sdk is not installed in this interpreter",
    )
    def test_writes_a_small_rrd_recording(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            cloud_directory = root / "pointclouds" / "lidar"
            cloud_directory.mkdir(parents=True)
            write_pcd(
                cloud_directory / "000000.pcd",
                DecodedPointCloud(
                    np.array([[1.0, 2.0, 3.0]], dtype=np.float32),
                    np.array([42.0], dtype=np.float32),
                ),
            )
            (cloud_directory / "index.csv").write_text(
                '"filename","timestamp_unix"\n'
                '"000000.pcd","1747902065.008561527"\n',
                encoding="utf-8",
            )
            image_directory = root / "images" / "left"
            image_directory.mkdir(parents=True)
            (image_directory / "000000.png").write_bytes(
                base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNg"
                    "YAAAAAMAASsJTYQAAAAASUVORK5CYII="
                )
            )
            (image_directory / "index.csv").write_text(
                '"filename","timestamp_unix"\n'
                '"000000.png","1747902065.008561527"\n',
                encoding="utf-8",
            )
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "topics": [
                            {
                                "topic": "/zed/zed_node/left/image_rect_color/compressed",
                                "message_type": "sensor_msgs/msg/CompressedImage",
                                "status": "completed",
                                "output": {
                                    "format": "compressed_image",
                                    "path": "images/left",
                                    "index_path": "images/left/index.csv",
                                },
                            },
                            {
                                "topic": "/livox/lidar",
                                "message_type": "sensor_msgs/msg/PointCloud2",
                                "status": "completed",
                                "output": {
                                    "format": "pcd",
                                    "path": "pointclouds/lidar",
                                    "index_path": "pointclouds/lidar/index.csv",
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            recording_path = root / "smoke.rrd"

            exit_code = main([str(root), "--save", str(recording_path)])

            self.assertEqual(exit_code, 0)
            self.assertTrue(recording_path.is_file())
            self.assertGreater(recording_path.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
