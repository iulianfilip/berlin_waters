"""Tests for lossless compressed-camera extraction helpers."""

from __future__ import annotations

import csv
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from tools.extraction.extract import (
    detect_compressed_image_extension,
    export_compressed_image,
    format_unix_timestamp_ns,
    write_camera_index,
)


def compressed_message(
    data: bytes,
    source_format: str = "jpeg",
    seconds: int = 12,
    nanoseconds: int = 34,
) -> SimpleNamespace:
    """Create a small object shaped like ``sensor_msgs/CompressedImage``."""
    stamp = SimpleNamespace(sec=seconds, nanosec=nanoseconds)
    header = SimpleNamespace(stamp=stamp, frame_id="zed_left_camera")
    return SimpleNamespace(header=header, format=source_format, data=data)


class CompressedImageTests(unittest.TestCase):
    def test_detects_jpeg_and_png_magic(self) -> None:
        self.assertEqual(
            detect_compressed_image_extension("unknown", b"\xff\xd8\xffpayload"),
            "jpg",
        )
        self.assertEqual(
            detect_compressed_image_extension("jpeg", b"\x89PNG\r\n\x1a\npayload"),
            "png",
        )

    def test_format_field_is_used_when_magic_is_unavailable(self) -> None:
        self.assertEqual(
            detect_compressed_image_extension("bgr8; jpeg compressed bgr8", b"data"),
            "jpg",
        )

    def test_unknown_format_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported compressed image"):
            detect_compressed_image_extension("tiff", b"data")

    def test_payload_is_written_without_modification(self) -> None:
        payload = b"\xff\xd8\xff\x00unchanged-camera-bytes\xff\xd9"
        message = compressed_message(payload)
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_directory = Path(temporary_directory)

            entry = export_compressed_image(
                message,
                timestamp_ns=123_456_789,
                output_directory=output_directory,
                image_index=7,
            )

            self.assertEqual(entry.filename, "000007.jpg")
            self.assertEqual(entry.header_timestamp_ns, 12_000_000_034)
            self.assertEqual(entry.frame_id, "zed_left_camera")
            self.assertEqual(entry.byte_count, len(payload))
            self.assertEqual((output_directory / entry.filename).read_bytes(), payload)

    def test_image_numbers_are_zero_padded_and_unique(self) -> None:
        message = compressed_message(b"\xff\xd8\xffpayload")
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_directory = Path(temporary_directory)

            first = export_compressed_image(message, 42, output_directory, 0)
            second = export_compressed_image(message, 42, output_directory, 1)

            self.assertEqual(first.filename, "000000.jpg")
            self.assertEqual(second.filename, "000001.jpg")
            self.assertTrue((output_directory / first.filename).is_file())
            self.assertTrue((output_directory / second.filename).is_file())

    def test_unix_timestamp_has_exact_nanosecond_precision(self) -> None:
        self.assertEqual(
            format_unix_timestamp_ns(1_747_902_065_008_561_527),
            "1747902065.008561527",
        )

    def test_index_csv_contains_source_metadata(self) -> None:
        message = compressed_message(b"\xff\xd8\xffpayload")
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_directory = Path(temporary_directory)
            entry = export_compressed_image(message, 42, output_directory, 0)
            index_path = output_directory / "index.csv"

            write_camera_index([entry], index_path)

            raw_index = index_path.read_text(encoding="utf-8")
            self.assertIn('"source_format"', raw_index)
            self.assertIn('"jpeg"', raw_index)
            self.assertNotIn("\r\n", raw_index)
            with index_path.open(newline="", encoding="utf-8") as index_file:
                rows = list(csv.DictReader(index_file))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["filename"], "000000.jpg")
            self.assertEqual(rows[0]["timestamp_unix"], "0.000000042")
            self.assertEqual(rows[0]["header_timestamp_unix"], "12.000000034")
            self.assertEqual(rows[0]["source_format"], "jpeg")
            self.assertNotIn("timestamp_ns", rows[0])
            self.assertNotIn("header_timestamp_ns", rows[0])


if __name__ == "__main__":
    unittest.main()
