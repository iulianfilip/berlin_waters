# Data extraction

Export images, point clouds, IMU, and GNSS data from a *Berlin Waters* ROS 2
bag without installing ROS 2. Complete the [Python setup](../../README.md#installation)
and run these commands from the repository root. Pass the bag **directory**
containing `metadata.yaml` and `.db3` files.

## Run the extractor

Export all supported data:

```bash
.venv/bin/python tools/extraction/extract.py \
  /path/to/berlin_06 /path/to/berlin_06_extracted
```

Export one sensor or one exact topic:

```bash
.venv/bin/python tools/extraction/extract.py \
  /path/to/berlin_06 /path/to/berlin_06_ouster \
  --sensor ouster

.venv/bin/python tools/extraction/extract.py \
  /path/to/berlin_06 /path/to/berlin_06_left_camera \
  --topic /zed/zed_node/left/image_rect_color/compressed
```

Repeat `--sensor` or `--topic` to select multiple streams. When both are used,
the extractor includes topics matching either selector. A sensor selects its
entire namespace; use `--topic` for a particular stream. Sensor names are
`velodyne` (`vlp32`), `ouster`, `livox`, `zed2i` (`zed`), `xsens`, and `ublox`.
With no selectors, all supported topics are exported.

## Output

For a seven-column IMU CSV and scene information, select one IMU topic:

```bash
.venv/bin/python tools/extraction/extract.py /path/to/berlin_06 \
  --topic /xsens/imu/data --imu-format compact
```

This writes two files in `extracted/berlin_06/`:

- `berlin_06_imu.csv`: the seven columns below.
- `berlin_06_info.txt`: scene name, first and last bag timestamps in Unix
  seconds (two decimal places), and CSV columns with units.

```text
timestamp_unix,acc_x,acc_y,acc_z,gyro_x,gyro_y,gyro_z
```

Time is the Unix bag timestamp in seconds; acceleration is in m/s² and angular
velocity in rad/s.

The bag timestamps cover all recorded topics. An explicit output path overrides
the default scene directory. Use a directory reserved for these two files.

Standard extraction uses the following layout:

| Data | Files |
| --- | --- |
| Compressed camera images | `images/<topic>/` with original `.jpg` or `.png` files and `index.csv` |
| LiDAR scans | `pointclouds/<topic>/` with binary PCD files and `index.csv` |
| IMU and GNSS | One CSV per topic under `tables/` |
| Extraction report | `manifest.json` with topic counts, output paths, and results |

Append `--pointcloud-format ply` to write binary PLY scans instead of PCD. The
point-cloud exporter retains supported fields such as intensity, ring, and
time; points with non-finite coordinates or intensity are omitted. Image data
is copied without re-encoding. CSV and index files include exact bag and
message-header timestamps.

For all options, run `.venv/bin/python tools/extraction/extract.py --help`.
