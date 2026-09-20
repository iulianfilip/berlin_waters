# Data extraction

The extraction tool exports compressed camera images, binary PCD/PLY point
clouds, IMU tables, and GNSS tables. It also provides ROS 2 bag inspection,
topic selection, and a `manifest.json`.

Inspect every topic in a bag:

```bash
python3 tools/extraction/extract.py /path/to/berlin_06 /path/to/output
```

Select a documented sensor namespace or an exact topic:

```bash
python3 tools/extraction/extract.py /path/to/berlin_06 --sensor ouster
python3 tools/extraction/extract.py /path/to/berlin_06 \
  --topic /zed/zed_node/left/image_rect_color/compressed
```

`--sensor` and `--topic` may each be repeated. When both are used, their matches
are combined. Sensor names currently include `velodyne` (alias `vlp32`),
`ouster`, `livox`, `zed2i` (alias `zed`), `xsens`, `ublox`, and `sonar`.

If the output directory is omitted, it defaults to `./<bag-name>_extracted`.

## Camera output

`sensor_msgs/msg/CompressedImage` payloads are copied directly from the bag as
`.jpg` or `.png` files. They are not decoded or re-encoded. Each camera topic is
written to its own directory:

```text
images/<sanitized-topic>/
├── 000000.jpg
├── 000001.jpg
└── index.csv
```

Image numbers are sequential and padded to at least six digits. The CSV maps
each filename to its exact bag timestamp in Unix `seconds.nanoseconds` form. It
also records the message-header timestamp in the same form, frame ID, source
format, and byte count. Unknown compressed formats are skipped and reported
rather than written with a misleading extension.

## IMU and GNSS output

Selected `sensor_msgs/msg/Imu` and `sensor_msgs/msg/NavSatFix` topics are streamed
to one CSV file per topic under `tables/`, without retaining large topics in
memory.

IMU tables contain exact Unix bag and header timestamps, frame ID, quaternion,
angular velocity, and linear acceleration. GNSS tables contain exact Unix bag
and header timestamps, frame ID, latitude, longitude, and altitude. Integer
nanosecond columns are omitted; nanoseconds are preserved as nine fractional
digits in the Unix timestamps and in the manifest.

Velocity extraction is outside the current scope, so velocity topics are
reported as unsupported.

## Point-cloud output

Selected `sensor_msgs/msg/PointCloud2` topics preserve every supported PointField
and its original scalar datatype. XYZ is required; fields such as intensity,
ring, time, tag, line, reflectivity, ambient, and range are retained when present.
Points with non-finite XYZ or intensity values are omitted. Organized clouds,
row padding, field offsets, PointField datatypes, and endianness are handled
without requiring Open3D.

PCD retains multi-value fields with its `COUNT` metadata. Because PLY properties
are scalar, multi-value fields are expanded predictably as `field_0`, `field_1`,
and so on.

Choose binary PCD or binary little-endian PLY:

```bash
python3 tools/extraction/extract.py /path/to/berlin_06 /path/to/output \
  --sensor ouster --pointcloud-format pcd

python3 tools/extraction/extract.py /path/to/berlin_06 /path/to/output \
  --sensor ouster --pointcloud-format ply
```

PCD is the default. Files use six-digit scan numbers and each topic directory
contains an `index.csv` mapping filenames to exact Unix bag/header timestamps,
frame ID, point count, intensity availability, and exported field names. During
extraction the terminal shows the percentage and processed/total scan count.
Camera extraction reports the same percentage and processed/total progress.

The manifest records source counts and timestamps, selection filters, relative
output paths, and every known topic. Camera, point-cloud, IMU, and GNSS topics
report `completed`, `partial`, or `failed` with extracted and failed counts.
Selected unsupported types are `skipped`, and unselected types are `filtered`.

For ROS 2 bags that do not embed message definitions, the inspector uses the
ROS 2 Humble type definitions supplied by `rosbags`. This matches the dataset's
supported ROS 2 baseline and does not require a ROS 2 installation.

Unknown and custom message types are recorded with an explicit reason rather
than treated as a successful extraction.

Install the planned non-ROS dependencies from
[`requirements-tools.txt`](../../requirements-tools.txt).
