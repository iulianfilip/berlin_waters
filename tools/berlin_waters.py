#!/usr/bin/env python3
"""Lightweight command-line utilities for the Berlin Waters dataset.

These tools operate on the standard ROS 2 rosbag2 metadata.yaml file and on the
machine-readable metadata shipped with this repository. They do not deserialize
bag messages, so basic inspection works without sourcing ROS 2.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENES = ROOT / "metadata" / "scenes.yaml"
DEFAULT_SENSORS = ROOT / "metadata" / "sensors.yaml"
SCENE_RE = re.compile(r"(berlin_\d{2})")


def load_yaml(path: Path):
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Expected a mapping in {path}")
    return data


def metadata_path(path: Path) -> Path:
    if path.is_file():
        if path.name != "metadata.yaml":
            raise ValueError("Expected a rosbag2 metadata.yaml file or bag directory")
        return path
    candidate = path / "metadata.yaml"
    if not candidate.is_file():
        raise FileNotFoundError(f"No metadata.yaml found in {path}")
    return candidate


def load_bag(path: Path):
    raw = load_yaml(metadata_path(path))
    info = raw.get("rosbag2_bagfile_information", raw)
    if not isinstance(info, dict):
        raise ValueError("Unrecognized rosbag2 metadata structure")
    return info


def duration_seconds(info):
    duration = info.get("duration") or {}
    ns = duration.get("nanoseconds") if isinstance(duration, dict) else None
    return None if ns is None else float(ns) / 1e9


def topics(info):
    duration = duration_seconds(info)
    result = []
    for item in info.get("topics_with_message_count", []) or []:
        meta = item.get("topic_metadata", {}) or {}
        count = item.get("message_count")
        result.append(
            {
                "name": meta.get("name", ""),
                "type": meta.get("type", ""),
                "message_count": count,
                "average_rate_hz": (count / duration) if duration and isinstance(count, int) else None,
            }
        )
    return sorted(result, key=lambda x: x["name"])


def scene_id_from_path(path: Path):
    match = SCENE_RE.search(str(path))
    return match.group(1) if match else None


def find_scene(config, scene_id):
    for scene in config.get("scenes", []):
        if scene.get("id") == scene_id:
            return scene
    raise KeyError(f"Unknown scene: {scene_id}")


def documented_topic_map(sensor_config):
    result = {}
    for sensor_id, sensor in sensor_config.get("sensors", {}).items():
        for topic in sensor.get("topics", []) or []:
            name = topic.get("name")
            if name:
                result[name] = sensor_id
    return result


def expected_topics(scene, sensor_config):
    expected = set()
    for sensor_id, available in (scene.get("sensors") or {}).items():
        if not available:
            continue
        sensor = sensor_config.get("sensors", {}).get(sensor_id, {})
        if sensor.get("check_in_rosbag", True) is False:
            continue
        for topic in sensor.get("topics", []) or []:
            if topic.get("required", True) and topic.get("name"):
                expected.add(topic["name"])
    return expected


def unavailable_topics(scene, sensor_config):
    result = set()
    for sensor_id, available in (scene.get("sensors") or {}).items():
        if available:
            continue
        sensor = sensor_config.get("sensors", {}).get(sensor_id, {})
        if sensor.get("check_in_rosbag", True) is False:
            continue
        result.update(t["name"] for t in sensor.get("topics", []) if t.get("name"))
    return result


def print_table(headers, rows):
    rows = [["" if v is None else str(v) for v in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in rows:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(value))

    def line(row):
        return "  ".join(value.ljust(widths[i]) for i, value in enumerate(row))

    print(line(list(headers)))
    print(line(["-" * w for w in widths]))
    for row in rows:
        print(line(row))


def cmd_scenes(args):
    cfg = load_yaml(args.scenes_file)
    scenes = cfg.get("scenes", [])
    if args.sensor:
        scenes = [s for s in scenes if (s.get("sensors") or {}).get(args.sensor, False)]
    if args.json:
        print(json.dumps({"scenes": scenes}, indent=2))
        return 0
    print_table(
        ("Scene", "Date", "GB", "Min", "Km", "Description"),
        [(s["id"], s["date"], s["size_gb"], s["duration_min"], s["distance_km"], s["description"]) for s in scenes],
    )
    return 0


def cmd_sensors(args):
    cfg = load_yaml(args.sensors_file)
    sensors = cfg.get("sensors", {})
    if args.json:
        print(json.dumps({"sensors": sensors}, indent=2))
        return 0
    rows = []
    for sensor_id, sensor in sensors.items():
        names = ", ".join(t.get("name", "") for t in sensor.get("topics", []) if t.get("name"))
        rows.append((sensor_id, sensor.get("display_name"), sensor.get("category"), sensor.get("nominal_rate_hz"), names or "(not in rosbag)"))
    print_table(("ID", "Sensor", "Category", "Hz", "ROS 2 topics"), rows)
    return 0


def cmd_bag_info(args):
    info = load_bag(args.bag)
    rows = topics(info)
    output = {
        "metadata_file": str(metadata_path(args.bag)),
        "storage_identifier": info.get("storage_identifier"),
        "duration_seconds": duration_seconds(info),
        "message_count": info.get("message_count"),
        "relative_file_paths": info.get("relative_file_paths", []),
        "topics": rows,
    }
    if args.json:
        print(json.dumps(output, indent=2))
        return 0
    print(f"Metadata: {output['metadata_file']}")
    print(f"Storage:  {output['storage_identifier']}")
    if output["duration_seconds"] is not None:
        print(f"Duration: {output['duration_seconds']:.3f} s")
    print(f"Messages: {output['message_count']}\n")
    print_table(
        ("Topic", "Type", "Messages", "Avg Hz"),
        [(x["name"], x["type"], x["message_count"], "" if x["average_rate_hz"] is None else f"{x['average_rate_hz']:.3f}") for x in rows],
    )
    return 0


def cmd_validate(args):
    sensor_cfg = load_yaml(args.sensors_file)
    scene_cfg = load_yaml(args.scenes_file)
    scene_id = args.scene or scene_id_from_path(args.bag)
    if not scene_id:
        raise ValueError("Could not infer scene ID; pass --scene berlin_XX")
    scene = find_scene(scene_cfg, scene_id)
    present = {t["name"] for t in topics(load_bag(args.bag)) if t["name"]}
    expected = expected_topics(scene, sensor_cfg)
    unavailable = unavailable_topics(scene, sensor_cfg)
    documented = set(documented_topic_map(sensor_cfg))
    result = {
        "scene": scene_id,
        "status": "pass" if not (expected - present) else "fail",
        "missing_expected_topics": sorted(expected - present),
        "topics_present_for_unavailable_sensors": sorted(present & unavailable),
        "additional_undocumented_topics": sorted(present - documented),
        "note": "Topic-level consistency check only; message contents, timestamps, calibration, sensor health, and RINEX files are not validated.",
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Scene:  {scene_id}")
        print(f"Status: {result['status'].upper()}")
        for label, key in [
            ("Missing expected topics", "missing_expected_topics"),
            ("Topics present although sensor is documented unavailable", "topics_present_for_unavailable_sensors"),
            ("Additional undocumented topics", "additional_undocumented_topics"),
        ]:
            if result[key]:
                print(f"\n{label}:")
                for topic in result[key]:
                    print(f"  - {topic}")
        print(f"\n{result['note']}")
    return 0 if result["status"] == "pass" else 1


def parser():
    p = argparse.ArgumentParser(description="Berlin Waters dataset utilities")
    p.add_argument("--scenes-file", type=Path, default=DEFAULT_SCENES)
    p.add_argument("--sensors-file", type=Path, default=DEFAULT_SENSORS)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scenes", help="List documented scenes")
    s.add_argument("--sensor", help="Filter to scenes where a sensor ID is available")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_scenes)

    s = sub.add_parser("sensors", help="List sensors and ROS 2 topics")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_sensors)

    s = sub.add_parser("bag-info", help="Inspect a ROS 2 bag through metadata.yaml")
    s.add_argument("bag", type=Path)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_bag_info)

    s = sub.add_parser("validate", help="Check bag topics against documented scene availability")
    s.add_argument("bag", type=Path)
    s.add_argument("--scene", help="Scene ID, e.g. berlin_06")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_validate)
    return p


def main():
    args = parser().parse_args()
    try:
        return int(args.func(args))
    except (FileNotFoundError, KeyError, ValueError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
