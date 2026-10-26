#!/usr/bin/env python3
"""Publish the complete Berlin Waters static sensor tree from one ROS node."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import sys

try:
    from .run_rviz import RvizLaunchError, load_static_transforms
except ImportError:  # Direct script execution adds this directory to sys.path.
    from run_rviz import RvizLaunchError, load_static_transforms


def quaternion_from_rpy(
    roll: float, pitch: float, yaw: float
) -> tuple[float, float, float, float]:
    """Convert fixed-axis roll, pitch, yaw to an x, y, z, w quaternion."""
    cr, sr = math.cos(roll / 2.0), math.sin(roll / 2.0)
    cp, sp = math.cos(pitch / 2.0), math.sin(pitch / 2.0)
    cy, sy = math.cos(yaw / 2.0), math.sin(yaw / 2.0)
    return (
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
        cr * cp * cy + sr * sp * sy,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transforms", type=Path)
    arguments = parser.parse_args()
    try:
        transforms = load_static_transforms(arguments.transforms.resolve())
    except RvizLaunchError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    try:
        import rclpy
        from geometry_msgs.msg import TransformStamped
        from tf2_ros.static_transform_broadcaster import StaticTransformBroadcaster
    except ImportError as error:
        print(
            "error: ROS 2 Python packages are unavailable; source the ROS 2 setup",
            file=sys.stderr,
        )
        return 2

    rclpy.init()
    node = rclpy.create_node("berlin_waters_static_tf")
    broadcaster = StaticTransformBroadcaster(node)
    messages = []
    stamp = node.get_clock().now().to_msg()
    for transform in transforms:
        message = TransformStamped()
        message.header.stamp = stamp
        message.header.frame_id = transform.parent_frame
        message.child_frame_id = transform.child_frame
        message.transform.translation.x = transform.translation[0]
        message.transform.translation.y = transform.translation[1]
        message.transform.translation.z = transform.translation[2]
        quaternion = quaternion_from_rpy(*transform.rotation_rpy)
        message.transform.rotation.x = quaternion[0]
        message.transform.rotation.y = quaternion[1]
        message.transform.rotation.z = quaternion[2]
        message.transform.rotation.w = quaternion[3]
        messages.append(message)

    broadcaster.sendTransform(messages)
    node.get_logger().info(f"published {len(messages)} static sensor transforms")
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
