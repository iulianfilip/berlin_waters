"""Tests for the low-latency Foxglove Bridge launcher."""

from __future__ import annotations

import argparse
import unittest
from unittest.mock import patch

from tools.visualization.foxglove.run_bridge import (
    BridgeLaunchError,
    build_bridge_command,
    validate_ros_environment,
)


class BridgeCommandTests(unittest.TestCase):
    def test_command_uses_local_socket_sim_time_and_bounded_backlog(self) -> None:
        arguments = argparse.Namespace(address="127.0.0.1", port=8765, backlog=10)
        command = build_bridge_command(arguments)

        self.assertEqual(
            command[:4],
            ["ros2", "launch", "foxglove_bridge", "foxglove_bridge_launch.xml"],
        )
        self.assertIn("address:=127.0.0.1", command)
        self.assertIn("port:=8765", command)
        self.assertIn("use_sim_time:=true", command)
        self.assertIn("max_qos_depth:=1", command)
        self.assertIn("message_backlog_size:=10", command)
        self.assertIn("sysinfo:=false", command)

    @patch("tools.visualization.foxglove.run_bridge.shutil.which", return_value="/bin/ros2")
    def test_accepts_sourced_ros_environment(self, _: object) -> None:
        validate_ros_environment({"ROS_VERSION": "2", "ROS_DISTRO": "humble"})

    def test_rejects_unsourced_ros_environment(self) -> None:
        with self.assertRaisesRegex(BridgeLaunchError, "not sourced"):
            validate_ros_environment({})


if __name__ == "__main__":
    unittest.main()
