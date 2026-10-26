# ROS 2 and RViz visualization

The supplied RViz configuration shows the stereo cameras, LiDARs, and sensor
frames while `ros2 bag play` controls playback. Run commands from the
repository root with ROS 2's system Python.

## Requirements

ROS 2 Humble on Ubuntu 22.04, with SQLite rosbag2, RViz, TF, compressed image
transport, and Python YAML support:

```bash
sudo apt install ros-humble-rosbag2 ros-humble-rviz2 ros-humble-tf2-ros \
  ros-humble-compressed-image-transport python3-yaml
```

## Start the viewer

In the first terminal:

```bash
cd /path/to/berlin_waters
source /opt/ros/humble/setup.bash
python3 tools/visualization/ros2/run_rviz.py
```

The launcher opens [`berlin_waters.rviz`](berlin_waters.rviz) and publishes the
sensor transforms in [`static_transforms.yaml`](static_transforms.yaml).

In a second terminal:

```bash
source /opt/ros/humble/setup.bash
ros2 bag play /path/to/berlin_01 --clock
```

Playback can be paused, restarted, or stopped independently of RViz. Pass the
bag directory containing `metadata.yaml` and `.db3` files.

## Configuration and calibration scope

The supplied transform translations use the sensor mounting measurements.
LiDAR and IMU rotations use the boat-axis convention; update
[`static_transforms.yaml`](static_transforms.yaml) when calibrated rotations
are available. If an Ouster bag uses a frame other than `os_sensor`, update
that frame in the transform file.

Run `python3 tools/visualization/ros2/run_rviz.py --help` for launcher
options, including `--dry-run` and `--no-transforms`.
