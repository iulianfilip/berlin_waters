#!/usr/bin/env python3
"""Visualize extracted Berlin Waters camera and LiDAR data with Rerun."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Iterator, Sequence
import warnings

import numpy as np


TIMELINE = "sensor_time"
SUPPORTED_TOPIC_STATUSES = {"completed", "partial"}
PCD_TYPES = {
    ("F", 4): np.dtype("<f4"),
    ("F", 8): np.dtype("<f8"),
    ("I", 1): np.dtype("i1"),
    ("I", 2): np.dtype("<i2"),
    ("I", 4): np.dtype("<i4"),
    ("U", 1): np.dtype("u1"),
    ("U", 2): np.dtype("<u2"),
    ("U", 4): np.dtype("<u4"),
}
PLY_TYPES = {
    "char": np.dtype("i1"),
    "int8": np.dtype("i1"),
    "uchar": np.dtype("u1"),
    "uint8": np.dtype("u1"),
    "short": np.dtype("<i2"),
    "int16": np.dtype("<i2"),
    "ushort": np.dtype("<u2"),
    "uint16": np.dtype("<u2"),
    "int": np.dtype("<i4"),
    "int32": np.dtype("<i4"),
    "uint": np.dtype("<u4"),
    "uint32": np.dtype("<u4"),
    "float": np.dtype("<f4"),
    "float32": np.dtype("<f4"),
    "double": np.dtype("<f8"),
    "float64": np.dtype("<f8"),
}


class ViewerError(RuntimeError):
    """A user-facing error in extracted data or viewer configuration."""


def build_parser() -> argparse.ArgumentParser:
    """Build the Rerun viewer command-line parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Visualize extracted Berlin Waters images and PCD/PLY scans with "
            "Rerun, without ROS 2."
        )
    )
    parser.add_argument(
        "extracted_directory",
        type=Path,
        help="directory produced by the Berlin Waters extraction tool",
    )
    parser.add_argument(
        "--max-points",
        type=int,
        default=200_000,
        help="maximum points displayed per scan (default: %(default)s)",
    )
    parser.add_argument(
        "--save",
        type=Path,
        metavar="RECORDING.RRD",
        help="save a Rerun recording instead of spawning the viewer",
    )
    return parser


def load_manifest(extracted_directory: Path) -> dict[str, Any]:
    """Load and minimally validate an extraction manifest."""
    manifest_path = extracted_directory / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ViewerError(f"manifest not found: {manifest_path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise ViewerError(f"could not read {manifest_path}: {error}") from error
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ViewerError("manifest.json does not use supported schema version 1")
    if not isinstance(manifest.get("topics"), list):
        raise ViewerError("manifest.json has no valid topics list")
    return manifest


def parse_unix_timestamp_ns(value: str) -> int:
    """Parse Unix seconds.nanoseconds without losing integer precision."""
    match = re.fullmatch(r"([+-]?)(\d+)(?:\.(\d{1,9}))?", value.strip())
    if match is None:
        raise ViewerError(f"invalid Unix timestamp: {value!r}")
    sign_text, seconds_text, fraction_text = match.groups()
    nanoseconds = int(seconds_text) * 1_000_000_000
    nanoseconds += int((fraction_text or "").ljust(9, "0"))
    return -nanoseconds if sign_text == "-" else nanoseconds


def resolve_relative(root: Path, relative: str) -> Path:
    """Resolve a manifest/index path while preventing escape from the dataset."""
    if not relative:
        raise ViewerError("manifest contains an empty output path")
    root = root.resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise ViewerError(f"path escapes extracted directory: {relative!r}")
    return candidate


def topic_entity_path(topic: str, message_type: str) -> str:
    """Map a ROS topic to a stable, readable Rerun entity path."""
    lower = topic.lower()
    if message_type == "sensor_msgs/msg/CompressedImage":
        if "/left/" in lower:
            return "world/cameras/zed_left/image"
        if "/right/" in lower:
            return "world/cameras/zed_right/image"
    if message_type == "sensor_msgs/msg/PointCloud2":
        if "vlp32" in lower or "velodyne" in lower:
            return "world/sensors/velodyne/points"
        if "ouster" in lower:
            return "world/sensors/ouster/points"
        if "livox" in lower:
            return "world/sensors/livox/points"
    sanitized = "/".join(
        re.sub(r"[^A-Za-z0-9_]", "_", part)
        for part in topic.strip("/").split("/")
        if part
    )
    return f"world/topics/{sanitized or 'unnamed'}"


def _read_header(input_file: Any, terminator: bytes) -> list[str]:
    lines: list[str] = []
    for _ in range(10_000):
        line = input_file.readline()
        if not line:
            raise ViewerError("point-cloud file has a truncated header")
        try:
            text = line.decode("ascii").strip()
        except UnicodeDecodeError as error:
            raise ViewerError("point-cloud header is not ASCII") from error
        lines.append(text)
        if line.strip().lower() == terminator:
            return lines
    raise ViewerError("point-cloud header is unreasonably long")


def _xyz_intensity(values: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
    names = values.dtype.names or ()
    names_by_lower = {name.lower(): name for name in names}
    missing = [name for name in ("x", "y", "z") if name not in names_by_lower]
    if missing:
        raise ViewerError(f"point cloud is missing fields: {', '.join(missing)}")
    coordinates = []
    for name in ("x", "y", "z"):
        field = values[names_by_lower[name]]
        if field.ndim > 1:
            field = field[:, 0]
        coordinates.append(field.astype(np.float32, copy=False))
    points = np.ascontiguousarray(np.column_stack(coordinates), dtype=np.float32)
    intensity = None
    if "intensity" in names_by_lower:
        intensity_values = values[names_by_lower["intensity"]]
        if intensity_values.ndim > 1:
            intensity_values = intensity_values[:, 0]
        intensity = np.ascontiguousarray(intensity_values, dtype=np.float32)
    return points, intensity


def read_pcd(path: Path) -> tuple[np.ndarray, np.ndarray | None]:
    """Read XYZ and optional intensity from a binary PCD written by extraction."""
    try:
        with path.open("rb") as input_file:
            lines = _read_header(input_file, b"data binary")
            metadata: dict[str, list[str]] = {}
            for line in lines:
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                metadata[parts[0].upper()] = parts[1:]
            if [part.lower() for part in metadata.get("DATA", [])] != ["binary"]:
                raise ViewerError(f"only binary PCD is supported: {path}")
            fields = metadata.get("FIELDS", [])
            sizes = [int(value) for value in metadata.get("SIZE", [])]
            types = metadata.get("TYPE", [])
            counts = [int(value) for value in metadata.get("COUNT", [])]
            if not counts:
                counts = [1] * len(fields)
            if not (len(fields) == len(sizes) == len(types) == len(counts)):
                raise ViewerError(f"inconsistent PCD field metadata: {path}")
            formats = []
            for type_name, size, count in zip(types, sizes, counts):
                try:
                    base = PCD_TYPES[(type_name.upper(), size)]
                except KeyError as error:
                    raise ViewerError(
                        f"unsupported PCD scalar type {type_name}{size}: {path}"
                    ) from error
                formats.append(base if count == 1 else (base, (count,)))
            dtype = np.dtype(list(zip(fields, formats)))
            point_values = metadata.get("POINTS")
            if point_values:
                point_count = int(point_values[0])
            else:
                point_count = int(metadata["WIDTH"][0]) * int(metadata["HEIGHT"][0])
            payload = input_file.read()
    except OSError as error:
        raise ViewerError(f"could not read {path}: {error}") from error
    expected_size = point_count * dtype.itemsize
    if len(payload) != expected_size:
        raise ViewerError(
            f"PCD payload size mismatch in {path}: expected {expected_size}, "
            f"received {len(payload)}"
        )
    return _xyz_intensity(np.frombuffer(payload, dtype=dtype, count=point_count))


def read_ply(path: Path) -> tuple[np.ndarray, np.ndarray | None]:
    """Read XYZ and optional intensity from a binary little-endian PLY file."""
    try:
        with path.open("rb") as input_file:
            lines = _read_header(input_file, b"end_header")
            if not lines or lines[0] != "ply":
                raise ViewerError(f"not a PLY file: {path}")
            if "format binary_little_endian 1.0" not in lines:
                raise ViewerError(f"only binary little-endian PLY is supported: {path}")
            vertex_count: int | None = None
            in_vertex = False
            properties: list[tuple[str, np.dtype[Any]]] = []
            for line in lines[1:]:
                parts = line.split()
                if parts[:2] == ["element", "vertex"] and len(parts) == 3:
                    vertex_count = int(parts[2])
                    in_vertex = True
                elif parts and parts[0] == "element":
                    in_vertex = False
                elif in_vertex and parts and parts[0] == "property":
                    if len(parts) != 3 or parts[1] == "list":
                        raise ViewerError(f"unsupported PLY vertex property: {line}")
                    try:
                        datatype = PLY_TYPES[parts[1].lower()]
                    except KeyError as error:
                        raise ViewerError(
                            f"unsupported PLY scalar type {parts[1]}: {path}"
                        ) from error
                    properties.append((parts[2], datatype))
            if vertex_count is None or not properties:
                raise ViewerError(f"PLY has no vertex definition: {path}")
            dtype = np.dtype(properties)
            payload = input_file.read()
    except OSError as error:
        raise ViewerError(f"could not read {path}: {error}") from error
    expected_size = vertex_count * dtype.itemsize
    if len(payload) < expected_size:
        raise ViewerError(
            f"PLY payload is truncated in {path}: expected at least {expected_size}, "
            f"received {len(payload)}"
        )
    return _xyz_intensity(np.frombuffer(payload, dtype=dtype, count=vertex_count))


def read_pointcloud(path: Path) -> tuple[np.ndarray, np.ndarray | None]:
    """Dispatch PCD/PLY reading from the filename extension."""
    suffix = path.suffix.lower()
    if suffix == ".pcd":
        return read_pcd(path)
    if suffix == ".ply":
        return read_ply(path)
    raise ViewerError(f"unsupported point-cloud extension: {path}")


def downsample_points(
    points: np.ndarray,
    intensity: np.ndarray | None,
    maximum: int,
) -> tuple[np.ndarray, np.ndarray | None]:
    """Deterministically cap the number of displayed points in a scan."""
    if maximum <= 0:
        raise ViewerError("--max-points must be greater than zero")
    if len(points) <= maximum:
        return points, intensity
    indices = np.linspace(0, len(points) - 1, maximum, dtype=np.int64)
    return points[indices], None if intensity is None else intensity[indices]


def intensity_colors(intensity: np.ndarray | None) -> np.ndarray | None:
    """Map finite intensity values to grayscale RGB colors for Rerun."""
    if intensity is None or not len(intensity):
        return None
    finite = np.isfinite(intensity)
    if not finite.any():
        return None
    low = float(np.min(intensity[finite]))
    high = float(np.max(intensity[finite]))
    scaled = np.full(len(intensity), 128, dtype=np.uint8)
    if high > low:
        normalized = np.clip((intensity[finite] - low) / (high - low), 0.0, 1.0)
        scaled[finite] = np.asarray(normalized * 255.0, dtype=np.uint8)
    return np.repeat(scaled[:, None], 3, axis=1)


def index_rows(index_path: Path) -> Iterator[dict[str, str]]:
    """Yield validated rows from an extraction index CSV."""
    try:
        with index_path.open(newline="", encoding="utf-8") as index_file:
            reader = csv.DictReader(index_file)
            required = {"filename", "timestamp_unix"}
            if not required.issubset(reader.fieldnames or []):
                raise ViewerError(f"index lacks required columns: {index_path}")
            yield from reader
    except OSError as error:
        raise ViewerError(f"could not read {index_path}: {error}") from error


def set_timestamp(recording: Any, timestamp_ns: int) -> None:
    """Set an exact Unix-nanosecond timestamp on the shared Rerun timeline."""
    recording.set_time(TIMELINE, timestamp=np.datetime64(timestamp_ns, "ns"))


def log_camera_topic(rr: Any, recording: Any, root: Path, topic: dict[str, Any]) -> int:
    output = topic["output"]
    output_directory = resolve_relative(root, output["path"])
    index_path = resolve_relative(root, output.get("index_path", f"{output['path']}/index.csv"))
    entity = topic_entity_path(topic["topic"], topic["message_type"])
    count = 0
    for row in index_rows(index_path):
        image_path = resolve_relative(output_directory, row["filename"])
        if not image_path.is_file():
            raise ViewerError(f"indexed image not found: {image_path}")
        set_timestamp(recording, parse_unix_timestamp_ns(row["timestamp_unix"]))
        recording.log(entity, rr.EncodedImage(path=image_path))
        count += 1
    return count


def log_pointcloud_topic(
    rr: Any,
    recording: Any,
    root: Path,
    topic: dict[str, Any],
    maximum_points: int,
) -> int:
    output = topic["output"]
    output_directory = resolve_relative(root, output["path"])
    index_path = resolve_relative(root, output.get("index_path", f"{output['path']}/index.csv"))
    entity = topic_entity_path(topic["topic"], topic["message_type"])
    count = 0
    for row in index_rows(index_path):
        cloud_path = resolve_relative(output_directory, row["filename"])
        points, intensity = read_pointcloud(cloud_path)
        points, intensity = downsample_points(points, intensity, maximum_points)
        colors = intensity_colors(intensity)
        set_timestamp(recording, parse_unix_timestamp_ns(row["timestamp_unix"]))
        if colors is None:
            recording.log(entity, rr.Points3D(points))
        else:
            recording.log(entity, rr.Points3D(points, colors=colors))
        count += 1
    return count


def log_trajectory(rr: Any, recording: Any, root: Path) -> int:
    """Log an optional trajectory.csv containing x, y, and z columns."""
    trajectory_path = root / "trajectory.csv"
    if not trajectory_path.is_file():
        return 0
    points: list[tuple[float, float, float]] = []
    try:
        with trajectory_path.open(newline="", encoding="utf-8") as trajectory_file:
            reader = csv.DictReader(trajectory_file)
            if not {"x", "y", "z"}.issubset(reader.fieldnames or []):
                raise ViewerError("trajectory.csv must contain x, y, and z columns")
            for row in reader:
                points.append((float(row["x"]), float(row["y"]), float(row["z"])))
    except (OSError, ValueError) as error:
        raise ViewerError(f"could not read {trajectory_path}: {error}") from error
    if points:
        recording.log(
            "world/trajectory/path",
            rr.LineStrips3D([np.asarray(points, dtype=np.float32)]),
            static=True,
        )
    return len(points)


def log_extracted_data(
    rr: Any,
    recording: Any,
    root: Path,
    manifest: dict[str, Any],
    maximum_points: int,
) -> tuple[int, int, int]:
    """Log supported extracted topics and return image/scan/trajectory counts."""
    image_count = 0
    scan_count = 0
    trajectory_count = log_trajectory(rr, recording, root)
    for topic in manifest["topics"]:
        if (
            not isinstance(topic, dict)
            or topic.get("status") not in SUPPORTED_TOPIC_STATUSES
            or not isinstance(topic.get("output"), dict)
        ):
            continue
        message_type = topic.get("message_type")
        if message_type == "sensor_msgs/msg/CompressedImage":
            image_count += log_camera_topic(rr, recording, root, topic)
        elif message_type == "sensor_msgs/msg/PointCloud2":
            scan_count += log_pointcloud_topic(rr, recording, root, topic, maximum_points)
    return image_count, scan_count, trajectory_count


def import_rerun() -> Any:
    """Import the optional SDK with an actionable error message."""
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Python 3.10 reaches end-of-life.*",
                category=DeprecationWarning,
            )
            import rerun as rr
    except ImportError as error:
        raise ViewerError(
            "rerun-sdk is not installed; create/activate .venv and install "
            "requirements-tools.txt"
        ) from error
    return rr


@contextmanager
def rerun_viewer_on_path() -> Iterator[None]:
    """Make a venv-local Rerun executable discoverable while spawning it."""
    original_path = os.environ.get("PATH")
    executable_directory = Path(sys.executable).parent
    viewer_executable = executable_directory / "rerun"
    needs_venv_path = shutil.which("rerun") is None and viewer_executable.is_file()
    if needs_venv_path:
        os.environ["PATH"] = os.pathsep.join(
            part
            for part in (str(executable_directory), original_path)
            if part
        )
    try:
        yield
    finally:
        if needs_venv_path:
            if original_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = original_path


def main(argv: Sequence[str] | None = None) -> int:
    """Load extracted files and either spawn Rerun or save an RRD recording."""
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.max_points <= 0:
        parser.error("--max-points must be greater than zero")
    root = arguments.extracted_directory.resolve()
    try:
        manifest = load_manifest(root)
        rr = import_rerun()
        recording_id = root.name or "extracted"
        with rr.RecordingStream(
            application_id="berlin_waters",
            recording_id=recording_id,
        ) as recording:
            if arguments.save is None:
                with rerun_viewer_on_path():
                    recording.spawn()
            else:
                arguments.save.parent.mkdir(parents=True, exist_ok=True)
                recording.save(str(arguments.save))
            images, scans, trajectory_points = log_extracted_data(
                rr, recording, root, manifest, arguments.max_points
            )
    except ViewerError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"error: Rerun visualization failed: {error}", file=sys.stderr)
        return 1

    destination = (
        f"saved to {arguments.save}"
        if arguments.save is not None
        else "sent to the Rerun viewer"
    )
    print(
        f"Rerun: {images} image(s), {scans} scan(s), and "
        f"{trajectory_points} trajectory point(s) {destination}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
