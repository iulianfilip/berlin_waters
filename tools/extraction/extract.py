#!/usr/bin/env python3
"""Extract public, ROS-independent files from Berlin Waters ROS 2 bags."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field as dataclass_field
import io
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Sequence, TextIO

import numpy as np


SUPPORTED_MESSAGE_TYPES = {
    "sensor_msgs/msg/CompressedImage": "compressed images",
    "sensor_msgs/msg/PointCloud2": "PCD/PLY point clouds",
    "sensor_msgs/msg/Imu": "IMU CSV",
    "sensor_msgs/msg/NavSatFix": "GNSS CSV",
}

OUTPUT_FORMATS = {
    "sensor_msgs/msg/CompressedImage": "compressed_image",
    "sensor_msgs/msg/PointCloud2": "pointcloud",
    "sensor_msgs/msg/Imu": "csv",
    "sensor_msgs/msg/NavSatFix": "csv",
}

TABULAR_MESSAGE_TYPES = {
    "sensor_msgs/msg/Imu",
    "sensor_msgs/msg/NavSatFix",
}

POINTFIELD_DTYPES = {
    1: np.dtype("i1"),
    2: np.dtype("u1"),
    3: np.dtype("i2"),
    4: np.dtype("u2"),
    5: np.dtype("i4"),
    6: np.dtype("u4"),
    7: np.dtype("f4"),
    8: np.dtype("f8"),
}

MANIFEST_SCHEMA_VERSION = 1

SENSOR_TOPIC_PREFIXES = {
    "velodyne": ("/VLP32/", "/velodyne/"),
    "ouster": ("/ouster/",),
    "livox": ("/livox/",),
    "zed2i": ("/zed/", "/zed2i/"),
    "xsens": ("/xsens/",),
    "ublox": ("/ublox_",),
    "sonar": ("/water_depth",),
}

SENSOR_ALIASES = {
    "vlp32": "velodyne",
    "zed": "zed2i",
}


class BagInspectionError(RuntimeError):
    """Raised when a ROS 2 bag cannot be inspected."""


@dataclass(frozen=True)
class SelectionResult:
    """Result of applying sensor and exact-topic selectors."""

    selected_topics: frozenset[str]
    missing_topics: tuple[str, ...]
    unmatched_sensors: tuple[str, ...]
    sensors: tuple[str, ...]
    requested_topics: tuple[str, ...]


@dataclass(frozen=True)
class TopicPlan:
    """Inspection result for one topic and message-type pair."""

    topic: str
    message_type: str
    selected: bool
    output_type: str | None
    reason: str | None
    message_count: int

    @property
    def supported(self) -> bool:
        """Return whether the message type has a planned MVP exporter."""
        return self.output_type is not None


@dataclass(frozen=True)
class BagInspection:
    """Read-only summary of a bag and the requested topic selection."""

    bag_path: Path
    topics: tuple[TopicPlan, ...]
    selection: SelectionResult
    message_count: int
    start_timestamp_ns: int | None
    end_timestamp_ns_exclusive: int | None


@dataclass(frozen=True)
class CameraIndexEntry:
    """One exported compressed image and its source metadata."""

    timestamp_ns: int
    header_timestamp_ns: int | None
    frame_id: str
    source_format: str
    filename: str
    byte_count: int


@dataclass(frozen=True)
class TopicExtractionResult:
    """Extraction outcome for one selected topic."""

    status: str
    reason: str | None
    extracted_message_count: int
    failed_message_count: int
    start_timestamp_ns: int | None
    end_timestamp_ns: int | None
    index_path: str | None = None
    output_format: str | None = None


@dataclass(frozen=True)
class DecodedPointCloud:
    """Decoded points with convenient XYZ/intensity views and all fields."""

    points: np.ndarray
    intensity: np.ndarray | None
    fields: tuple["DecodedPointField", ...] = dataclass_field(default_factory=tuple)


@dataclass(frozen=True)
class DecodedPointField:
    """One decoded PointCloud2 field with its original ROS datatype."""

    name: str
    values: np.ndarray
    datatype: int
    count: int


@dataclass(frozen=True)
class PointCloudIndexEntry:
    """One exported scan and its timing metadata."""

    timestamp_ns: int
    header_timestamp_ns: int | None
    frame_id: str
    filename: str
    point_count: int
    has_intensity: bool
    fields: str


def normalize_topic(topic: str) -> str:
    """Normalize an exact topic selector to its absolute ROS name."""
    stripped = topic.strip()
    if not stripped:
        raise ValueError("topic selectors cannot be empty")
    return stripped if stripped.startswith("/") else f"/{stripped}"


def normalize_sensor(sensor: str) -> str:
    """Return the canonical name for a supported sensor selector."""
    normalized = sensor.strip().lower()
    normalized = SENSOR_ALIASES.get(normalized, normalized)
    if normalized not in SENSOR_TOPIC_PREFIXES:
        available = ", ".join(sorted(SENSOR_TOPIC_PREFIXES))
        raise ValueError(f"unknown sensor {sensor!r}; choose from: {available}")
    return normalized


def topic_matches_sensor(topic: str, sensor: str) -> bool:
    """Return whether a topic belongs to a documented sensor namespace."""
    canonical_sensor = normalize_sensor(sensor)
    return topic.startswith(SENSOR_TOPIC_PREFIXES[canonical_sensor])


def select_topic_names(
    topic_names: Iterable[str],
    sensors: Iterable[str] = (),
    requested_topics: Iterable[str] = (),
) -> SelectionResult:
    """Select topics using the union of sensor and exact-topic selectors.

    With no selectors, every topic is selected. Exact topics may be provided
    with or without a leading slash.
    """
    available_topics = frozenset(topic_names)
    canonical_sensors = tuple(dict.fromkeys(normalize_sensor(item) for item in sensors))
    exact_topics = tuple(dict.fromkeys(normalize_topic(item) for item in requested_topics))

    if not canonical_sensors and not exact_topics:
        return SelectionResult(available_topics, (), (), (), ())

    selected = set(available_topics.intersection(exact_topics))
    unmatched_sensors: list[str] = []
    for sensor in canonical_sensors:
        matches = {
            topic
            for topic in available_topics
            if topic_matches_sensor(topic, sensor)
        }
        if not matches:
            unmatched_sensors.append(sensor)
        selected.update(matches)

    missing_topics = tuple(topic for topic in exact_topics if topic not in available_topics)
    return SelectionResult(
        frozenset(selected),
        missing_topics,
        tuple(unmatched_sensors),
        canonical_sensors,
        exact_topics,
    )


def classify_message_type(message_type: str) -> tuple[str | None, str | None]:
    """Return the planned output type or a clear unsupported reason."""
    output_type = SUPPORTED_MESSAGE_TYPES.get(message_type)
    if output_type is not None:
        return output_type, None
    if "livox" in message_type.lower():
        return None, "custom Livox messages are not supported by the MVP extractor"
    return None, f"message type {message_type!r} is not supported by the MVP extractor"


def build_topic_plans(
    connections: Iterable[tuple[str, str, int]], selected_topics: frozenset[str]
) -> tuple[TopicPlan, ...]:
    """Build stable plans from ``(topic, message_type, count)`` tuples."""
    plans = []
    for topic, message_type, message_count in sorted(connections):
        output_type, reason = classify_message_type(message_type)
        plans.append(
            TopicPlan(
                topic=topic,
                message_type=message_type,
                selected=topic in selected_topics,
                output_type=output_type,
                reason=reason,
                message_count=message_count,
            )
        )
    return tuple(plans)


def sanitize_topic_name(topic: str) -> str:
    """Convert a ROS topic into a stable, filesystem-safe stem."""
    components = [component for component in topic.split("/") if component]
    if not components:
        return "root"
    return "_".join(
        "".join(character if character.isalnum() or character in "-_" else "_"
                for character in component)
        for component in components
    )


def planned_output_path(plan: TopicPlan) -> str | None:
    """Return the future output path relative to the extraction directory."""
    stem = sanitize_topic_name(plan.topic)
    if plan.message_type == "sensor_msgs/msg/CompressedImage":
        return f"images/{stem}"
    if plan.message_type == "sensor_msgs/msg/PointCloud2":
        return f"pointclouds/{stem}"
    if plan.message_type in OUTPUT_FORMATS:
        return f"tables/{stem}.csv"
    return None


def detect_compressed_image_extension(source_format: str, data: bytes) -> str:
    """Detect a JPEG or PNG extension without decoding the image."""
    if not data:
        raise ValueError("compressed image payload is empty")
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"

    normalized_format = source_format.lower()
    if "jpeg" in normalized_format or "jpg" in normalized_format:
        return "jpg"
    if "png" in normalized_format:
        return "png"
    raise ValueError(f"unsupported compressed image format: {source_format!r}")


def message_header_metadata(message: Any) -> tuple[int | None, str]:
    """Read a ROS header timestamp and frame ID when available."""
    header = getattr(message, "header", None)
    if header is None:
        return None, ""

    frame_id = str(getattr(header, "frame_id", ""))
    stamp = getattr(header, "stamp", None)
    if stamp is None:
        return None, frame_id

    try:
        timestamp_ns = int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)
    except (AttributeError, TypeError, ValueError):
        return None, frame_id
    return timestamp_ns, frame_id


def format_unix_timestamp_ns(timestamp_ns: int) -> str:
    """Format integer nanoseconds as exact Unix ``seconds.nanoseconds``."""
    seconds, nanoseconds = divmod(timestamp_ns, 1_000_000_000)
    return f"{seconds}.{nanoseconds:09d}"


def export_compressed_image(
    message: Any,
    timestamp_ns: int,
    output_directory: Path,
    image_index: int,
) -> CameraIndexEntry:
    """Write one original compressed payload and return its index entry."""
    source_format = str(getattr(message, "format", ""))
    data = bytes(getattr(message, "data", b""))
    extension = detect_compressed_image_extension(source_format, data)

    filename = f"{image_index:06d}.{extension}"
    image_path = output_directory / filename
    temporary_path = output_directory / f".{filename}.tmp"
    try:
        temporary_path.write_bytes(data)
        temporary_path.replace(image_path)
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise

    header_timestamp_ns, frame_id = message_header_metadata(message)
    return CameraIndexEntry(
        timestamp_ns=timestamp_ns,
        header_timestamp_ns=header_timestamp_ns,
        frame_id=frame_id,
        source_format=source_format,
        filename=filename,
        byte_count=len(data),
    )


def write_camera_index(entries: Sequence[CameraIndexEntry], index_path: Path) -> None:
    """Atomically write a camera topic's CSV index."""
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, quoting=csv.QUOTE_ALL, lineterminator="\n")
    writer.writerow(
        [
            "filename",
            "timestamp_unix",
            "header_timestamp_unix",
            "frame_id",
            "source_format",
            "byte_count",
        ]
    )
    for entry in entries:
        writer.writerow(
            [
                entry.filename,
                format_unix_timestamp_ns(entry.timestamp_ns),
                (
                    ""
                    if entry.header_timestamp_ns is None
                    else format_unix_timestamp_ns(entry.header_timestamp_ns)
                ),
                entry.frame_id,
                entry.source_format,
                entry.byte_count,
            ]
        )

    temporary_path = index_path.parent / f".{index_path.name}.tmp"
    try:
        temporary_path.write_text(stream.getvalue(), encoding="utf-8", newline="")
        temporary_path.replace(index_path)
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise


def tabular_csv_header(message_type: str) -> tuple[str, ...]:
    """Return the stable CSV columns for a supported tabular message."""
    common = ("timestamp_unix", "header_timestamp_unix", "frame_id")
    if message_type == "sensor_msgs/msg/Imu":
        return common + (
            "orientation_x",
            "orientation_y",
            "orientation_z",
            "orientation_w",
            "angular_velocity_x",
            "angular_velocity_y",
            "angular_velocity_z",
            "linear_acceleration_x",
            "linear_acceleration_y",
            "linear_acceleration_z",
        )
    if message_type == "sensor_msgs/msg/NavSatFix":
        return common + ("latitude", "longitude", "altitude")
    raise ValueError(f"unsupported tabular message type: {message_type}")


def tabular_csv_row(
    message_type: str, message: Any, timestamp_ns: int
) -> tuple[Any, ...]:
    """Convert one supported tabular ROS message into a CSV row."""
    header_timestamp_ns, frame_id = message_header_metadata(message)
    common = (
        format_unix_timestamp_ns(timestamp_ns),
        (
            ""
            if header_timestamp_ns is None
            else format_unix_timestamp_ns(header_timestamp_ns)
        ),
        frame_id,
    )

    if message_type == "sensor_msgs/msg/Imu":
        orientation = message.orientation
        angular_velocity = message.angular_velocity
        linear_acceleration = message.linear_acceleration
        return common + (
            orientation.x,
            orientation.y,
            orientation.z,
            orientation.w,
            angular_velocity.x,
            angular_velocity.y,
            angular_velocity.z,
            linear_acceleration.x,
            linear_acceleration.y,
            linear_acceleration.z,
        )
    if message_type == "sensor_msgs/msg/NavSatFix":
        return common + (message.latitude, message.longitude, message.altitude)
    raise ValueError(f"unsupported tabular message type: {message_type}")


def pointcloud_field_values(
    rawdata: bytes,
    point_count: int,
    point_step: int,
    field: Any,
    is_bigendian: bool,
) -> np.ndarray:
    """Read every value of one PointCloud2 field."""
    datatype = POINTFIELD_DTYPES.get(int(field.datatype))
    if datatype is None:
        raise ValueError(
            f"field {field.name!r} uses unsupported datatype {field.datatype}"
        )
    field_count = int(getattr(field, "count", 1))
    if field_count < 1:
        raise ValueError(f"field {field.name!r} has an invalid count")

    datatype = datatype.newbyteorder(">" if is_bigendian else "<")
    offset = int(field.offset)
    if offset < 0 or offset + datatype.itemsize * field_count > point_step:
        raise ValueError(f"field {field.name!r} exceeds point_step")
    if point_count == 0:
        return np.empty((0, field_count), dtype=datatype)
    values = np.ndarray(
        shape=(point_count, field_count),
        dtype=datatype,
        buffer=rawdata,
        offset=offset,
        strides=(point_step, datatype.itemsize),
    )
    return values


def decode_pointcloud2(message: Any) -> DecodedPointCloud:
    """Decode XYZ and optional intensity from a standard PointCloud2 message."""
    width = int(message.width)
    height = int(message.height)
    point_step = int(message.point_step)
    row_step = int(message.row_step)
    if width < 0 or height < 0 or point_step <= 0 or row_step < 0:
        raise ValueError("PointCloud2 dimensions and strides are invalid")
    minimum_row_step = width * point_step
    if row_step < minimum_row_step:
        raise ValueError("PointCloud2 row_step is smaller than width * point_step")

    source_data = bytes(message.data)
    required_size = height * row_step
    if len(source_data) < required_size:
        raise ValueError(
            f"PointCloud2 data is truncated: expected {required_size} bytes, "
            f"received {len(source_data)}"
        )

    if row_step == minimum_row_step:
        packed_data = source_data[:required_size]
    else:
        packed_data = b"".join(
            source_data[row * row_step : row * row_step + minimum_row_step]
            for row in range(height)
        )

    source_fields = tuple(message.fields)
    fields_by_name = {str(field.name).lower(): field for field in source_fields}
    missing_fields = sorted({"x", "y", "z"}.difference(fields_by_name))
    if missing_fields:
        raise ValueError(
            f"PointCloud2 is missing required fields: {', '.join(missing_fields)}"
        )

    point_count = width * height
    decoded_values: dict[str, np.ndarray] = {}
    for source_field in source_fields:
        field_name = str(source_field.name)
        if not field_name:
            raise ValueError("PointCloud2 contains a field with an empty name")
        if field_name in decoded_values:
            raise ValueError(f"PointCloud2 contains duplicate field {field_name!r}")
        decoded_values[field_name] = pointcloud_field_values(
            packed_data,
            point_count,
            point_step,
            source_field,
            bool(message.is_bigendian),
        )

    coordinates = [
        decoded_values[str(fields_by_name[name].name)][:, 0].astype(
            np.float32, copy=False
        )
        for name in ("x", "y", "z")
    ]
    points = np.column_stack(coordinates).astype(np.float32, copy=False)
    valid = np.isfinite(points).all(axis=1)

    intensity = None
    if "intensity" in fields_by_name:
        intensity = decoded_values[
            str(fields_by_name["intensity"].name)
        ][:, 0].astype(np.float32, copy=False)
        valid &= np.isfinite(intensity)

    points = np.ascontiguousarray(points[valid], dtype=np.float32)
    if intensity is not None:
        intensity = np.ascontiguousarray(intensity[valid], dtype=np.float32)
    decoded_fields = tuple(
        DecodedPointField(
            name=str(source_field.name),
            values=np.ascontiguousarray(
                decoded_values[str(source_field.name)][valid]
            ),
            datatype=int(source_field.datatype),
            count=int(getattr(source_field, "count", 1)),
        )
        for source_field in source_fields
    )
    return DecodedPointCloud(
        points=points,
        intensity=intensity,
        fields=decoded_fields,
    )


def pointcloud_output_fields(
    cloud: DecodedPointCloud,
) -> tuple[DecodedPointField, ...]:
    """Return all decoded fields, with an XYZ/intensity fallback for tests."""
    if cloud.fields:
        return cloud.fields

    fields = [
        DecodedPointField("x", cloud.points[:, 0:1], 7, 1),
        DecodedPointField("y", cloud.points[:, 1:2], 7, 1),
        DecodedPointField("z", cloud.points[:, 2:3], 7, 1),
    ]
    if cloud.intensity is not None:
        fields.append(
            DecodedPointField(
                "intensity",
                cloud.intensity.reshape(-1, 1),
                7,
                1,
            )
        )
    return tuple(fields)


def pointcloud_output_name(name: str) -> str:
    """Return an ASCII field name accepted by PCD and PLY readers."""
    output_name = "".join(
        character if character.isascii() and (character.isalnum() or character == "_")
        else "_"
        for character in name
    )
    if not output_name:
        raise ValueError(f"point-cloud field name {name!r} cannot be represented")
    return output_name


def pcd_field_type(datatype: np.dtype[Any]) -> str:
    """Map a NumPy scalar dtype to a PCD TYPE value."""
    if datatype.kind == "f":
        return "F"
    if datatype.kind == "i":
        return "I"
    if datatype.kind == "u":
        return "U"
    raise ValueError(f"unsupported PCD datatype: {datatype}")


def ply_field_type(datatype: np.dtype[Any]) -> str:
    """Map a NumPy scalar dtype to a binary PLY property type."""
    type_names = {
        ("i", 1): "char",
        ("u", 1): "uchar",
        ("i", 2): "short",
        ("u", 2): "ushort",
        ("i", 4): "int",
        ("u", 4): "uint",
        ("f", 4): "float",
        ("f", 8): "double",
    }
    try:
        return type_names[(datatype.kind, datatype.itemsize)]
    except KeyError as error:
        raise ValueError(f"unsupported PLY datatype: {datatype}") from error


def pcd_binary_payload(
    cloud: DecodedPointCloud,
) -> tuple[bytes, list[str], list[int], list[str], list[int]]:
    """Build a packed PCD payload and its field metadata."""
    output_fields = pointcloud_output_fields(cloud)
    names = [pointcloud_output_name(field.name) for field in output_fields]
    if len(names) != len(set(names)):
        raise ValueError("point-cloud field names collide after sanitization")

    formats = []
    sizes = []
    types = []
    counts = []
    for field in output_fields:
        datatype = POINTFIELD_DTYPES[field.datatype].newbyteorder("<")
        formats.append((datatype, (field.count,)))
        sizes.append(datatype.itemsize)
        types.append(pcd_field_type(datatype))
        counts.append(field.count)

    packed = np.empty(cloud.points.shape[0], dtype=np.dtype(list(zip(names, formats))))
    for name, field in zip(names, output_fields):
        packed[name] = field.values.astype(packed.dtype[name].base, copy=False)
    return packed.tobytes(order="C"), names, sizes, types, counts


def ply_binary_payload(
    cloud: DecodedPointCloud,
) -> tuple[bytes, list[tuple[str, str]]]:
    """Build a packed PLY payload and expanded scalar properties."""
    output_fields = pointcloud_output_fields(cloud)
    properties: list[tuple[str, str]] = []
    formats: list[np.dtype[Any]] = []
    sources: list[tuple[DecodedPointField, int]] = []
    for field in output_fields:
        base_name = pointcloud_output_name(field.name)
        datatype = POINTFIELD_DTYPES[field.datatype].newbyteorder("<")
        for index in range(field.count):
            name = base_name if field.count == 1 else f"{base_name}_{index}"
            properties.append((name, ply_field_type(datatype)))
            formats.append(datatype)
            sources.append((field, index))

    names = [name for name, _ in properties]
    if len(names) != len(set(names)):
        raise ValueError("point-cloud PLY property names are not unique")
    packed = np.empty(cloud.points.shape[0], dtype=np.dtype(list(zip(names, formats))))
    for name, (field, index) in zip(names, sources):
        packed[name] = field.values[:, index].astype(packed.dtype[name], copy=False)
    return packed.tobytes(order="C"), properties


def write_binary_pointcloud(path: Path, header: str, payload: bytes) -> None:
    """Atomically write a binary point-cloud file."""
    temporary_path = path.parent / f".{path.name}.tmp"
    try:
        with temporary_path.open("wb") as output_file:
            output_file.write(header.encode("ascii"))
            output_file.write(payload)
        temporary_path.replace(path)
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise


def write_pcd(path: Path, cloud: DecodedPointCloud) -> None:
    """Write all decoded fields as binary PCD 0.7."""
    payload, fields, sizes, types, counts = pcd_binary_payload(cloud)
    point_count = cloud.points.shape[0]
    header = (
        "# .PCD v0.7 - Point Cloud Data file format\n"
        "VERSION 0.7\n"
        f"FIELDS {' '.join(fields)}\n"
        f"SIZE {' '.join(str(size) for size in sizes)}\n"
        f"TYPE {' '.join(types)}\n"
        f"COUNT {' '.join(str(count) for count in counts)}\n"
        f"WIDTH {point_count}\n"
        "HEIGHT 1\n"
        "VIEWPOINT 0 0 0 1 0 0 0\n"
        f"POINTS {point_count}\n"
        "DATA binary\n"
    )
    write_binary_pointcloud(path, header, payload)


def write_ply(path: Path, cloud: DecodedPointCloud) -> None:
    """Write all decoded fields as binary little-endian PLY."""
    payload, properties = ply_binary_payload(cloud)
    properties_text = "".join(
        f"property {property_type} {name}\n"
        for name, property_type in properties
    )
    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        f"element vertex {cloud.points.shape[0]}\n"
        f"{properties_text}"
        "end_header\n"
    )
    write_binary_pointcloud(path, header, payload)


def write_pointcloud_index(
    entries: Sequence[PointCloudIndexEntry], index_path: Path
) -> None:
    """Atomically write the index for one point-cloud topic."""
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, quoting=csv.QUOTE_ALL, lineterminator="\n")
    writer.writerow(
        [
            "filename",
            "timestamp_unix",
            "header_timestamp_unix",
            "frame_id",
            "point_count",
            "has_intensity",
            "fields",
        ]
    )
    for entry in entries:
        writer.writerow(
            [
                entry.filename,
                format_unix_timestamp_ns(entry.timestamp_ns),
                (
                    ""
                    if entry.header_timestamp_ns is None
                    else format_unix_timestamp_ns(entry.header_timestamp_ns)
                ),
                entry.frame_id,
                entry.point_count,
                str(entry.has_intensity).lower(),
                entry.fields,
            ]
        )

    temporary_path = index_path.parent / f".{index_path.name}.tmp"
    try:
        temporary_path.write_text(stream.getvalue(), encoding="utf-8", newline="")
        temporary_path.replace(index_path)
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise


def create_bag_reader(bag_path: Path) -> Any:
    """Create a ROS 2 Humble-compatible rosbags reader."""
    try:
        from rosbags.highlevel import AnyReader
        from rosbags.typesys import Stores, get_typestore
    except ImportError as error:
        raise BagInspectionError(
            "rosbags is required; install dependencies from requirements-tools.txt"
        ) from error

    default_typestore = get_typestore(Stores.ROS2_HUMBLE)
    return AnyReader([bag_path], default_typestore=default_typestore)


def extract_tabular_topics(
    inspection: BagInspection, output_directory: Path
) -> dict[str, TopicExtractionResult]:
    """Stream selected IMU and GNSS messages into per-topic CSV files."""
    table_plans = {
        plan.topic: plan
        for plan in inspection.topics
        if plan.selected and plan.message_type in TABULAR_MESSAGE_TYPES
    }
    if not table_plans:
        return {}

    streams: dict[str, TextIO] = {}
    writers: dict[str, Any] = {}
    temporary_paths: dict[str, Path] = {}
    output_paths: dict[str, Path] = {}
    extracted_counts = {topic: 0 for topic in table_plans}
    failure_counts = {topic: 0 for topic in table_plans}
    failure_reasons: dict[str, list[str]] = {
        topic: [] for topic in table_plans
    }
    start_timestamps: dict[str, int | None] = {
        topic: None for topic in table_plans
    }
    end_timestamps: dict[str, int | None] = {
        topic: None for topic in table_plans
    }

    for topic, plan in table_plans.items():
        relative_path = planned_output_path(plan)
        assert relative_path is not None
        output_path = output_directory / relative_path
        temporary_path = output_path.parent / f".{output_path.name}.tmp"
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            stream = temporary_path.open("w", encoding="utf-8", newline="")
            writer = csv.writer(
                stream,
                quoting=csv.QUOTE_ALL,
                lineterminator="\n",
            )
            writer.writerow(tabular_csv_header(plan.message_type))
            streams[topic] = stream
            writers[topic] = writer
            temporary_paths[topic] = temporary_path
            output_paths[topic] = output_path
        except OSError as error:
            failure_counts[topic] = plan.message_count
            failure_reasons[topic].append(f"could not create CSV output: {error}")

    try:
        with create_bag_reader(inspection.bag_path) as reader:
            connections = [
                connection
                for connection in reader.connections
                if connection.topic in writers
                and connection.msgtype == table_plans[connection.topic].message_type
            ]
            for connection, timestamp_ns, rawdata in reader.messages(
                connections=connections
            ):
                topic = connection.topic
                try:
                    message = reader.deserialize(rawdata, connection.msgtype)
                    writers[topic].writerow(
                        tabular_csv_row(connection.msgtype, message, timestamp_ns)
                    )
                    extracted_counts[topic] += 1
                    if start_timestamps[topic] is None:
                        start_timestamps[topic] = timestamp_ns
                    end_timestamps[topic] = timestamp_ns
                except Exception as error:
                    failure_counts[topic] += 1
                    reason = f"{type(error).__name__}: {error}"
                    if reason not in failure_reasons[topic]:
                        failure_reasons[topic].append(reason)
    except Exception as error:
        for topic in table_plans:
            failure_reasons[topic].append(f"bag reading failed: {error}")

    ready_topics = set(streams)
    for topic, stream in streams.items():
        try:
            stream.close()
        except OSError as error:
            ready_topics.discard(topic)
            failure_reasons[topic].append(f"could not finish CSV output: {error}")

    results: dict[str, TopicExtractionResult] = {}
    for topic, plan in table_plans.items():
        missing_count = max(
            0,
            plan.message_count
            - extracted_counts[topic]
            - failure_counts[topic],
        )
        if missing_count:
            failure_counts[topic] += missing_count
            failure_reasons[topic].append(
                f"{missing_count} message(s) were not returned by the bag reader"
            )

        if topic in ready_topics:
            try:
                temporary_paths[topic].replace(output_paths[topic])
            except OSError as error:
                ready_topics.discard(topic)
                failure_reasons[topic].append(f"could not publish CSV output: {error}")

        if topic not in ready_topics:
            temporary_path = temporary_paths.get(topic)
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            extracted_counts[topic] = 0
            failure_counts[topic] = plan.message_count
            start_timestamps[topic] = None
            end_timestamps[topic] = None

        if (
            not failure_reasons[topic]
            and extracted_counts[topic] == plan.message_count
        ):
            status = "completed"
            reason = None
        elif extracted_counts[topic]:
            status = "partial"
            reason = "; ".join(failure_reasons[topic][:5])
        else:
            status = "failed"
            reason = (
                "; ".join(failure_reasons[topic][:5])
                or "no messages were exported"
            )

        results[topic] = TopicExtractionResult(
            status=status,
            reason=reason,
            extracted_message_count=extracted_counts[topic],
            failed_message_count=failure_counts[topic],
            start_timestamp_ns=start_timestamps[topic],
            end_timestamp_ns=end_timestamps[topic],
        )
    return results


def extract_pointcloud_topics(
    inspection: BagInspection,
    output_directory: Path,
    output_format: str,
) -> dict[str, TopicExtractionResult]:
    """Extract selected PointCloud2 topics into binary PCD or PLY files."""
    if output_format not in {"pcd", "ply"}:
        raise ValueError(f"invalid point-cloud format: {output_format!r}")

    cloud_plans = {
        plan.topic: plan
        for plan in inspection.topics
        if plan.selected and plan.message_type == "sensor_msgs/msg/PointCloud2"
    }
    if not cloud_plans:
        return {}

    entries: dict[str, list[PointCloudIndexEntry]] = {
        topic: [] for topic in cloud_plans
    }
    message_indices = {topic: 0 for topic in cloud_plans}
    failure_counts = {topic: 0 for topic in cloud_plans}
    failure_reasons: dict[str, list[str]] = {
        topic: [] for topic in cloud_plans
    }
    output_paths: dict[str, Path] = {}
    for topic, plan in cloud_plans.items():
        relative_path = planned_output_path(plan)
        assert relative_path is not None
        topic_output = output_directory / relative_path
        try:
            topic_output.mkdir(parents=True, exist_ok=True)
            if not topic_output.is_dir():
                raise OSError(f"output path is not a directory: {topic_output}")
            output_paths[topic] = topic_output
        except OSError as error:
            failure_counts[topic] = plan.message_count
            failure_reasons[topic].append(
                f"could not create output directory: {error}"
            )

    total_messages = sum(plan.message_count for plan in cloud_plans.values())
    processed_messages = 0
    last_percentage = 0
    progress_started = total_messages > 0
    if progress_started:
        print(
            f"Point-cloud extraction progress: 0% (0/{total_messages})",
            end="",
            flush=True,
        )

    try:
        with create_bag_reader(inspection.bag_path) as reader:
            connections = [
                connection
                for connection in reader.connections
                if connection.topic in cloud_plans
                and connection.msgtype == "sensor_msgs/msg/PointCloud2"
                and connection.topic in output_paths
            ]
            for connection, timestamp_ns, rawdata in reader.messages(
                connections=connections
            ):
                topic = connection.topic
                scan_index = message_indices[topic]
                message_indices[topic] += 1
                staged_paths: list[tuple[Path, Path]] = []
                try:
                    message = reader.deserialize(rawdata, connection.msgtype)
                    cloud = decode_pointcloud2(message)
                    stem = f"{scan_index:06d}"
                    final_path = output_paths[topic] / f"{stem}.{output_format}"
                    staged_path = output_paths[topic] / (
                        f".{stem}.{output_format}.staged"
                    )
                    if output_format == "pcd":
                        write_pcd(staged_path, cloud)
                    else:
                        write_ply(staged_path, cloud)
                    staged_paths.append((staged_path, final_path))
                    for staged_path, final_path in staged_paths:
                        staged_path.replace(final_path)

                    header_timestamp_ns, frame_id = message_header_metadata(message)
                    entries[topic].append(
                        PointCloudIndexEntry(
                            timestamp_ns=timestamp_ns,
                            header_timestamp_ns=header_timestamp_ns,
                            frame_id=frame_id,
                            filename=f"{stem}.{output_format}",
                            point_count=cloud.points.shape[0],
                            has_intensity=cloud.intensity is not None,
                            fields=" ".join(
                                field.name for field in pointcloud_output_fields(cloud)
                            ),
                        )
                    )
                except Exception as error:
                    for staged_path, _ in staged_paths:
                        staged_path.unlink(missing_ok=True)
                    failure_counts[topic] += 1
                    reason = f"{type(error).__name__}: {error}"
                    if reason not in failure_reasons[topic]:
                        failure_reasons[topic].append(reason)
                finally:
                    processed_messages += 1
                    if progress_started:
                        percentage = min(
                            100,
                            processed_messages * 100 // total_messages,
                        )
                        if percentage > last_percentage:
                            print(
                                "\rPoint-cloud extraction progress: "
                                f"{percentage}% "
                                f"({processed_messages}/{total_messages})",
                                end="",
                                flush=True,
                            )
                            last_percentage = percentage
    except Exception as error:
        for topic in cloud_plans:
            failure_reasons[topic].append(f"bag reading failed: {error}")
    finally:
        if progress_started:
            print()

    results: dict[str, TopicExtractionResult] = {}
    for topic, plan in cloud_plans.items():
        topic_entries = entries[topic]
        missing_count = max(
            0,
            plan.message_count - len(topic_entries) - failure_counts[topic],
        )
        if missing_count:
            failure_counts[topic] += missing_count
            failure_reasons[topic].append(
                f"{missing_count} message(s) were not returned by the bag reader"
            )

        relative_output = planned_output_path(plan)
        index_relative_path = f"{relative_output}/index.csv"
        index_written = False
        if topic in output_paths:
            try:
                write_pointcloud_index(
                    topic_entries,
                    output_paths[topic] / "index.csv",
                )
                index_written = True
            except OSError as error:
                failure_reasons[topic].append(f"could not write index CSV: {error}")

        if not failure_reasons[topic] and len(topic_entries) == plan.message_count:
            status = "completed"
            reason = None
        elif topic_entries:
            status = "partial"
            reason = "; ".join(failure_reasons[topic][:5])
        else:
            status = "failed"
            reason = (
                "; ".join(failure_reasons[topic][:5])
                or "no point clouds were exported"
            )

        timestamps = [entry.timestamp_ns for entry in topic_entries]
        results[topic] = TopicExtractionResult(
            status=status,
            reason=reason,
            extracted_message_count=len(topic_entries),
            failed_message_count=failure_counts[topic],
            start_timestamp_ns=min(timestamps) if timestamps else None,
            end_timestamp_ns=max(timestamps) if timestamps else None,
            index_path=index_relative_path if index_written else None,
            output_format=output_format,
        )
    return results


def extract_compressed_images(
    inspection: BagInspection, output_directory: Path
) -> dict[str, TopicExtractionResult]:
    """Extract selected ``CompressedImage`` topics without re-encoding."""
    camera_plans = {
        plan.topic: plan
        for plan in inspection.topics
        if plan.selected and plan.message_type == "sensor_msgs/msg/CompressedImage"
    }
    if not camera_plans:
        return {}

    entries: dict[str, list[CameraIndexEntry]] = {
        topic: [] for topic in camera_plans
    }
    message_indices = {topic: 0 for topic in camera_plans}
    failure_counts = {topic: 0 for topic in camera_plans}
    failure_reasons: dict[str, list[str]] = {
        topic: [] for topic in camera_plans
    }
    output_paths: dict[str, Path] = {}
    for topic, plan in camera_plans.items():
        relative_path = planned_output_path(plan)
        assert relative_path is not None
        topic_output = output_directory / relative_path
        try:
            topic_output.mkdir(parents=True, exist_ok=True)
            if not topic_output.is_dir():
                raise OSError(f"output path is not a directory: {topic_output}")
            output_paths[topic] = topic_output
        except OSError as error:
            failure_counts[topic] = plan.message_count
            failure_reasons[topic].append(
                f"could not create output directory: {error}"
            )

    total_messages = sum(plan.message_count for plan in camera_plans.values())
    processed_messages = 0
    last_percentage = 0
    progress_started = total_messages > 0
    if progress_started:
        print(
            f"Camera extraction progress: 0% (0/{total_messages})",
            end="",
            flush=True,
        )

    try:
        with create_bag_reader(inspection.bag_path) as reader:
            connections = [
                connection
                for connection in reader.connections
                if connection.topic in camera_plans
                and connection.msgtype == "sensor_msgs/msg/CompressedImage"
                and connection.topic in output_paths
            ]
            for connection, timestamp_ns, rawdata in reader.messages(
                connections=connections
            ):
                topic = connection.topic
                image_index = message_indices[topic]
                message_indices[topic] += 1
                try:
                    message = reader.deserialize(rawdata, connection.msgtype)
                    entry = export_compressed_image(
                        message,
                        timestamp_ns,
                        output_paths[topic],
                        image_index,
                    )
                    entries[topic].append(entry)
                except Exception as error:
                    failure_counts[topic] += 1
                    reason = f"{type(error).__name__}: {error}"
                    if reason not in failure_reasons[topic]:
                        failure_reasons[topic].append(reason)
                finally:
                    processed_messages += 1
                    if progress_started:
                        percentage = min(
                            100,
                            processed_messages * 100 // total_messages,
                        )
                        if percentage > last_percentage:
                            print(
                                "\rCamera extraction progress: "
                                f"{percentage}% "
                                f"({processed_messages}/{total_messages})",
                                end="",
                                flush=True,
                            )
                            last_percentage = percentage
    except Exception as error:
        for topic in camera_plans:
            failure_reasons[topic].append(f"bag reading failed: {error}")
    finally:
        if progress_started:
            print()

    results: dict[str, TopicExtractionResult] = {}
    for topic, plan in camera_plans.items():
        topic_entries = entries[topic]
        missing_count = max(
            0,
            plan.message_count - len(topic_entries) - failure_counts[topic],
        )
        if missing_count:
            failure_counts[topic] += missing_count
            failure_reasons[topic].append(
                f"{missing_count} message(s) were not returned by the bag reader"
            )

        relative_output = planned_output_path(plan)
        index_relative_path = f"{relative_output}/index.csv"
        index_written = False
        if topic in output_paths:
            try:
                write_camera_index(
                    topic_entries,
                    output_paths[topic] / "index.csv",
                )
                index_written = True
            except OSError as error:
                failure_reasons[topic].append(f"could not write index CSV: {error}")

        if not failure_reasons[topic] and len(topic_entries) == plan.message_count:
            status = "completed"
            reason = None
        elif topic_entries:
            status = "partial"
            reason = "; ".join(failure_reasons[topic][:5])
        else:
            status = "failed"
            reason = (
                "; ".join(failure_reasons[topic][:5])
                or "no images were exported"
            )

        timestamps = [entry.timestamp_ns for entry in topic_entries]
        results[topic] = TopicExtractionResult(
            status=status,
            reason=reason,
            extracted_message_count=len(topic_entries),
            failed_message_count=failure_counts[topic],
            start_timestamp_ns=min(timestamps) if timestamps else None,
            end_timestamp_ns=max(timestamps) if timestamps else None,
            index_path=index_relative_path if index_written else None,
        )
    return results


def topic_manifest_entry(
    plan: TopicPlan, extraction_result: TopicExtractionResult | None = None
) -> dict[str, Any]:
    """Build one serializable manifest topic entry."""
    if extraction_result is not None:
        status = extraction_result.status
        reason = extraction_result.reason
    elif not plan.selected:
        status = "filtered"
        reason = "topic was not selected"
    elif plan.supported:
        status = "planned"
        reason = "exporter is not implemented in the current milestone"
    else:
        status = "skipped"
        reason = plan.reason

    output = None
    if plan.selected and plan.supported:
        output = {
            "format": (
                extraction_result.output_format
                if extraction_result is not None
                and extraction_result.output_format is not None
                else OUTPUT_FORMATS[plan.message_type]
            ),
            "path": planned_output_path(plan),
        }
        if extraction_result is not None and extraction_result.index_path is not None:
            output["index_path"] = extraction_result.index_path

    if extraction_result is None:
        extracted_message_count = 0
        failed_message_count = 0
        timestamp_range_ns = None
    else:
        extracted_message_count = extraction_result.extracted_message_count
        failed_message_count = extraction_result.failed_message_count
        if (
            extraction_result.start_timestamp_ns is None
            and extraction_result.end_timestamp_ns is None
        ):
            timestamp_range_ns = None
        else:
            timestamp_range_ns = {
                "start": extraction_result.start_timestamp_ns,
                "end": extraction_result.end_timestamp_ns,
            }

    return {
        "topic": plan.topic,
        "message_type": plan.message_type,
        "selected": plan.selected,
        "status": status,
        "reason": reason,
        "source_message_count": plan.message_count,
        "extracted_message_count": extracted_message_count,
        "failed_message_count": failed_message_count,
        "timestamp_range_ns": timestamp_range_ns,
        "output": output,
    }


def build_manifest(
    inspection: BagInspection,
    extraction_results: dict[str, TopicExtractionResult] | None = None,
) -> dict[str, Any]:
    """Build the stable inspection-stage manifest document."""
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "mode": "inspection" if extraction_results is None else "extraction",
        "source": {
            "bag": str(inspection.bag_path),
            "message_count": inspection.message_count,
            "start_timestamp_ns": inspection.start_timestamp_ns,
            "end_timestamp_ns_exclusive": inspection.end_timestamp_ns_exclusive,
        },
        "selection": {
            "sensors": list(inspection.selection.sensors),
            "topics": list(inspection.selection.requested_topics),
            "missing_topics": list(inspection.selection.missing_topics),
            "unmatched_sensors": list(inspection.selection.unmatched_sensors),
        },
        "topics": [
            topic_manifest_entry(
                plan,
                None if extraction_results is None else extraction_results.get(plan.topic),
            )
            for plan in inspection.topics
        ],
    }


def write_manifest(manifest: dict[str, Any], output_directory: Path) -> Path:
    """Atomically write ``manifest.json`` and return its path."""
    output_directory.mkdir(parents=True, exist_ok=True)
    if not output_directory.is_dir():
        raise OSError(f"output path is not a directory: {output_directory}")

    manifest_path = output_directory / "manifest.json"
    temporary_path = output_directory / ".manifest.json.tmp"
    try:
        temporary_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(manifest_path)
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise
    return manifest_path


def inspect_bag(
    bag_path: Path,
    sensors: Iterable[str] = (),
    requested_topics: Iterable[str] = (),
) -> BagInspection:
    """Inspect connections in a ROS 2 bag without reading message payloads."""
    if not bag_path.is_dir():
        raise BagInspectionError(f"bag directory does not exist: {bag_path}")
    if not (bag_path / "metadata.yaml").is_file():
        raise BagInspectionError(f"ROS 2 metadata.yaml was not found in: {bag_path}")

    try:
        with create_bag_reader(bag_path) as reader:
            connection_counts: dict[tuple[str, str], int] = {}
            for connection in reader.connections:
                key = (connection.topic, connection.msgtype)
                connection_counts[key] = (
                    connection_counts.get(key, 0) + connection.msgcount
                )
            message_count = reader.message_count
            start_timestamp_ns = reader.start_time if message_count else None
            end_timestamp_ns_exclusive = reader.end_time if message_count else None
    except Exception as error:
        raise BagInspectionError(f"failed to read bag metadata: {error}") from error

    if not connection_counts:
        raise BagInspectionError(f"the bag contains no topic connections: {bag_path}")

    topic_names = {topic for topic, _ in connection_counts}
    selection = select_topic_names(topic_names, sensors, requested_topics)
    connections = (
        (topic, message_type, count)
        for (topic, message_type), count in connection_counts.items()
    )
    plans = build_topic_plans(connections, selection.selected_topics)
    return BagInspection(
        bag_path,
        plans,
        selection,
        message_count,
        start_timestamp_ns,
        end_timestamp_ns_exclusive,
    )


def print_inspection(inspection: BagInspection) -> None:
    """Print a human-readable extraction plan."""
    print(f"Bag: {inspection.bag_path}")
    print("Mode: camera, point-cloud, IMU, and GNSS extraction")
    print()

    for plan in inspection.topics:
        if not plan.selected:
            status = "filtered"
        elif plan.supported:
            status = "selected"
        else:
            status = "unsupported"

        print(f"[{status}] {plan.topic}")
        print(f"  type: {plan.message_type}")
        if plan.selected and plan.supported:
            print(f"  planned output: {plan.output_type}")
        elif plan.selected:
            print(f"  reason: {plan.reason}")

    selected_supported = sum(
        plan.selected and plan.supported for plan in inspection.topics
    )
    selected_unsupported = sum(
        plan.selected and not plan.supported for plan in inspection.topics
    )
    filtered = sum(not plan.selected for plan in inspection.topics)
    print()
    print(
        "Summary: "
        f"{selected_supported} supported selected, "
        f"{selected_unsupported} unsupported selected, "
        f"{filtered} filtered"
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the extractor command-line parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Extract compressed camera images, point clouds, IMU, and GNSS "
            "data from a Berlin Waters ROS 2 bag."
        ),
        epilog=(
            "When --sensor and --topic are combined, topics matching either "
            "selector are included."
        ),
    )
    parser.add_argument("bag", type=Path, help="path to a ROS 2 bag directory")
    parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        help="extraction output directory (default: ./<bag-name>_extracted)",
    )
    parser.add_argument(
        "--sensor",
        action="append",
        default=[],
        metavar="NAME",
        help="select a named sensor; may be supplied more than once",
    )
    parser.add_argument(
        "--topic",
        action="append",
        default=[],
        metavar="TOPIC",
        help="select an exact topic; may be supplied more than once",
    )
    parser.add_argument(
        "--pointcloud-format",
        choices=("pcd", "ply"),
        default="pcd",
        help="point-cloud output format (default: %(default)s)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Extract compressed images and write the current extraction manifest."""
    parser = build_parser()
    args = parser.parse_args(argv)

    bag_path = args.bag.expanduser().resolve()
    try:
        inspection = inspect_bag(bag_path, args.sensor, args.topic)
    except (BagInspectionError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    for topic in inspection.selection.missing_topics:
        print(f"warning: requested topic is not in the bag: {topic}", file=sys.stderr)
    for sensor in inspection.selection.unmatched_sensors:
        print(f"warning: sensor has no matching topics in the bag: {sensor}", file=sys.stderr)

    if not inspection.selection.selected_topics:
        print("error: the supplied selectors matched no topics", file=sys.stderr)
        return 2

    output_directory = (
        args.output.expanduser().resolve()
        if args.output is not None
        else Path.cwd() / f"{bag_path.name}_extracted"
    )
    print_inspection(inspection)
    extraction_results = extract_compressed_images(inspection, output_directory)
    extraction_results.update(extract_tabular_topics(inspection, output_directory))
    extraction_results.update(
        extract_pointcloud_topics(
            inspection,
            output_directory,
            args.pointcloud_format,
        )
    )
    if extraction_results:
        print()
        print("Extraction results:")
        for topic, result in sorted(extraction_results.items()):
            print(
                f"[{result.status}] {topic}: "
                f"{result.extracted_message_count} exported, "
                f"{result.failed_message_count} failed"
            )
    else:
        print()
        print("Extraction: no selected camera, point-cloud, IMU, or GNSS topics")

    try:
        manifest_path = write_manifest(
            build_manifest(inspection, extraction_results),
            output_directory,
        )
    except OSError as error:
        print(f"error: could not write manifest: {error}", file=sys.stderr)
        return 1

    print(f"Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
