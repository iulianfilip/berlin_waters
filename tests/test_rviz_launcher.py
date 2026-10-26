"""Tests for the ROS 2 RViz and static-TF launcher."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from tools.visualization.ros2.run_rviz import (
    RvizLaunchError,
    build_parser,
    build_rviz_command,
    build_transform_command,
    load_static_transforms,
    validate_ros_environment,
)


class CommandConstructionTests(unittest.TestCase):
    def test_rviz_uses_config_and_simulation_time(self) -> None:
        self.assertEqual(
            build_rviz_command(Path("/repo/berlin_waters.rviz")),
            [
                "rviz2", "-d", "/repo/berlin_waters.rviz",
                "--ros-args", "-p", "use_sim_time:=true",
            ],
        )

    def test_static_transform_command_uses_explicit_flags(self) -> None:
        command = build_transform_command(
            Path("/repo/static_transforms.yaml"), python_executable="python3"
        )
        self.assertEqual(
            command,
            [
                "python3",
                str(
                    Path(__file__).parents[1]
                    / "tools"
                    / "visualization"
                    / "ros2"
                    / "publish_static_tf.py"
                ),
                "/repo/static_transforms.yaml",
            ],
        )

    def test_bag_is_not_a_launcher_argument(self) -> None:
        arguments = build_parser().parse_args([])
        self.assertFalse(hasattr(arguments, "bag"))


class TransformTests(unittest.TestCase):
    def test_loads_transform_yaml(self) -> None:
        document = {
            "transforms": [{
                "parent_frame": "imu_link",
                "child_frame": "velodyne_VLP32",
                "translation": [0, 0, 0.1908],
                "rotation_rpy": [0, 0, 0],
            }]
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "transforms.yaml"
            path.write_text(yaml.safe_dump(document), encoding="utf-8")
            transforms = load_static_transforms(path)
        self.assertEqual(transforms[0].child_frame, "velodyne_VLP32")
        self.assertEqual(transforms[0].translation, (0.0, 0.0, 0.1908))

    def test_rejects_duplicate_child_frames(self) -> None:
        entry = {
            "parent_frame": "imu_link",
            "child_frame": "lidar",
            "translation": [0, 0, 0],
            "rotation_rpy": [0, 0, 0],
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "transforms.yaml"
            path.write_text(
                yaml.safe_dump({"transforms": [entry, entry]}), encoding="utf-8"
            )
            with self.assertRaisesRegex(RvizLaunchError, "duplicate child"):
                load_static_transforms(path)


class EnvironmentAndConfigTests(unittest.TestCase):
    @patch("tools.visualization.ros2.run_rviz.shutil.which", return_value="/bin/tool")
    def test_accepts_sourced_ros2_environment(self, _: object) -> None:
        validate_ros_environment({"ROS_VERSION": "2", "ROS_DISTRO": "humble"})

    def test_rejects_unsourced_ros2_environment(self) -> None:
        with self.assertRaisesRegex(RvizLaunchError, "not sourced"):
            validate_ros_environment({})

    def test_default_files_describe_connected_sensor_tree(self) -> None:
        ros_directory = Path(__file__).parents[1] / "tools" / "visualization" / "ros2"
        config = yaml.safe_load(
            (ros_directory / "berlin_waters.rviz").read_text(encoding="utf-8")
        )
        transforms = load_static_transforms(ros_directory / "static_transforms.yaml")
        manager = config["Visualization Manager"]
        displays = {display["Name"]: display for display in manager["Displays"]}

        self.assertEqual(manager["Global Options"]["Fixed Frame"], "imu_link")
        self.assertEqual(
            {transform.child_frame for transform in transforms},
            {
                "velodyne_VLP32", "livox_frame", "os_sensor", "zed_imu_link",
                "zed_left_camera_optical_frame", "zed_right_camera_optical_frame",
            },
        )
        self.assertEqual(displays["Velodyne"]["Color"], "0; 255; 0")
        self.assertEqual(displays["Livox"]["Color"], "255; 0; 0")
        self.assertEqual(displays["Ouster"]["Color"], "0; 0; 255")
        for name in ("Velodyne", "Livox", "Ouster"):
            self.assertEqual(displays[name]["Topic"]["Depth"], 1)
            self.assertEqual(displays[name]["Topic"]["Filter size"], 1)
            self.assertFalse(displays[name]["Selectable"])
        self.assertEqual(displays["ZED Left"]["Topic"]["Depth"], 1)
        self.assertEqual(displays["ZED Right"]["Topic"]["Depth"], 1)


if __name__ == "__main__":
    unittest.main()
