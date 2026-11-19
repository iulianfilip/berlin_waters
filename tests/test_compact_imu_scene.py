"""End-to-end checks for the compact IMU scene export."""

from contextlib import redirect_stdout, redirect_stderr
import csv
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from tools.extraction.extract import main

try:
    from rosbags.rosbag2 import Writer
    from rosbags.typesys import Stores, get_typestore
except ImportError:
    Writer = None


@unittest.skipIf(Writer is None, "rosbags is required for bag integration tests")
class CompactImuSceneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bag = self.root / "berlin_06"
        store = get_typestore(Stores.ROS2_HUMBLE)
        types = store.types
        imu_type = "sensor_msgs/msg/Imu"
        marker_type = "std_msgs/msg/String"
        imu = types[imu_type](
            header=types["std_msgs/msg/Header"](
                stamp=types["builtin_interfaces/msg/Time"](sec=123, nanosec=4),
                frame_id="imu_link",
            ),
            orientation=types["geometry_msgs/msg/Quaternion"](x=0., y=0., z=0., w=1.),
            orientation_covariance=np.zeros(9),
            angular_velocity=types["geometry_msgs/msg/Vector3"](x=1., y=2., z=3.),
            angular_velocity_covariance=np.zeros(9),
            linear_acceleration=types["geometry_msgs/msg/Vector3"](x=4., y=5., z=6.),
            linear_acceleration_covariance=np.zeros(9),
        )
        with Writer(self.bag, version=9) as writer:
            marker_connection = writer.add_connection(
                "/marker", marker_type, typestore=store
            )
            imu_connection = writer.add_connection(
                "/xsens/imu/data", imu_type, typestore=store
            )
            marker = store.serialize_cdr(types[marker_type](data="bound"), marker_type)
            writer.write(marker_connection, 1_700_000_000_000_000_001, marker)
            writer.write(
                imu_connection, 1_700_000_001_123_456_789,
                store.serialize_cdr(imu, imu_type),
            )
            writer.write(marker_connection, 1_700_000_002_987_654_321, marker)

    def run_export(self, *extra: str) -> int:
        with patch("tools.extraction.extract.Path.cwd", return_value=self.root):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                return main([
                    str(self.bag), *extra, "--topic", "/xsens/imu/data",
                    "--imu-format", "compact",
                ])

    def test_scene_directory_contains_only_csv_and_info_with_global_bag_bounds(self) -> None:
        self.assertEqual(self.run_export(), 0)
        output = self.root / "extracted" / "berlin_06"
        self.assertEqual(
            {p.name for p in output.iterdir()},
            {"berlin_06_imu.csv", "berlin_06_info.txt"},
        )
        with (output / "berlin_06_imu.csv").open() as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(rows, [
            ["timestamp_unix", "acc_x", "acc_y", "acc_z", "gyro_x", "gyro_y", "gyro_z"],
            ["1700000001.123456789", "4.0", "5.0", "6.0", "1.0", "2.0", "3.0"],
        ])
        info = (output / "berlin_06_info.txt").read_text()
        self.assertEqual(info, (
            "Scene: berlin_06\n\n"
            "Bag start timestamp (Unix seconds): 1700000000.00\n"
            "Bag finish timestamp (Unix seconds): 1700000002.99\n\n"
            "CSV columns and units:\n"
            "1. timestamp_unix: Unix seconds\n"
            "2. acc_x: linear_acceleration.x [m/s^2]\n"
            "3. acc_y: linear_acceleration.y [m/s^2]\n"
            "4. acc_z: linear_acceleration.z [m/s^2]\n"
            "5. gyro_x: angular_velocity.x [rad/s]\n"
            "6. gyro_y: angular_velocity.y [rad/s]\n"
            "7. gyro_z: angular_velocity.z [rad/s]\n"
        ))

    def test_existing_unrelated_files_are_preserved_and_export_rejected(self) -> None:
        output = self.root / "custom"
        output.mkdir()
        original = output / "manifest.json"
        original.write_text("existing export")
        self.assertEqual(self.run_export(str(output)), 2)
        self.assertEqual(original.read_text(), "existing export")
        self.assertEqual(list(output.iterdir()), [original])

    def test_multiple_selected_topics_are_rejected(self) -> None:
        self.assertEqual(self.run_export("--topic", "/marker"), 2)
        self.assertFalse((self.root / "extracted" / "berlin_06").exists())


if __name__ == "__main__":
    unittest.main()
