#!/usr/bin/env python3
"""Launch Berlin Waters RViz and its static sensor transform tree."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence


DEFAULT_RVIZ_CONFIG = Path(__file__).with_name("berlin_waters.rviz")
DEFAULT_TRANSFORMS = Path(__file__).with_name("static_transforms.yaml")
TF_PUBLISHER = Path(__file__).with_name("publish_static_tf.py")


class RvizLaunchError(RuntimeError):
    """A user-facing launcher configuration or process error."""


@dataclass(frozen=True)
class StaticTransform:
    """One parent-to-child static transform."""

    parent_frame: str
    child_frame: str
    translation: tuple[float, float, float]
    rotation_rpy: tuple[float, float, float]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Launch RViz and the Berlin Waters static TF tree. Play the ROS 2 "
            "bag separately with 'ros2 bag play BAG --clock'."
        )
    )
    parser.add_argument(
        "--rviz-config",
        type=Path,
        default=DEFAULT_RVIZ_CONFIG,
        help="RViz configuration file (default: %(default)s)",
    )
    parser.add_argument(
        "--transforms",
        type=Path,
        default=DEFAULT_TRANSFORMS,
        help="static-transform YAML file (default: %(default)s)",
    )
    parser.add_argument(
        "--no-transforms",
        action="store_true",
        help="open RViz without publishing the static sensor tree",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and print commands without launching processes",
    )
    return parser


def validate_ros_environment(environment: Mapping[str, str] = os.environ) -> None:
    if environment.get("ROS_VERSION") != "2" or not environment.get("ROS_DISTRO"):
        raise RvizLaunchError(
            "a ROS 2 environment is not sourced; run "
            "'source /opt/ros/humble/setup.bash' first"
        )
    missing = [name for name in ("ros2", "rviz2") if shutil.which(name) is None]
    if missing:
        raise RvizLaunchError(
            "required ROS executable(s) not found in PATH: " + ", ".join(missing)
        )


def load_static_transforms(path: Path) -> list[StaticTransform]:
    """Load and validate static transforms from YAML."""
    try:
        import yaml
    except ImportError as error:
        raise RvizLaunchError("PyYAML is required to load static transforms") from error

    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        entries = document["transforms"]
        if not isinstance(entries, list) or not entries:
            raise ValueError("'transforms' must be a non-empty list")
        transforms = []
        children = set()
        for index, entry in enumerate(entries):
            parent = str(entry["parent_frame"]).strip()
            child = str(entry["child_frame"]).strip()
            translation = tuple(float(value) for value in entry["translation"])
            rotation = tuple(float(value) for value in entry["rotation_rpy"])
            if not parent or not child or parent == child:
                raise ValueError(f"transform {index} has invalid frame names")
            if len(translation) != 3 or len(rotation) != 3:
                raise ValueError(
                    f"transform {index} needs three translation and rotation values"
                )
            if child in children:
                raise ValueError(f"duplicate child frame: {child}")
            children.add(child)
            transforms.append(StaticTransform(parent, child, translation, rotation))
        return transforms
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as error:
        raise RvizLaunchError(f"could not load transforms from {path}: {error}") from error


def build_rviz_command(config_path: Path) -> list[str]:
    return [
        "rviz2",
        "-d",
        str(config_path),
        "--ros-args",
        "-p",
        "use_sim_time:=true",
    ]


def build_transform_command(
    transform_path: Path, python_executable: str = sys.executable
) -> list[str]:
    """Build one publisher command for the complete static tree."""
    return [python_executable, str(TF_PUBLISHER), str(transform_path)]


def stop_process(process: subprocess.Popen[Any] | None) -> None:
    if process is None or process.poll() is not None:
        return
    for stop_signal, timeout in (
        (signal.SIGINT, 3.0),
        (signal.SIGTERM, 2.0),
        (signal.SIGKILL, 1.0),
    ):
        try:
            os.killpg(process.pid, stop_signal)
            process.wait(timeout=timeout)
            return
        except ProcessLookupError:
            return
        except subprocess.TimeoutExpired:
            continue


def run_rviz_and_transforms(
    rviz_command: Sequence[str], transform_command: Sequence[str] | None
) -> int:
    """Run RViz and keep the static-tree publisher alive with it."""
    publisher: subprocess.Popen[Any] | None = None
    rviz_process: subprocess.Popen[Any] | None = None
    try:
        if transform_command:
            publisher = subprocess.Popen(transform_command, start_new_session=True)
            time.sleep(0.5)
            publisher_exit = publisher.poll()
            if publisher_exit is not None:
                raise RvizLaunchError(
                    "the static-transform publisher exited during startup "
                    f"with code {publisher_exit}"
                )

        rviz_process = subprocess.Popen(rviz_command, start_new_session=True)
        while True:
            rviz_exit = rviz_process.poll()
            if rviz_exit is not None:
                return int(rviz_exit)
            publisher_exit = publisher.poll() if publisher else None
            if publisher_exit is not None:
                raise RvizLaunchError(
                    "the static-transform publisher exited unexpectedly "
                    f"with code {publisher_exit}"
                )
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nStopping RViz and static TF publishers...")
        return 130
    finally:
        stop_process(rviz_process)
        stop_process(publisher)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    config_path = arguments.rviz_config.expanduser().resolve()
    transform_path = arguments.transforms.expanduser().resolve()
    try:
        validate_ros_environment()
        if not config_path.is_file():
            raise RvizLaunchError(f"RViz configuration does not exist: {config_path}")
        transforms = [] if arguments.no_transforms else load_static_transforms(transform_path)
        rviz_command = build_rviz_command(config_path)
        transform_command = (
            None if arguments.no_transforms else build_transform_command(transform_path)
        )
    except RvizLaunchError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    print(f"RViz config: {config_path}")
    print(f"Static transforms: {len(transforms)}")
    if transform_command:
        print(f"TF command: {shlex.join(transform_command)}")
    print(f"RViz command: {shlex.join(rviz_command)}")
    print("\nIn a second terminal, run:")
    print("  ros2 bag play /path/to/bag --clock")
    if arguments.dry_run:
        return 0
    try:
        return run_rviz_and_transforms(rviz_command, transform_command)
    except (OSError, RvizLaunchError) as error:
        print(f"error: could not launch RViz/TF: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
