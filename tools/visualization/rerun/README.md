# Rerun visualization

Rerun displays *Berlin Waters* camera images, LiDAR scans, IMU plots, and GNSS
positions without ROS 2. Complete the [Python setup](../../../README.md#installation)
and run commands from the repository root.

## View a bag directly

```bash
.venv/bin/python tools/visualization/rerun/view_bag.py /path/to/berlin_01
```

The viewer reads the bag directory containing `metadata.yaml` and `.db3` files.
It places each LiDAR in its own 3D panel and plots the available camera, IMU,
and GNSS streams.

## View extracted files

First export data with the [extractor](../../extraction/README.md), then open
its output directory:

```bash
.venv/bin/python tools/visualization/rerun/view.py /path/to/berlin_01_extracted
```

This viewer opens the extracted images and PCD/PLY scans on one timeline.

## Save a recording

Use `--save` to write an `.rrd` file instead of opening the live viewer. For
example, save the first ten seconds of a bag:

```bash
.venv/bin/python tools/visualization/rerun/view_bag.py \
  /path/to/berlin_01 --duration 10 --rate 0 --save berlin_01_preview.rrd
.venv/bin/rerun berlin_01_preview.rrd
```

GNSS map tiles require an internet connection. For playback speed, start time,
point limits, and other options, run either viewer with `--help`.
