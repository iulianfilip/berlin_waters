# Berlin Waters tools

Run these scripts from the repository root. The [main README](../README.md#tools)
has the installation steps. For bag-based tools, pass a scene directory
containing `metadata.yaml` and one or more `.db3` files.

| Workflow | Script or asset | Guide | Requirements |
| --- | --- | --- | --- |
| Extract images, point clouds, IMU, and GNSS | `tools/extraction/extract.py` | [Extraction](extraction/README.md) | Python environment from `requirements-tools.txt` |
| View a bag directly | `tools/visualization/rerun/view_bag.py` | [Rerun](visualization/rerun/README.md) | Same Python environment; no ROS 2 |
| View extracted files | `tools/visualization/rerun/view.py` | [Rerun](visualization/rerun/README.md) | Same Python environment; no ROS 2 |
| View a bag in RViz | `tools/visualization/ros2/run_rviz.py` | [ROS 2/RViz](visualization/ros2/README.md) | ROS 2, RViz, and a sourced ROS environment |
| View a bag in Foxglove | `tools/visualization/foxglove/run_bridge.py` and the supplied layout | [Foxglove](visualization/foxglove/README.md) | ROS 2, `foxglove_bridge`, and Foxglove Desktop |
