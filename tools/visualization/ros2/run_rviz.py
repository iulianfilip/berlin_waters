#!/usr/bin/env python3
"""Command-line entry point for Berlin Waters RViz visualization."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence


def build_parser() -> argparse.ArgumentParser:
    """Build the RViz launcher command-line parser."""
    parser = argparse.ArgumentParser(
        description="Play a Berlin Waters ROS 2 bag and inspect it in RViz."
    )
    parser.add_argument("bag", type=Path, help="path to a ROS 2 bag directory")
    parser.add_argument(
        "--rate",
        type=float,
        default=1.0,
        help="bag playback rate (default: %(default)s)",
    )
    parser.add_argument(
        "--transforms",
        type=Path,
        help="YAML file containing verified static transforms",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse command-line arguments for the future RViz launcher."""
    parser = build_parser()
    parser.parse_args(argv)
    parser.error("RViz visualization will be implemented in a later milestone")


if __name__ == "__main__":
    raise SystemExit(main())
