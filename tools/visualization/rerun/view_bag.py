#!/usr/bin/env python3
"""Stream a Berlin Waters ROS 2 bag directly into Rerun without ROS 2."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import math
from pathlib import Path
import re
import sys
import time
from typing import Any, Sequence

import numpy as np

# Keep the documented direct-script invocation working without installation.
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from tools.extraction.extract import (
    BagInspectionError,
    create_bag_reader,
    decode_pointcloud2,
    detect_compressed_image_extension,
)
from tools.visualization.rerun.view import (
    TIMELINE,
    ViewerError,
    downsample_points,
    import_rerun,
    rerun_viewer_on_path,
    set_timestamp,
    topic_entity_path,
)


CAMERA_IMAGE_TYPE = "sensor_msgs/msg/CompressedImage"
CAMERA_INFO_TYPE = "sensor_msgs/msg/CameraInfo"
POINTCLOUD_TYPE = "sensor_msgs/msg/PointCloud2"
IMU_TYPE = "sensor_msgs/msg/Imu"
GNSS_TYPE = "sensor_msgs/msg/NavSatFix"
SUPPORTED_TYPES = {
    CAMERA_IMAGE_TYPE,
    CAMERA_INFO_TYPE,
    POINTCLOUD_TYPE,
    IMU_TYPE,
    GNSS_TYPE,
}
GNSS_COLORS = {
    "ublox_1": [230, 57, 70],
    "ublox_2": [42, 157, 143],
    "ublox_3": [69, 123, 157],
    "xsens": [244, 162, 97],
}
LIDAR_COLORS = {
    "livox": [255, 0, 0],
    "velodyne": [0, 255, 0],
    "ouster": [0, 0, 255],
}
AXIS_COLORS = {
    "x": [255, 0, 0],
    "y": [0, 255, 0],
    "z": [0, 0, 255],
}
LIDAR_ORDER = {"livox": 0, "velodyne": 1, "ouster": 2}


@dataclass(frozen=True)
class BagViewPlan:
    """Topics selected for direct Rerun playback."""

    connections: tuple[Any, ...]
    camera_entities: tuple[str, ...]
    lidar_entities: tuple[str, ...]
    message_count: int
    bag_start_ns: int
    bag_end_ns: int


@dataclass
class PlaybackState:
    """Mutable state used to throttle and summarize live visualization."""

    camera_info_logged: set[str]
    last_imu_timestamp_ns: dict[str, int]
    processed: int = 0
    failed: int = 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Stream camera, calibration, Xsens IMU, GNSS, and LiDAR messages "
            "from a Berlin Waters ROS 2 bag directly into Rerun."
        )
    )
    parser.add_argument("bag", type=Path, help="path to a ROS 2 bag directory")
    parser.add_argument(
        "--rate",
        type=float,
        default=1.0,
        help="playback rate; 0 processes as fast as possible (default: %(default)s)",
    )
    parser.add_argument(
        "--start",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="start offset from the beginning of the bag (default: %(default)s)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        metavar="SECONDS",
        help="stop after this much bag time",
    )
    parser.add_argument(
        "--memory-limit",
        default="2GB",
        help="Rerun rolling memory limit, e.g. 2GB or 25%% (default: %(default)s)",
    )
    parser.add_argument(
        "--max-points",
        type=int,
        default=200_000,
        help="maximum displayed points per LiDAR scan (default: %(default)s)",
    )
    parser.add_argument(
        "--imu-max-hz",
        type=float,
        default=50.0,
        help="maximum displayed Xsens IMU rate; 0 keeps every sample (default: %(default)s)",
    )
    parser.add_argument(
        "--save",
        type=Path,
        metavar="RECORDING.RRD",
        help="save an RRD instead of spawning the rolling live viewer",
    )
    return parser


def sanitize_entity_part(value: str) -> str:
    """Return an entity-path-safe identifier."""
    return re.sub(r"[^A-Za-z0-9_]", "_", value).strip("_") or "unknown"


def is_xsens_imu(topic: str, message_type: str) -> bool:
    return message_type == IMU_TYPE and topic.startswith("/xsens/")


def camera_name(topic: str) -> str:
    lower = topic.lower()
    if "/left/" in lower:
        return "zed_left"
    if "/right/" in lower:
        return "zed_right"
    return sanitize_entity_part(topic.strip("/").replace("/", "_"))


def camera_entity(topic: str) -> str:
    return f"world/cameras/{camera_name(topic)}"


def lidar_entity(topic: str) -> str:
    return topic_entity_path(topic, POINTCLOUD_TYPE).rsplit("/points", 1)[0]


def lidar_name(entity_or_topic: str) -> str:
    """Return the canonical LiDAR name used for order, color, and labels."""
    lower = entity_or_topic.lower()
    if "livox" in lower:
        return "livox"
    if "vlp32" in lower or "velodyne" in lower:
        return "velodyne"
    if "ouster" in lower:
        return "ouster"
    return sanitize_entity_part(entity_or_topic.rsplit("/", 1)[-1]).lower()


def lidar_sort_key(entity: str) -> tuple[int, str]:
    name = lidar_name(entity)
    return LIDAR_ORDER.get(name, len(LIDAR_ORDER)), name


def lidar_color(entity_or_topic: str) -> list[int]:
    """Return the requested fixed RGB color for a known LiDAR."""
    return LIDAR_COLORS.get(lidar_name(entity_or_topic), [255, 255, 255])


def gnss_name(topic: str) -> str:
    parts = [part for part in topic.strip("/").split("/") if part]
    if parts and parts[0].startswith("ublox_"):
        return sanitize_entity_part(parts[0])
    if parts and parts[0] == "xsens":
        return "xsens"
    return sanitize_entity_part("_".join(parts))


def build_view_plan(reader: Any) -> BagViewPlan:
    """Select directly visualizable connections from an open bag reader."""
    selected = []
    camera_entities = set()
    lidar_entities = set()
    for connection in reader.connections:
        if connection.msgtype not in SUPPORTED_TYPES:
            continue
        if connection.msgtype == IMU_TYPE and not is_xsens_imu(
            connection.topic, connection.msgtype
        ):
            continue
        selected.append(connection)
        if connection.msgtype in {CAMERA_IMAGE_TYPE, CAMERA_INFO_TYPE}:
            camera_entities.add(camera_entity(connection.topic))
        elif connection.msgtype == POINTCLOUD_TYPE:
            lidar_entities.add(lidar_entity(connection.topic))
    if not selected:
        raise ViewerError("bag contains no directly visualizable Berlin Waters topics")
    return BagViewPlan(
        connections=tuple(selected),
        camera_entities=tuple(sorted(camera_entities)),
        lidar_entities=tuple(sorted(lidar_entities, key=lidar_sort_key)),
        message_count=sum(int(connection.msgcount) for connection in selected),
        bag_start_ns=int(reader.start_time),
        bag_end_ns=int(reader.end_time),
    )


def build_blueprint(rrb: Any, plan: BagViewPlan) -> Any:
    """Create the default all-sensors-at-once dashboard."""
    camera_views = [
        rrb.Spatial2DView(
            origin=f"{entity}/image",
            contents=f"{entity}/image",
            name=entity.rsplit("/", 1)[-1],
        )
        for entity in plan.camera_entities
    ]
    top_row = []
    top_shares = []
    if camera_views:
        top_row.append(
            rrb.Vertical(
                *camera_views,
                row_shares=[1.0] * len(camera_views),
                name="Stereo cameras",
            )
        )
        top_shares.append(1.0)
    top_row.append(
        rrb.MapView(
            origin="world/gnss",
            contents="world/gnss/**",
            name="GNSS — OpenStreetMap",
            zoom=17.0,
            background=rrb.MapProvider.OpenStreetMap,
        )
    )
    top_shares.append(1.35)
    top_row.append(
        rrb.Vertical(
            rrb.TimeSeriesView(
                origin="world/imu/xsens/angular_velocity",
                name="Xsens angular velocity",
            ),
            rrb.TimeSeriesView(
                origin="world/imu/xsens/linear_acceleration",
                name="Xsens linear acceleration",
            ),
            name="Xsens IMU",
        )
    )
    top_shares.append(1.0)
    sensor_dashboard = rrb.Horizontal(
        *top_row,
        column_shares=top_shares,
        name="Cameras, GNSS, and IMU",
    )

    lidar_views = [
        rrb.Spatial3DView(
            origin=entity,
            contents=f"{entity}/**",
            name=lidar_name(entity).capitalize(),
        )
        for entity in plan.lidar_entities
    ]
    rows = [sensor_dashboard]
    if lidar_views:
        rows.append(
            rrb.Horizontal(
                *lidar_views,
                column_shares=[1.0] * len(lidar_views),
                name="LiDAR",
            )
        )
    return rrb.Blueprint(
        rrb.Vertical(
            *rows,
            row_shares=[1.0] * len(rows),
            name="Berlin Waters",
        ),
        auto_views=False,
        collapse_panels=True,
    )


def compressed_media_type(message: Any) -> str:
    extension = detect_compressed_image_extension(
        str(message.format), bytes(message.data)
    )
    return "image/jpeg" if extension == "jpg" else "image/png"


def log_camera_image(rr: Any, recording: Any, topic: str, message: Any) -> None:
    recording.log(
        f"{camera_entity(topic)}/image",
        rr.EncodedImage(
            contents=bytes(message.data),
            media_type=compressed_media_type(message),
        ),
    )


def log_camera_info(
    rr: Any,
    recording: Any,
    topic: str,
    message: Any,
    state: PlaybackState,
) -> None:
    entity = camera_entity(topic)
    if entity in state.camera_info_logged:
        return
    intrinsic = np.asarray(message.k, dtype=np.float64)
    if intrinsic.size != 9 or int(message.width) <= 0 or int(message.height) <= 0:
        raise ViewerError(f"invalid CameraInfo calibration on {topic}")
    recording.log(
        entity,
        rr.Pinhole(
            image_from_camera=intrinsic.reshape(3, 3),
            resolution=[int(message.width), int(message.height)],
            camera_xyz=rr.ViewCoordinates.RDF,
        ),
        static=True,
    )
    state.camera_info_logged.add(entity)


def log_pointcloud(
    rr: Any,
    recording: Any,
    topic: str,
    message: Any,
    maximum_points: int,
) -> None:
    cloud = decode_pointcloud2(message)
    points, _ = downsample_points(
        cloud.points, cloud.intensity, maximum_points
    )
    entity = f"{lidar_entity(topic)}/points"
    recording.log(entity, rr.Points3D(points, colors=lidar_color(topic)))


def should_log_imu(
    topic: str,
    timestamp_ns: int,
    maximum_hz: float,
    state: PlaybackState,
) -> bool:
    if maximum_hz == 0:
        state.last_imu_timestamp_ns[topic] = timestamp_ns
        return True
    minimum_interval_ns = max(1, int(1_000_000_000 / maximum_hz))
    previous = state.last_imu_timestamp_ns.get(topic)
    if previous is not None and timestamp_ns - previous < minimum_interval_ns:
        return False
    state.last_imu_timestamp_ns[topic] = timestamp_ns
    return True


def log_imu(rr: Any, recording: Any, message: Any) -> None:
    values = {
        "angular_velocity/x": message.angular_velocity.x,
        "angular_velocity/y": message.angular_velocity.y,
        "angular_velocity/z": message.angular_velocity.z,
        "linear_acceleration/x": message.linear_acceleration.x,
        "linear_acceleration/y": message.linear_acceleration.y,
        "linear_acceleration/z": message.linear_acceleration.z,
    }
    for relative_entity, value in values.items():
        recording.log(
            f"world/imu/xsens/{relative_entity}",
            rr.Scalars([float(value)]),
        )


def log_imu_series_styles(rr: Any, recording: Any) -> None:
    """Use the conventional RGB mapping for X/Y/Z IMU plot series."""
    for measurement in ("angular_velocity", "linear_acceleration"):
        for axis, color in AXIS_COLORS.items():
            recording.log(
                f"world/imu/xsens/{measurement}/{axis}",
                rr.SeriesLines(
                    colors=color,
                    names=axis.upper(),
                    widths=2.0,
                ),
                static=True,
            )


def log_gnss(rr: Any, recording: Any, topic: str, message: Any) -> None:
    latitude = float(message.latitude)
    longitude = float(message.longitude)
    if not (
        math.isfinite(latitude)
        and math.isfinite(longitude)
        and -90.0 <= latitude <= 90.0
        and -180.0 <= longitude <= 180.0
    ):
        return
    name = gnss_name(topic)
    recording.log(
        f"world/gnss/{name}",
        rr.GeoPoints(
            lat_lon=[latitude, longitude],
            radii=rr.Radius.ui_points(8.0),
            colors=GNSS_COLORS.get(name, [255, 255, 255]),
        ),
    )


def log_message(
    rr: Any,
    recording: Any,
    connection: Any,
    timestamp_ns: int,
    message: Any,
    state: PlaybackState,
    maximum_points: int,
    imu_maximum_hz: float,
) -> None:
    set_timestamp(recording, timestamp_ns)
    if connection.msgtype == CAMERA_IMAGE_TYPE:
        log_camera_image(rr, recording, connection.topic, message)
    elif connection.msgtype == CAMERA_INFO_TYPE:
        log_camera_info(rr, recording, connection.topic, message, state)
    elif connection.msgtype == POINTCLOUD_TYPE:
        log_pointcloud(rr, recording, connection.topic, message, maximum_points)
    elif connection.msgtype == IMU_TYPE and should_log_imu(
        connection.topic, timestamp_ns, imu_maximum_hz, state
    ):
        log_imu(rr, recording, message)
    elif connection.msgtype == GNSS_TYPE:
        log_gnss(rr, recording, connection.topic, message)


def pace_playback(
    timestamp_ns: int,
    first_timestamp_ns: int,
    wall_start: float,
    rate: float,
) -> None:
    if rate == 0:
        return
    target_elapsed = (timestamp_ns - first_timestamp_ns) / 1_000_000_000 / rate
    delay = target_elapsed - (time.monotonic() - wall_start)
    if delay > 0:
        time.sleep(delay)


def print_plan(plan: BagViewPlan, start_ns: int, stop_ns: int) -> None:
    print("Direct bag visualization (no ROS 2)")
    print(f"Timeline: {TIMELINE}")
    print(f"Selected messages: {plan.message_count}")
    print(f"Cameras: {len(plan.camera_entities)}")
    print(f"LiDARs: {len(plan.lidar_entities)}")
    print(
        "Playback range: "
        f"{(start_ns - plan.bag_start_ns) / 1e9:.3f}s to "
        f"{(stop_ns - plan.bag_start_ns) / 1e9:.3f}s"
    )


def validate_arguments(parser: argparse.ArgumentParser, arguments: Any) -> None:
    if arguments.rate < 0:
        parser.error("--rate must be zero or greater")
    if arguments.start < 0:
        parser.error("--start must be zero or greater")
    if arguments.duration is not None and arguments.duration <= 0:
        parser.error("--duration must be greater than zero")
    if arguments.max_points <= 0:
        parser.error("--max-points must be greater than zero")
    if arguments.imu_max_hz < 0:
        parser.error("--imu-max-hz must be zero or greater")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    validate_arguments(parser, arguments)
    bag_path = arguments.bag.expanduser().resolve()
    if not bag_path.is_dir() or not (bag_path / "metadata.yaml").is_file():
        print(f"error: ROS 2 bag directory is invalid: {bag_path}", file=sys.stderr)
        return 2

    try:
        rr = import_rerun()
        import rerun.blueprint as rrb

        with create_bag_reader(bag_path) as reader:
            plan = build_view_plan(reader)
            start_ns = plan.bag_start_ns + int(arguments.start * 1_000_000_000)
            stop_ns = plan.bag_end_ns
            if arguments.duration is not None:
                stop_ns = min(
                    stop_ns,
                    start_ns + int(arguments.duration * 1_000_000_000),
                )
            if start_ns >= plan.bag_end_ns or stop_ns <= start_ns:
                raise ViewerError("requested playback range is outside the bag")
            print_plan(plan, start_ns, stop_ns)

            with rr.RecordingStream(
                application_id="berlin_waters_bag",
                recording_id=bag_path.name,
            ) as recording:
                if arguments.save is None:
                    with rerun_viewer_on_path():
                        recording.spawn(memory_limit=arguments.memory_limit)
                else:
                    arguments.save.parent.mkdir(parents=True, exist_ok=True)
                    recording.save(str(arguments.save))
                recording.send_blueprint(build_blueprint(rrb, plan))
                log_imu_series_styles(rr, recording)

                state = PlaybackState(set(), {})
                wall_start = time.monotonic()
                first_timestamp_ns: int | None = None
                last_progress_second = -1
                for connection, timestamp_ns, rawdata in reader.messages(
                    connections=plan.connections,
                    start=start_ns,
                    stop=stop_ns,
                ):
                    if first_timestamp_ns is None:
                        first_timestamp_ns = timestamp_ns
                        wall_start = time.monotonic()
                    pace_playback(
                        timestamp_ns,
                        first_timestamp_ns,
                        wall_start,
                        arguments.rate,
                    )
                    try:
                        message = reader.deserialize(rawdata, connection.msgtype)
                        log_message(
                            rr,
                            recording,
                            connection,
                            timestamp_ns,
                            message,
                            state,
                            arguments.max_points,
                            arguments.imu_max_hz,
                        )
                    except Exception as error:
                        state.failed += 1
                        if state.failed <= 10:
                            print(
                                f"warning: {connection.topic} at {timestamp_ns}: {error}",
                                file=sys.stderr,
                            )
                    state.processed += 1
                    elapsed_second = int((timestamp_ns - start_ns) / 1_000_000_000)
                    if elapsed_second != last_progress_second:
                        print(
                            "\rPlayback: "
                            f"{elapsed_second:.0f}s bag time, "
                            f"{state.processed} messages, {state.failed} failed",
                            end="",
                            flush=True,
                        )
                        last_progress_second = elapsed_second
                if state.processed:
                    print()
                print(
                    f"Finished: {state.processed} messages processed, "
                    f"{state.failed} failed"
                )
    except KeyboardInterrupt:
        print("\nPlayback stopped by user")
        return 130
    except (BagInspectionError, ViewerError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"error: direct bag visualization failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
