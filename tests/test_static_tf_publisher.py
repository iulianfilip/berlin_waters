"""Tests for static-TF math that do not require a running ROS graph."""

from __future__ import annotations

import math
import unittest

from tools.visualization.ros2.publish_static_tf import quaternion_from_rpy


class QuaternionTests(unittest.TestCase):
    def test_identity_rotation(self) -> None:
        self.assertEqual(quaternion_from_rpy(0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0))

    def test_camera_optical_rotation_is_unit_quaternion(self) -> None:
        quaternion = quaternion_from_rpy(-math.pi / 2.0, 0.0, -math.pi / 2.0)
        magnitude = math.sqrt(sum(value * value for value in quaternion))
        self.assertAlmostEqual(magnitude, 1.0)


if __name__ == "__main__":
    unittest.main()
