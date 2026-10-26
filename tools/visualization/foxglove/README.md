# Foxglove visualization

Foxglove displays *Berlin Waters* ROS 2 bag playback through a local
`foxglove_bridge`. The supplied [layout](berlin_waters.layout.json) includes
stereo cameras, LiDARs, IMU plots, and GNSS positions.

## Requirements

Install [Foxglove Desktop](https://foxglove.dev/download) and, for ROS 2
Humble on Ubuntu 22.04, the bridge:

```bash
sudo apt install ros-humble-foxglove-bridge
```

Run the commands below from the repository root with ROS 2's system Python.

## Start the bridge

In the first terminal:

```bash
source /opt/ros/humble/setup.bash
python3 tools/visualization/foxglove/run_bridge.py
```

In Foxglove Desktop, open a **Foxglove WebSocket** connection to
`ws://localhost:8765`. Import
[`berlin_waters.layout.json`](berlin_waters.layout.json) using **Layouts →
Import from file**. Connect before starting playback.

## Play the bag

In a second terminal:

```bash
source /opt/ros/humble/setup.bash
ros2 bag play /path/to/berlin_01 --clock
```

Pass the bag directory containing `metadata.yaml` and `.db3` files. Control
playback in the `ros2 bag play` terminal. GNSS map tiles require an internet
connection.

Run `python3 tools/visualization/foxglove/run_bridge.py --help` for bridge
options, including `--dry-run`, address, port, and backlog.
