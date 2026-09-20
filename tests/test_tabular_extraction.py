"""Tests for IMU and GNSS CSV conversion."""

from __future__ import annotations

from types import SimpleNamespace
import unittest

from tools.extraction.extract import tabular_csv_header, tabular_csv_row


def vector(x: float, y: float, z: float) -> SimpleNamespace:
    return SimpleNamespace(x=x, y=y, z=z)


def header() -> SimpleNamespace:
    stamp = SimpleNamespace(sec=12, nanosec=34)
    return SimpleNamespace(stamp=stamp, frame_id="sensor_frame")


class ImuCsvTests(unittest.TestCase):
    def test_imu_schema_contains_required_fields_without_ns_columns(self) -> None:
        columns = tabular_csv_header("sensor_msgs/msg/Imu")

        self.assertEqual(columns[:3], ("timestamp_unix", "header_timestamp_unix", "frame_id"))
        self.assertNotIn("timestamp_ns", columns)
        self.assertNotIn("header_timestamp_ns", columns)
        self.assertIn("orientation_w", columns)
        self.assertIn("angular_velocity_z", columns)
        self.assertIn("linear_acceleration_z", columns)

    def test_imu_row_preserves_values_and_timestamps(self) -> None:
        message = SimpleNamespace(
            header=header(),
            orientation=SimpleNamespace(x=0.1, y=0.2, z=0.3, w=0.4),
            angular_velocity=vector(1.0, 2.0, 3.0),
            linear_acceleration=vector(4.0, 5.0, 6.0),
        )

        row = tabular_csv_row("sensor_msgs/msg/Imu", message, 42)

        self.assertEqual(row[:3], ("0.000000042", "12.000000034", "sensor_frame"))
        self.assertEqual(
            row[3:],
            (0.1, 0.2, 0.3, 0.4, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0),
        )


class GnssCsvTests(unittest.TestCase):
    def test_gnss_schema_and_row(self) -> None:
        columns = tabular_csv_header("sensor_msgs/msg/NavSatFix")
        message = SimpleNamespace(
            header=header(),
            latitude=52.5,
            longitude=13.4,
            altitude=34.2,
        )

        row = tabular_csv_row("sensor_msgs/msg/NavSatFix", message, 1_500_000_001)

        self.assertEqual(
            columns,
            (
                "timestamp_unix",
                "header_timestamp_unix",
                "frame_id",
                "latitude",
                "longitude",
                "altitude",
            ),
        )
        self.assertEqual(
            row,
            (
                "1.500000001",
                "12.000000034",
                "sensor_frame",
                52.5,
                13.4,
                34.2,
            ),
        )

    def test_unrequested_tabular_type_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported tabular"):
            tabular_csv_header("geometry_msgs/msg/TwistWithCovarianceStamped")


if __name__ == "__main__":
    unittest.main()
