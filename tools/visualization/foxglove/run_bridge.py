#!/usr/bin/env python3
"""Launch a low-latency local Foxglove WebSocket bridge for ROS 2."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from typing import Mapping, Sequence


class BridgeLaunchError(RuntimeError):
    """A user-facing bridge configuration error."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--address",
        default="127.0.0.1",
        help="WebSocket bind address (default: %(default)s)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="WebSocket port (default: %(default)s)",
    )
    parser.add_argument(
        "--backlog",
        type=int,
        default=10,
        help="messages buffered per client before oldest data is dropped (default: %(default)s)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and print the ROS command without starting the bridge",
    )
    return parser


def validate_arguments(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> None:
    if not 1 <= arguments.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if arguments.backlog < 1:
        parser.error("--backlog must be at least 1")


def validate_ros_environment(environment: Mapping[str, str] = os.environ) -> None:
    if environment.get("ROS_VERSION") != "2" or not environment.get("ROS_DISTRO"):
        raise BridgeLaunchError(
            "a ROS 2 environment is not sourced; run "
            "'source /opt/ros/humble/setup.bash' first"
        )
    if shutil.which("ros2") is None:
        raise BridgeLaunchError("ros2 was not found in PATH")


def bridge_package_available() -> bool:
    result = subprocess.run(
        ["ros2", "pkg", "prefix", "foxglove_bridge"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def build_bridge_command(arguments: argparse.Namespace) -> list[str]:
    return [
        "ros2",
        "launch",
        "foxglove_bridge",
        "foxglove_bridge_launch.xml",
        f"address:={arguments.address}",
        f"port:={arguments.port}",
        "use_sim_time:=true",
        "min_qos_depth:=1",
        "max_qos_depth:=1",
        f"message_backlog_size:={arguments.backlog}",
        "sysinfo:=false",
        "capabilities:=[connectionGraph]",
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    validate_arguments(parser, arguments)
    try:
        validate_ros_environment()
        if not bridge_package_available():
            raise BridgeLaunchError(
                "foxglove_bridge is not installed; install "
                "ros-$ROS_DISTRO-foxglove-bridge"
            )
    except BridgeLaunchError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    command = build_bridge_command(arguments)
    print(f"Foxglove WebSocket: ws://{arguments.address}:{arguments.port}")
    print("Bridge command:", " ".join(command))
    if arguments.dry_run:
        return 0
    try:
        return subprocess.run(command, check=False).returncode
    except KeyboardInterrupt:
        return 130
    except OSError as error:
        print(f"error: could not launch foxglove_bridge: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
