"""Tests for PointCloud2 decoding and binary PCD/PLY output."""

from __future__ import annotations

from pathlib import Path
import csv
import struct
from types import SimpleNamespace
import tempfile
import unittest

import numpy as np

from tools.extraction.extract import (
    DecodedPointCloud,
    PointCloudIndexEntry,
    decode_pointcloud2,
    write_pcd,
    write_ply,
    write_pointcloud_index,
)


def field(
    name: str, offset: int, datatype: int, count: int = 1
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        offset=offset,
        datatype=datatype,
        count=count,
    )


def message(
    data: bytes,
    *,
    width: int,
    height: int,
    point_step: int,
    row_step: int,
    fields: list[SimpleNamespace],
    is_bigendian: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        data=data,
        width=width,
        height=height,
        point_step=point_step,
        row_step=row_step,
        fields=fields,
        is_bigendian=is_bigendian,
    )


class PointCloudDecodingTests(unittest.TestCase):
    def test_decodes_xyz_and_intensity_and_filters_nan(self) -> None:
        rawdata = b"".join(
            [
                struct.pack("<ffff", 1.0, 2.0, 3.0, 4.0),
                struct.pack("<ffff", float("nan"), 5.0, 6.0, 7.0),
                struct.pack("<ffff", 8.0, 9.0, 10.0, 11.0),
            ]
        )
        cloud_message = message(
            rawdata,
            width=3,
            height=1,
            point_step=16,
            row_step=48,
            fields=[
                field("x", 0, 7),
                field("y", 4, 7),
                field("z", 8, 7),
                field("intensity", 12, 7),
            ],
        )

        cloud = decode_pointcloud2(cloud_message)

        np.testing.assert_allclose(
            cloud.points,
            np.array([[1.0, 2.0, 3.0], [8.0, 9.0, 10.0]], dtype=np.float32),
        )
        np.testing.assert_allclose(
            cloud.intensity,
            np.array([4.0, 11.0], dtype=np.float32),
        )

    def test_handles_big_endian_integer_intensity_and_row_padding(self) -> None:
        first = struct.pack(">fffHxx", 1.0, 2.0, 3.0, 25) + b"PAD!"
        second = struct.pack(">fffHxx", 4.0, 5.0, 6.0, 50) + b"PAD!"
        cloud_message = message(
            first + second,
            width=1,
            height=2,
            point_step=16,
            row_step=20,
            fields=[
                field("x", 0, 7),
                field("y", 4, 7),
                field("z", 8, 7),
                field("intensity", 12, 4),
            ],
            is_bigendian=True,
        )

        cloud = decode_pointcloud2(cloud_message)

        np.testing.assert_allclose(
            cloud.points,
            np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], dtype=np.float32),
        )
        np.testing.assert_allclose(
            cloud.intensity,
            np.array([25.0, 50.0], dtype=np.float32),
        )

    def test_rejects_missing_xyz_field(self) -> None:
        cloud_message = message(
            struct.pack("<ff", 1.0, 2.0),
            width=1,
            height=1,
            point_step=8,
            row_step=8,
            fields=[field("x", 0, 7), field("y", 4, 7)],
        )

        with self.assertRaisesRegex(ValueError, "missing required fields: z"):
            decode_pointcloud2(cloud_message)

    def test_preserves_extra_fields_datatypes_and_counts(self) -> None:
        rawdata = struct.pack(
            "<ffffHf3f",
            1.0,
            2.0,
            3.0,
            4.0,
            5,
            6.0,
            7.0,
            8.0,
            9.0,
        )
        cloud_message = message(
            rawdata,
            width=1,
            height=1,
            point_step=34,
            row_step=34,
            fields=[
                field("x", 0, 7),
                field("y", 4, 7),
                field("z", 8, 7),
                field("intensity", 12, 7),
                field("ring", 16, 4),
                field("time", 18, 7),
                field("normal", 22, 7, count=3),
            ],
        )

        cloud = decode_pointcloud2(cloud_message)

        fields = {item.name: item for item in cloud.fields}
        self.assertEqual(tuple(fields), ("x", "y", "z", "intensity", "ring", "time", "normal"))
        self.assertEqual(fields["ring"].datatype, 4)
        self.assertEqual(fields["ring"].values[0, 0], 5)
        self.assertEqual(fields["normal"].count, 3)
        np.testing.assert_allclose(fields["normal"].values, [[7.0, 8.0, 9.0]])

        with tempfile.TemporaryDirectory() as temporary_directory:
            pcd_path = Path(temporary_directory) / "scan.pcd"
            ply_path = Path(temporary_directory) / "scan.ply"
            write_pcd(pcd_path, cloud)
            write_ply(ply_path, cloud)
            pcd_header = pcd_path.read_bytes().split(b"DATA binary\n", 1)[0]
            ply_header = ply_path.read_bytes().split(b"end_header\n", 1)[0]

        self.assertIn(b"FIELDS x y z intensity ring time normal", pcd_header)
        self.assertIn(b"SIZE 4 4 4 4 2 4 4", pcd_header)
        self.assertIn(b"TYPE F F F F U F F", pcd_header)
        self.assertIn(b"COUNT 1 1 1 1 1 1 3", pcd_header)
        self.assertIn(b"property ushort ring", ply_header)
        self.assertIn(b"property float normal_0", ply_header)
        self.assertIn(b"property float normal_2", ply_header)


class PointCloudWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cloud = DecodedPointCloud(
            points=np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], dtype=np.float32),
            intensity=np.array([7.0, 8.0], dtype=np.float32),
        )

    def test_writes_binary_pcd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "scan.pcd"

            write_pcd(path, self.cloud)

            header, payload = path.read_bytes().split(b"DATA binary\n", maxsplit=1)
            self.assertIn(b"FIELDS x y z intensity", header)
            self.assertIn(b"POINTS 2", header)
            values = np.frombuffer(payload, dtype="<f4").reshape(2, 4)
            np.testing.assert_allclose(values[:, :3], self.cloud.points)
            np.testing.assert_allclose(values[:, 3], self.cloud.intensity)

    def test_writes_binary_little_endian_ply(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "scan.ply"

            write_ply(path, self.cloud)

            header, payload = path.read_bytes().split(b"end_header\n", maxsplit=1)
            self.assertIn(b"format binary_little_endian 1.0", header)
            self.assertIn(b"element vertex 2", header)
            self.assertIn(b"property float intensity", header)
            values = np.frombuffer(payload, dtype="<f4").reshape(2, 4)
            np.testing.assert_allclose(values[:, :3], self.cloud.points)
            np.testing.assert_allclose(values[:, 3], self.cloud.intensity)

    def test_index_uses_one_filename_column(self) -> None:
        entry = PointCloudIndexEntry(
            timestamp_ns=1_000_000_002,
            header_timestamp_ns=1_000_000_001,
            frame_id="lidar_frame",
            filename="000000.ply",
            point_count=2,
            has_intensity=True,
            fields="x y z intensity ring",
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "index.csv"

            write_pointcloud_index([entry], path)

            with path.open(newline="", encoding="utf-8") as index_file:
                rows = list(csv.DictReader(index_file))
        self.assertEqual(rows[0]["filename"], "000000.ply")
        self.assertNotIn("pcd_filename", rows[0])
        self.assertNotIn("ply_filename", rows[0])


if __name__ == "__main__":
    unittest.main()
