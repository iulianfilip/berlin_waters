# Berlin Waters tools

This directory contains lightweight utilities for inspecting and validating Berlin Waters dataset scenes.

The first tools deliberately operate on the standard ROS 2 `metadata.yaml` file that accompanies each bag. This keeps the basic workflow fast and avoids requiring a sourced ROS 2 environment just to inspect a scene.

## Installation

From the repository root:

```bash
python3 -m pip install -r requirements-tools.txt
```

## Commands

### List scenes

```bash
python3 tools/berlin_waters.py scenes
```

Filter to scenes that contain a documented sensor:

```bash
python3 tools/berlin_waters.py scenes --sensor ouster
```

List available sensor IDs and canonical ROS 2 topics:

```bash
python3 tools/berlin_waters.py sensors
```

### Inspect a ROS 2 bag

Pass either the bag directory or its `metadata.yaml` file:

```bash
python3 tools/berlin_waters.py bag-info /path/to/berlin_06
```

The command reports the storage backend, duration, total messages, topic types, message counts, and average message rates.

Machine-readable output is available with `--json`.

### Validate a scene against published metadata

```bash
python3 tools/berlin_waters.py validate /path/to/berlin_06
```

If the scene ID cannot be inferred from the path:

```bash
python3 tools/berlin_waters.py validate /path/to/bag --scene berlin_06
```

The validator compares topics in the bag metadata with the sensor availability documented in `metadata/scenes.yaml` and the canonical topic catalog in `metadata/sensors.yaml`.

This is a **topic-level consistency check only**. It does not validate message contents, calibration, timestamp quality, sensor health, or RINEX files.

## Machine-readable metadata

- `metadata/sensors.yaml` — canonical sensor names, nominal rates, and ROS 2 topics.
- `metadata/scenes.yaml` — scene sizes, durations, distances, descriptions, and sensor availability derived from the repository README.

These files provide a single programmatic source for later dataset tools instead of hard-coding scene and sensor information in multiple scripts.

## Design direction

Established robotics datasets typically provide more than download links. Useful patterns include:

- a machine-readable dataset schema or metadata catalog;
- a small SDK or loader API;
- sample/mini data for debugging;
- visualization and sensor-fusion examples;
- validation utilities;
- canonical train/validation/test splits;
- evaluation code and result formats;
- selective download helpers;
- tutorials and reproducible examples.

Representative examples are the nuScenes devkit, SemanticKITTI API, Oxford RobotCar SDK, and Boreas `pyboreas`.

## Planned Berlin Waters extensions

The following should be added once the released files, calibration package, and download endpoints are stable enough to test them properly:

1. **Selective download** — choose scenes and, if the hosting layout permits it, sensor subsets; verify checksums and file sizes.
2. **ROS 2 extraction** — extract ZED images, GNSS/IMU CSV, LiDAR frames, topics, and time intervals without manually composing `ros2 bag` commands.
3. **Visualization** — RViz configuration, trajectory/map viewer, synchronized camera/LiDAR examples, and semantic-mask overlays.
4. **Calibration/transforms** — load intrinsic/extrinsic calibration, transform between sensor/body frames, and project LiDAR into camera images.
5. **SLAM evaluation** — documented trajectory format, timestamp association, alignment, ATE/RPE/drift metrics, and a canonical result format.
6. **Semantic-segmentation utilities** — class palette, mask decoding/overlay, split definitions, IoU/mIoU evaluation, and prediction validation.
7. **Dataset integrity** — scene checksums, expected file/topic counts, calibration/version identifiers, and automated smoke tests using a small public sample.

## Design principle

Tools should be small, scriptable, reproducible, and usable from the command line before heavier graphical or framework-specific dependencies are introduced. Dataset metadata should have one authoritative machine-readable representation wherever practical.
