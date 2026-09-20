# Visualization

Berlin Waters provides or plans three visualization paths:

- **ROS 2:** play a ROS 2 bag and inspect it directly in RViz. See the
  [ROS 2 and RViz guide](ros2/README.md).
- **Without ROS 2:** stream a bag directly into Rerun with bounded memory, or
  inspect extracted common files. See the [Rerun guide](rerun/README.md).
- **Foxglove:** open the ROS 2 bag directly with a supplied panel layout. The
  Berlin Waters layout will be added after the Rerun and RViz workflows.

Direct Rerun playback is implemented for stereo images and calibration, Xsens
IMU, GNSS on OpenStreetMap, and each standard PointCloud2 LiDAR. The RViz
configuration and Foxglove layout remain later milestones.
