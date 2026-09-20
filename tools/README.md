# Berlin Waters tools

The public tools are being introduced in small, reviewable milestones. They are
split into two groups:

1. [Data extraction](extraction/README.md) for converting supported ROS 2 bag
   messages into common files.
2. [Visualization](visualization/README.md) for inspecting a bag with ROS 2 and
   RViz, or inspecting extracted files without ROS 2 using Rerun.

The extraction command can inspect a ROS 2 bag, select topics by sensor or exact
name, copy compressed camera images without re-encoding, and export binary
PCD/PLY point clouds plus IMU and GNSS CSV tables. The non-ROS Rerun viewer can
either stream a ROS 2 bag directly with a bounded rolling memory window or load
previously extracted images and point clouds on a shared timeline.
