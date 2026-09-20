# Rerun visualization

## Direct bag playback without ROS 2

The direct viewer reads the ROS 2 SQLite bag with `rosbags`; it does not require
ROS 2 and does not create extracted files. It shows:

- left and right compressed camera images plus their `CameraInfo` calibration;
- Xsens angular velocity and linear acceleration plots;
- all standard GNSS fixes together over OpenStreetMap, with one color per receiver;
- one independent 3D panel per PointCloud2 LiDAR.

The default dashboard keeps every modality visible at once: stacked stereo
cameras on the upper left, GNSS in the upper center, stacked IMU plots on the
upper right, and an equal-width row of every available LiDAR across the bottom.
Livox points are red, Velodyne points are green, and Ouster points are blue.
Both IMU plots use the same convention: X is red, Y is green, and Z is blue.

Start real-time playback with the default 2 GB rolling Rerun window:

```bash
cd /path/to/berlin_waters
.venv/bin/python tools/visualization/rerun/view_bag.py \
  /path/to/berlin_01
```

Useful playback controls:

```bash
# Start five minutes into the bag and play at twice real time.
.venv/bin/python tools/visualization/rerun/view_bag.py \
  /path/to/berlin_01 --start 300 --rate 2

# Use a smaller rolling window and display at most 100,000 points per scan.
.venv/bin/python tools/visualization/rerun/view_bag.py \
  /path/to/berlin_01 --memory-limit 1GB --max-points 100000
```

The default IMU display rate is limited to 50 Hz for interactive plotting; use
`--imu-max-hz 0` to retain every recorded sample. Old messages are discarded
when the live viewer reaches its memory limit. OpenStreetMap tiles require an
internet connection. LiDARs are deliberately kept in separate 3D panels until
verified extrinsic transforms are supplied.

## Extracted-data playback

The viewer displays extracted camera images and binary PCD/PLY point clouds on
one Unix-time timeline without requiring ROS 2. Point intensity is shown as
grayscale, and `--max-points` deterministically limits the number of displayed
points per scan without changing the extracted files.

Create the project-local environment once:

```bash
cd /path/to/berlin_waters
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-tools.txt
```

Open the viewer:

```bash
.venv/bin/python tools/visualization/rerun/view.py \
  /path/to/berlin_01_extracted
```

Activation is optional. The launcher automatically finds the Rerun executable
installed beside `.venv/bin/python`, even when `.venv/bin` is not in `PATH`.

For large LiDAR scans, reduce the displayed points per scan:

```bash
.venv/bin/python tools/visualization/rerun/view.py \
  /path/to/berlin_01_extracted --max-points 100000
```

Save an Rerun recording instead of opening a window:

```bash
.venv/bin/python tools/visualization/rerun/view.py \
  /path/to/berlin_01_extracted --save berlin_01.rrd
```

If `trajectory.csv` is present in the extracted directory and contains `x`,
`y`, and `z` columns, it is added as a static 3D path. The viewer deliberately
does not invent transforms: point clouds remain in each sensor's recorded frame
until verified extrinsic transforms are available.
