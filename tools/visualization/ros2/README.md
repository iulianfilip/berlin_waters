# ROS 2 and RViz visualization

This tool will launch ROS 2 bag playback and RViz together. It will support a
playback rate and an optional YAML file containing verified static transforms.
It will not embed guessed calibration values.

The command-line interface is currently scaffolded:

```bash
python3 tools/visualization/ros2/run_rviz.py --help
```

The launcher and `berlin_waters.rviz` configuration will be implemented in a
later milestone.
